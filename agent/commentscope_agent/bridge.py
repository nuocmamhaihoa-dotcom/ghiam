"""Cầu nối proxy chạy trên máy PC, chỉ nghe ở địa chỉ loopback.

Chromium không tự đăng nhập được vào proxy SOCKS5 có mật khẩu. Agent mở một proxy HTTP nhỏ trên 127.0.0.1
(mật khẩu ngẫu nhiên cho mỗi lần chạy) và chuyển mọi kết nối của Chromium qua proxy thật đã thuê từ VPS:
HTTP, HTTPS (TLS tới chính proxy) hoặc SOCKS5, có hoặc không có mật khẩu. Tên miền đích luôn được gửi
nguyên cho proxy phân giải, máy PC không tự tra DNS cho trang đích.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import re
import secrets
import ssl
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import quote, urlsplit

PROTOCOLS = ("http", "https", "socks5")
REALM = "CommentScope agent"
NOT_A_WEBSITE = "Đây là cầu nối proxy của CommentScope agent, không phải trang web"
MAX_REQUEST_HEAD = 64 * 1024
MAX_RESPONSE_HEAD = 256 * 1024
MAX_HEADERS = 200
CHUNK = 64 * 1024
HALF_CLOSE_GRACE_SEC = 30.0
REASONS = {
    400: "Bad Request",
    407: "Proxy Authentication Required",
    431: "Request Header Fields Too Large",
    501: "Not Implemented",
    502: "Bad Gateway",
    504: "Gateway Timeout",
}
SOCKS5_REPLIES = {
    1: "lỗi chung của proxy",
    2: "proxy không cho phép kết nối này",
    3: "không tới được mạng đích",
    4: "không tới được máy đích",
    5: "máy đích từ chối kết nối",
    6: "hết thời gian chờ",
    7: "proxy không hỗ trợ lệnh CONNECT",
    8: "proxy không hỗ trợ kiểu địa chỉ này",
}
_TOKEN_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HOSTNAME_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?\.?")
_REQUEST_DROP = frozenset(
    {"connection", "keep-alive", "proxy-authorization", "proxy-connection", "te", "upgrade", "expect"}
)
_RESPONSE_DROP = frozenset({"connection", "keep-alive", "proxy-connection"})

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]


class UpstreamError(Exception):
    """Không đi được qua proxy thật.

    proxy_fault=False khi proxy vẫn trả lời nhưng không tới được trang đích (proxy từ chối, trang đích sập...).
    """

    def __init__(self, message: str, *, proxy_fault: bool = True) -> None:
        super().__init__(message)
        self.proxy_fault = proxy_fault


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Upstream:
    """Proxy thật mà cầu nối chuyển tiếp tới."""

    protocol: str
    host: str
    port: int
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    label: str = ""

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ValueError(f"Agent chưa hỗ trợ proxy kiểu {self.protocol!r}")
        if not 1 <= self.port <= 65_535:
            raise ValueError(f"Cổng proxy không hợp lệ: {self.port}")
        if not self.label:
            object.__setattr__(self, "label", f"{self.protocol}://{_host_for_url(self.host)}:{self.port}")

    def basic_auth(self) -> str | None:
        if not self.username:
            return None
        raw = f"{self.username}:{self.password or ''}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class BridgeCredentials:
    username: str
    password: str = field(repr=False)

    @classmethod
    def generate(cls) -> BridgeCredentials:
        return cls("agent", secrets.token_urlsafe(24))


@dataclass(slots=True)
class BridgeStats:
    requests: int = 0
    auth_rejected: int = 0
    upstream_ok: int = 0
    upstream_failures: int = 0
    target_failures: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    last_error: str | None = None
    last_proxy_error: str | None = None


@dataclass(slots=True)
class _Head:
    start: str
    headers: list[tuple[str, str]]

    def get(self, name: str) -> str | None:
        values = self.get_all(name)
        return values[0] if values else None

    def get_all(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name]

    @property
    def status(self) -> int:
        parts = self.start.split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/") or not _is_digits(parts[1]) or len(parts[1]) != 3:
            raise _HttpError(502, f"Dòng trạng thái HTTP không hợp lệ: {self.start[:100]}")
        return int(parts[1])


class ProxyBridge:
    """Proxy HTTP trên loopback cho Chromium, chuyển tiếp qua proxy thật (HTTP/HTTPS/SOCKS5)."""

    def __init__(
        self,
        upstream: Upstream,
        *,
        credentials: BridgeCredentials | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        connect_timeout: float = 15.0,
        idle_timeout: float = 120.0,
        tls_context: ssl.SSLContext | None = None,
    ) -> None:
        if not _is_loopback_ip(host):
            raise ValueError("Cầu nối proxy chỉ được mở trên địa chỉ loopback (127.0.0.1 hoặc ::1)")
        self.upstream = upstream
        self.credentials = credentials or BridgeCredentials.generate()
        self.stats = BridgeStats()
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._idle_timeout = idle_timeout
        self._tls_context = tls_context
        self._expected_auth = f"{self.credentials.username}:{self.credentials.password}".encode()
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("Cầu nối proxy chưa chạy")
        return self._port

    @property
    def server_url(self) -> str:
        return f"http://{_host_for_url(self._host)}:{self.port}"

    @property
    def proxy_url(self) -> str:
        username = quote(self.credentials.username, safe="")
        password = quote(self.credentials.password, safe="")
        return f"http://{username}:{password}@{_host_for_url(self._host)}:{self.port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle, self._host, self._port, limit=MAX_REQUEST_HEAD)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 5)

    async def open_tunnel(self, host: str, port: int) -> Streams:
        """Mở kết nối TCP tới host:port đi qua proxy thật."""
        streams = await self._connect(host, port, tunnel=True)
        self.stats.upstream_ok += 1
        return streams

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            self.stats.last_error = f"Lỗi nội bộ của cầu nối proxy: {exc!r}"[:300]
        finally:
            if task is not None:
                self._tasks.discard(task)
            await _close(writer)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(_read_head(reader), self._idle_timeout)
            if head is None:
                return
            method, target = _request_line(head)
            if method == "CONNECT":
                host, port = _split_authority(target, default_port=None)
            elif target.startswith("/") or target == "*":
                raise _HttpError(400, NOT_A_WEBSITE)
            else:
                host, port = _absolute_target(target)
        except _HttpError as exc:
            await _send_error(writer, exc.status, str(exc))
            return
        if not self._authorized(head):
            self.stats.auth_rejected += 1
            await _send_error(
                writer,
                407,
                "Cần đăng nhập vào cầu nối proxy của CommentScope agent",
                extra=(("Proxy-Authenticate", f'Basic realm="{REALM}"'),),
            )
            return
        self.stats.requests += 1
        if method == "CONNECT":
            await self._tunnel(host, port, reader, writer)
        else:
            await self._forward(head, method, target, host, port, reader, writer)

    def _authorized(self, head: _Head) -> bool:
        scheme, _, token = (head.get("proxy-authorization") or "").partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(token.strip(), validate=True)
        except (binascii.Error, ValueError):
            return False
        return secrets.compare_digest(decoded, self._expected_auth)

    async def _tunnel(self, host: str, port: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await self.open_tunnel(host, port)
        except UpstreamError as exc:
            await _send_error(writer, 502, str(exc))
            return
        try:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._relay(reader, writer, up_reader, up_writer)
        finally:
            await _close(up_writer)

    async def _forward(
        self,
        head: _Head,
        method: str,
        target: str,
        host: str,
        port: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            length = _request_body_length(head)
        except _HttpError as exc:
            await _send_error(writer, exc.status, str(exc))
            return
        parts = urlsplit(target)
        authority = parts.netloc.rpartition("@")[2]
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        via_proxy = self.upstream.protocol != "socks5"
        try:
            if via_proxy:
                up_reader, up_writer = await self._connect(host, port, tunnel=False)
            else:
                up_reader, up_writer = await self.open_tunnel(host, port)
        except UpstreamError as exc:
            await _send_error(writer, 502, str(exc))
            return
        try:
            if (head.get("expect") or "").lower() == "100-continue":
                writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await writer.drain()
            extra: list[tuple[str, str]] = []
            if via_proxy and (auth := self.upstream.basic_auth()):
                extra.append(("Proxy-Authorization", auth))
            request_target = f"http://{authority}{path}" if via_proxy else path
            request_head = _request_bytes(head, method, request_target, authority, extra)
            up_writer.write(request_head)
            await up_writer.drain()
            self._count(len(request_head), upload=True)
            if length:
                await self._copy(reader, up_writer, length, upload=True)
            await self._relay_response(method, up_reader, writer, via_proxy=via_proxy)
        finally:
            await _close(up_writer)

    async def _relay_response(
        self, method: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, via_proxy: bool
    ) -> None:
        try:
            async with asyncio.timeout(self._idle_timeout):
                head = await _read_head(reader, response=True)
                while head is not None and 100 <= head.status < 200 and head.status != 101:
                    head = await _read_head(reader, response=True)
        except TimeoutError:
            await _send_error(writer, 504, f"Trang đích không trả lời qua proxy sau {self._idle_timeout:g} giây")
            return
        except _HttpError as exc:
            await _send_error(writer, 502, str(exc))
            return
        if head is None or (via_proxy and head.status == 407):
            if head is None:
                who = f"Proxy {self.upstream.label}" if via_proxy else "Trang đích"
                error = UpstreamError(f"{who} đóng kết nối mà không trả lời", proxy_fault=via_proxy)
            else:
                error = UpstreamError(f"Proxy {self.upstream.label} từ chối tên đăng nhập/mật khẩu (HTTP 407)")
            self._record_failure(error)
            await _send_error(writer, 502, str(error))
            return
        if via_proxy:
            self.stats.upstream_ok += 1
        response_head = _response_head_bytes(head)
        writer.write(response_head)
        await writer.drain()
        self._count(len(response_head), upload=False)
        await self._copy(reader, writer, _response_body_length(method, head), upload=False)

    async def _copy(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, length: int | None, *, upload: bool
    ) -> None:
        remaining = length
        while remaining is None or remaining > 0:
            size = CHUNK if remaining is None else min(CHUNK, remaining)
            data = await asyncio.wait_for(reader.read(size), self._idle_timeout)
            if not data:
                if upload and remaining:
                    raise ConnectionResetError("Chromium ngắt kết nối khi đang gửi dữ liệu")
                return
            writer.write(data)
            await writer.drain()
            self._count(len(data), upload=upload)
            if remaining is not None:
                remaining -= len(data)

    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
    ) -> None:
        loop = asyncio.get_running_loop()
        last_activity = loop.time()

        async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, upload: bool) -> bool:
            nonlocal last_activity
            while data := await reader.read(CHUNK):
                last_activity = loop.time()
                writer.write(data)
                await writer.drain()
                self._count(len(data), upload=upload)
            return _write_eof(writer)

        pending = {
            asyncio.create_task(pump(client_reader, up_writer, upload=True)),
            asyncio.create_task(pump(up_reader, client_writer, upload=False)),
        }
        half_closed_at: float | None = None
        try:
            while pending:
                deadline = last_activity + self._idle_timeout
                if half_closed_at is not None:
                    deadline = min(deadline, half_closed_at + HALF_CLOSE_GRACE_SEC)
                if loop.time() >= deadline:
                    return
                done, pending = await asyncio.wait(
                    pending, timeout=deadline - loop.time(), return_when=asyncio.FIRST_COMPLETED
                )
                # Kết nối TLS tới proxy HTTPS không đóng được một chiều: bên kia sẽ không bao giờ biết đã hết
                # dữ liệu, nên đóng hẳn đường hầm thay vì chờ.
                if any(task.cancelled() or task.exception() is not None or not task.result() for task in done):
                    return
                if done and half_closed_at is None:
                    half_closed_at = loop.time()
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _connect(self, host: str, port: int, *, tunnel: bool) -> Streams:
        upstream = self.upstream
        connected = False
        try:
            async with asyncio.timeout(self._connect_timeout):
                context = None
                if upstream.protocol == "https":
                    context = self._tls_context or ssl.create_default_context()
                reader, writer = await asyncio.open_connection(
                    upstream.host,
                    upstream.port,
                    ssl=context,
                    server_hostname=upstream.host if context else None,
                    limit=MAX_RESPONSE_HEAD,
                )
                connected = True
                if not tunnel:
                    return reader, writer
                try:
                    if upstream.protocol == "socks5":
                        await self._socks5_connect(reader, writer, host, port)
                    else:
                        await self._http_connect(reader, writer, host, port)
                except BaseException:
                    writer.close()
                    raise
                return reader, writer
        except UpstreamError as exc:
            failure = exc
        except (OSError, asyncio.IncompleteReadError) as exc:
            failure = self._describe(exc, connected=connected)
        self._record_failure(failure)
        raise failure

    def _describe(self, exc: BaseException, *, connected: bool) -> UpstreamError:
        label = self.upstream.label
        if isinstance(exc, TimeoutError):
            return UpstreamError(f"Proxy {label} không phản hồi sau {self._connect_timeout:g} giây")
        if isinstance(exc, ssl.SSLCertVerificationError):
            return UpstreamError(f"Chứng chỉ TLS của proxy {label} không hợp lệ: {exc.verify_message}")
        if isinstance(exc, ssl.SSLError):
            return UpstreamError(f"Lỗi TLS khi kết nối tới proxy {label}: {_short(exc)}")
        if isinstance(exc, asyncio.IncompleteReadError):
            return UpstreamError(f"Proxy {label} đóng kết nối giữa chừng")
        if connected:
            return UpstreamError(f"Mất kết nối với proxy {label}: {_short(exc)}")
        return UpstreamError(f"Không kết nối được tới proxy {label}: {_short(exc)}")

    def _record_failure(self, error: UpstreamError) -> None:
        self.stats.last_error = str(error)
        if error.proxy_fault:
            self.stats.upstream_failures += 1
            self.stats.last_proxy_error = str(error)
        else:
            self.stats.target_failures += 1

    def _count(self, size: int, *, upload: bool) -> None:
        if upload:
            self.stats.bytes_sent += size
        else:
            self.stats.bytes_received += size

    async def _http_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int
    ) -> None:
        authority = f"{_host_for_url(host)}:{port}"
        lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
        if auth := self.upstream.basic_auth():
            lines.append(f"Proxy-Authorization: {auth}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        label = self.upstream.label
        try:
            head = await _read_head(reader, response=True)
            status = head.status if head is not None else None
        except _HttpError:
            raise UpstreamError(f"{label} trả lời không đúng giao thức proxy HTTP") from None
        if status is None:
            raise UpstreamError(f"Proxy {label} đóng kết nối giữa chừng")
        if 200 <= status < 300:
            return
        if status == 407:
            raise UpstreamError(f"Proxy {label} từ chối tên đăng nhập/mật khẩu (HTTP 407)")
        raise UpstreamError(f"Proxy {label} không mở được kết nối tới {authority} (HTTP {status})", proxy_fault=False)

    async def _socks5_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int
    ) -> None:
        upstream = self.upstream
        label = upstream.label
        methods = b"\x00\x02" if upstream.username else b"\x00"
        writer.write(bytes([5, len(methods)]) + methods)
        await writer.drain()
        version, method = await reader.readexactly(2)
        if version != 5:
            raise UpstreamError(f"{label} không trả lời đúng giao thức SOCKS5")
        if method == 0xFF:
            hint = "" if upstream.username else " (proxy cần tên đăng nhập/mật khẩu)"
            raise UpstreamError(f"Proxy SOCKS5 {label} không chấp nhận cách đăng nhập nào{hint}")
        if method == 0x02 and upstream.username:
            username = upstream.username.encode()
            password = (upstream.password or "").encode()
            if len(username) > 255 or len(password) > 255:
                raise UpstreamError("Tên đăng nhập hoặc mật khẩu proxy SOCKS5 dài quá 255 byte")
            writer.write(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
            await writer.drain()
            _auth_version, status = await reader.readexactly(2)
            if status != 0:
                raise UpstreamError(f"Proxy SOCKS5 {label} từ chối tên đăng nhập/mật khẩu")
        elif method != 0x00:
            raise UpstreamError(f"Proxy SOCKS5 {label} đòi cách đăng nhập không hỗ trợ (mã {method:#04x})")
        writer.write(b"\x05\x01\x00" + _socks5_address(host) + port.to_bytes(2, "big"))
        await writer.drain()
        version, reply, _reserved, address_type = await reader.readexactly(4)
        if version != 5:
            raise UpstreamError(f"{label} không trả lời đúng giao thức SOCKS5")
        if reply != 0:
            reason = SOCKS5_REPLIES.get(reply, f"mã lỗi {reply}")
            raise UpstreamError(
                f"Proxy SOCKS5 {label} không kết nối được tới {_host_for_url(host)}:{port}: {reason}",
                proxy_fault=reply in (7, 8),
            )
        if address_type == 1:
            await reader.readexactly(4 + 2)
        elif address_type == 4:
            await reader.readexactly(16 + 2)
        elif address_type == 3:
            (length,) = await reader.readexactly(1)
            await reader.readexactly(length + 2)
        else:
            raise UpstreamError(f"{label} không trả lời đúng giao thức SOCKS5")


async def _read_head(reader: asyncio.StreamReader, *, response: bool = False) -> _Head | None:
    """Đọc dòng đầu và tiêu đề HTTP; None nếu bên kia đóng kết nối trước khi gửi gì.

    Yêu cầu hỏng từ Chromium thành lỗi 400/431, phản hồi hỏng từ proxy hoặc trang đích thành lỗi 502.
    """
    bad, too_large = (502, 502) if response else (400, 431)
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError as exc:
        if not exc.partial.strip():
            return None
        raise _HttpError(bad, "Dữ liệu HTTP bị cắt giữa chừng") from None
    except asyncio.LimitOverrunError:
        raise _HttpError(too_large, "Phần tiêu đề HTTP quá lớn") from None
    lines = raw.decode("latin-1").split("\r\n")[:-2]
    if len(lines) - 1 > MAX_HEADERS:
        raise _HttpError(too_large, "Quá nhiều tiêu đề HTTP")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator or not _TOKEN_RE.fullmatch(name):
            raise _HttpError(bad, "Tiêu đề HTTP không hợp lệ")
        headers.append((name, value.strip()))
    return _Head(start=lines[0], headers=headers)


def _request_line(head: _Head) -> tuple[str, str]:
    parts = head.start.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1.") or not _TOKEN_RE.fullmatch(parts[0]):
        raise _HttpError(400, "Yêu cầu HTTP không hợp lệ")
    return parts[0], parts[1]


def _split_authority(authority: str, *, default_port: int | None) -> tuple[str, int]:
    invalid = _HttpError(400, f"Địa chỉ đích không hợp lệ: {authority[:100]}")
    try:
        parts = urlsplit(f"//{authority}")
        port = parts.port or default_port
    except ValueError:
        raise invalid from None
    host = parts.hostname
    if not host or port is None or parts.username is not None or parts.path or parts.query or not _valid_host(host):
        raise invalid
    return host, port


def _absolute_target(target: str) -> tuple[str, int]:
    parts = urlsplit(target)
    if parts.scheme.lower() != "http":
        raise _HttpError(400, "Cầu nối proxy chỉ nhận địa chỉ http://... hoặc lệnh CONNECT")
    return _split_authority(parts.netloc.rpartition("@")[2], default_port=80)


def _request_body_length(head: _Head) -> int:
    if head.get("transfer-encoding") is not None:
        raise _HttpError(501, "Cầu nối proxy chưa hỗ trợ gửi dữ liệu dạng chunked (Transfer-Encoding)")
    values = {value.strip() for value in head.get_all("content-length")}
    if not values:
        return 0
    value = values.pop()
    if values or not _is_digits(value):
        raise _HttpError(400, "Content-Length không hợp lệ")
    return int(value)


def _response_body_length(method: str, head: _Head) -> int | None:
    """Số byte thân phản hồi; None nghĩa là đọc tới khi bên kia đóng kết nối."""
    if method == "HEAD" or head.status in (204, 304) or 100 <= head.status < 200:
        return 0
    if head.get("transfer-encoding") is not None:
        return None
    value = (head.get("content-length") or "").strip()
    return int(value) if _is_digits(value) else None


def _connection_tokens(head: _Head) -> set[str]:
    tokens: set[str] = set()
    for value in head.get_all("connection") + head.get_all("proxy-connection"):
        tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    return tokens


def _request_bytes(head: _Head, method: str, target: str, authority: str, extra: list[tuple[str, str]]) -> bytes:
    drop = _REQUEST_DROP | _connection_tokens(head)
    headers = [(name, value) for name, value in head.headers if name.lower() not in drop]
    if not any(name.lower() == "host" for name, _ in headers):
        headers.insert(0, ("Host", authority))
    headers.extend(extra)
    headers.append(("Connection", "close"))
    lines = [f"{method} {target} HTTP/1.1", *(f"{name}: {value}" for name, value in headers)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _response_head_bytes(head: _Head) -> bytes:
    headers = [(name, value) for name, value in head.headers if name.lower() not in _RESPONSE_DROP]
    headers.append(("Connection", "close"))
    lines = [head.start, *(f"{name}: {value}" for name, value in headers)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def _send_error(
    writer: asyncio.StreamWriter, status: int, message: str, *, extra: tuple[tuple[str, str], ...] = ()
) -> None:
    body = f"{message}\n".encode()
    lines = [
        f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}",
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(body)}",
        "Connection: close",
        *(f"{name}: {value}" for name, value in extra),
    ]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
    await writer.drain()


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), 5)


def _write_eof(writer: asyncio.StreamWriter) -> bool:
    """Báo bên kia đã hết dữ liệu nhưng vẫn nhận tiếp; False nếu kết nối không hỗ trợ (TLS) hoặc đã đóng."""
    try:
        if writer.is_closing() or not writer.can_write_eof():
            return False
        writer.write_eof()
    except (OSError, RuntimeError):
        return False
    return True


def _socks5_address(host: str) -> bytes:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        name = host.encode("ascii")
        if len(name) > 255:
            raise UpstreamError(f"Tên miền đích dài quá 255 ký tự: {host[:60]}…", proxy_fault=False) from None
        return b"\x03" + bytes([len(name)]) + name
    return (b"\x01" if address.version == 4 else b"\x04") + address.packed


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return len(host) <= 253 and _HOSTNAME_RE.fullmatch(host) is not None
    return True


def _is_loopback_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_digits(value: str) -> bool:
    return value.isascii() and value.isdigit()


def _host_for_url(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _short(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= limit else f"{text[:limit]}…"
