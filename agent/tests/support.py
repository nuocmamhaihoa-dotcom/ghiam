"""Máy chủ giả và tiện ích cho test agent: trang đích, proxy HTTP/HTTPS, proxy SOCKS5, API agent của VPS.

Tên miền trong LOCAL_NAMES chỉ các proxy giả mới phân giải được (về 127.0.0.1), nên trang nào mở được bằng tên
đó thì chắc chắn đã đi qua proxy.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import io
import ipaddress
import json
import re
import secrets
import shutil
import socket
import ssl
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
import pytest

from commentscope_agent.cli import execute_async
from commentscope_agent.client import ControlPlaneClient

LOCAL_NAMES = frozenset({"origin.test", "localhost"})
EXIT_IP = "203.0.113.7"
PAGE_TITLE = "Trang thử CommentScope"
BIG_BODY = bytes(range(256)) * 1200
CHUNKED_PARTS = ("Một ", "hai ", "ba")
EOF_BODY = "Hết dữ liệu thì đóng kết nối".encode()
USER = "khach"
PASSWORD = "p@ss:w0rd/é"
REASONS = {200: "OK", 403: "Forbidden", 404: "Not Found", 407: "Proxy Authentication Required", 502: "Bad Gateway"}

CA_CONFIG = """\
[req]
distinguished_name = dn
prompt = no
x509_extensions = ca_ext
[dn]
CN = CommentScope test CA
[ca_ext]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
"""
LEAF_CONFIG = """\
[req]
distinguished_name = dn
prompt = no
[dn]
CN = origin.test
[leaf_ext]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:origin.test, DNS:localhost, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
"""


@dataclass(frozen=True, slots=True)
class Certs:
    """CA thử và chứng chỉ cho origin.test / localhost / 127.0.0.1 do CA đó ký."""

    ca: Path
    cert: Path
    key: Path

    def server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        return context

    def client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.ca)


def make_certs(directory: Path) -> Certs:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("Cần lệnh openssl để tạo chứng chỉ TLS cho test")
    (directory / "ca.cnf").write_text(CA_CONFIG, encoding="utf-8")
    (directory / "leaf.cnf").write_text(LEAF_CONFIG, encoding="utf-8")
    ec_key = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    serial = hex(secrets.randbits(62) | 1)
    ca = ["req", "-x509", *ec_key, "-keyout", "ca.key", "-out", "ca.pem", "-days", "2", "-config", "ca.cnf"]
    csr = ["req", "-new", *ec_key, "-keyout", "leaf.key", "-out", "leaf.csr", "-config", "leaf.cnf"]
    sign = ["x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-set_serial", serial]
    extensions = ["-days", "2", "-extfile", "leaf.cnf", "-extensions", "leaf_ext", "-out", "leaf.pem"]
    for arguments in (ca, csr, [*sign, *extensions]):
        subprocess.run([openssl, *arguments], cwd=directory, check=True, capture_output=True)
    return Certs(ca=directory / "ca.pem", cert=directory / "leaf.pem", key=directory / "leaf.key")


def resolve(host: str) -> str | None:
    if host in LOCAL_NAMES:
        return "127.0.0.1"
    try:
        return host if ipaddress.ip_address(host).is_loopback else None
    except ValueError:
        return None


def basic(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes = b""
    size: int = 0

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


async def read_request(reader: asyncio.StreamReader) -> HttpRequest | None:
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        return None
    lines = raw.decode("latin-1").split("\r\n")[:-2]
    method, target, _version = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        key = name.strip().lower()
        headers[key] = f"{headers[key]}, {value.strip()}" if key in headers else value.strip()
    length = int(headers.get("content-length") or 0)
    body = await reader.readexactly(length) if length else b""
    return HttpRequest(method, target, headers, body, len(raw) + len(body))


def response_bytes(
    status: int,
    body: bytes = b"",
    *,
    content_type: str = "text/plain; charset=utf-8",
    headers: tuple[tuple[str, str], ...] = (),
) -> bytes:
    lines = [
        f"HTTP/1.1 {status} {REASONS.get(status, 'Status')}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        *(f"{name}: {value}" for name, value in headers),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65_536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            if not writer.is_closing() and writer.can_write_eof():
                writer.write_eof()


async def relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    try:
        await asyncio.gather(pipe(client_reader, up_writer), pipe(up_reader, client_writer))
    finally:
        up_writer.close()


class _Server:
    def __init__(self, *, tls: ssl.SSLContext | None = None) -> None:
        self.tls = tls
        self.port = 0
        self.connections = 0
        self.active = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.errors: list[BaseException] = []
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0, ssl=self.tls)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc_info: object) -> None:
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

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self.connections += 1
        self.active += 1
        self.idle.clear()
        try:
            await self.handle(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            self.errors.append(exc)
        finally:
            self.active -= 1
            if not self.active:
                self.idle.set()
            if task is not None:
                self._tasks.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 2)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raise NotImplementedError


class FakeOrigin(_Server):
    """Trang đích HTTP/1.1 (bật TLS nếu có `tls`), giữ kết nối cho nhiều request như máy chủ web thật.

    /json, /ip: IP ra dạng ipinfo.io · /text-ip: IP dạng chữ · /page: HTML có tiêu đề · /status/NNN: mã NNN
    /echo: trả lại request nhận được · /big, /chunked, /eof: thân phản hồi lớn, chunked, đọc tới khi đóng
    /slow: không bao giờ trả lời
    """

    def __init__(self, *, tls: ssl.SSLContext | None = None, ip: str = EXIT_IP) -> None:
        super().__init__(tls=tls)
        self.ip = ip
        self.requests: list[HttpRequest] = []
        self.requested = asyncio.Event()

    def url(self, path: str, *, host: str = "origin.test") -> str:
        scheme = "https" if self.tls is not None else "http"
        return f"{scheme}://{host}:{self.port}{path}"

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while (request := await read_request(reader)) is not None:
            self.requests.append(request)
            self.requested.set()
            keep_alive = await self._respond(request, writer)
            await writer.drain()
            if not keep_alive or (request.header("connection") or "").lower() == "close":
                return

    async def _respond(self, request: HttpRequest, writer: asyncio.StreamWriter) -> bool:
        parts = urlsplit(request.target)
        path = parts.path
        if path in ("/json", "/ip"):
            payload = json.dumps({"ip": self.ip, "country": "VN"}).encode()
            writer.write(response_bytes(200, payload, content_type="application/json"))
        elif path == "/text-ip":
            writer.write(response_bytes(200, f"{self.ip}\n".encode()))
        elif path == "/page":
            html = (
                f'<!doctype html><html><head><meta charset="utf-8"><title>{PAGE_TITLE}</title></head>'
                "<body><h1>Xin chào qua proxy</h1></body></html>"
            )
            writer.write(response_bytes(200, html.encode(), content_type="text/html; charset=utf-8"))
        elif match := re.fullmatch(r"/status/(\d{3})", path):
            status = int(match.group(1))
            writer.write(response_bytes(status, f"HTTP {status}".encode()))
        elif path == "/echo":
            echo = {
                "method": request.method,
                "target": request.target,
                "headers": request.headers,
                "body": request.body.decode(errors="replace"),
            }
            payload = json.dumps(echo, ensure_ascii=False).encode()
            writer.write(response_bytes(200, payload, content_type="application/json"))
        elif path == "/big":
            writer.write(response_bytes(200, BIG_BODY, content_type="application/octet-stream"))
        elif path == "/chunked":
            chunks = b"".join(
                f"{len(part.encode()):x}\r\n".encode() + part.encode() + b"\r\n" for part in CHUNKED_PARTS
            )
            head = "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nTransfer-Encoding: chunked\r\n\r\n"
            writer.write(head.encode() + chunks + b"0\r\n\r\n")
        elif path == "/eof":
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n" + EOF_BODY
            )
            return False
        elif path == "/slow":
            await asyncio.Event().wait()
        else:
            writer.write(response_bytes(404, "Không có trang này".encode()))
        return True


class FakeHttpProxy(_Server):
    """Proxy HTTP như squid (thành proxy HTTPS nếu có `tls`): CONNECT mở đường hầm thật, request http://... được
    chuyển tiếp. `connect_status` ép mã trả lời cho CONNECT; `hang_up` đọc request rồi cúp máy không trả lời."""

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        tls: ssl.SSLContext | None = None,
        connect_status: int | None = None,
        hang_up: bool = False,
    ) -> None:
        super().__init__(tls=tls)
        self.username = username
        self.password = password
        self.connect_status = connect_status
        self.hang_up = hang_up
        self.requests: list[HttpRequest] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await read_request(reader)
        if request is None:
            return
        self.requests.append(request)
        if self.hang_up:
            return
        if self.username is not None and not self._authorized(request):
            writer.write(response_bytes(407, b"auth", headers=(("Proxy-Authenticate", 'Basic realm="fake"'),)))
            await writer.drain()
            return
        if request.method == "CONNECT":
            await self._tunnel(request, reader, writer)
        else:
            await self._forward(request, writer)

    def _authorized(self, request: HttpRequest) -> bool:
        scheme, _, token = (request.header("proxy-authorization") or "").partition(" ")
        try:
            decoded = base64.b64decode(token, validate=True)
        except (binascii.Error, ValueError):
            return False
        return scheme.lower() == "basic" and decoded == f"{self.username}:{self.password}".encode()

    async def _tunnel(self, request: HttpRequest, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        host, _, port = request.target.rpartition(":")
        address = resolve(host.strip("[]"))
        if self.connect_status is not None or address is None:
            writer.write(response_bytes(self.connect_status or 502, b"refused"))
            await writer.drain()
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(address, int(port))
        except OSError:
            writer.write(response_bytes(502, b"connect failed"))
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await relay(reader, writer, up_reader, up_writer)

    async def _forward(self, request: HttpRequest, writer: asyncio.StreamWriter) -> None:
        parts = urlsplit(request.target)
        address = resolve(parts.hostname or "")
        if parts.scheme != "http" or address is None:
            writer.write(response_bytes(502, b"unknown host"))
            await writer.drain()
            return
        up_reader, up_writer = await asyncio.open_connection(address, parts.port or 80)
        try:
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            skip = {"proxy-authorization", "proxy-connection"}
            lines = [
                f"{request.method} {path} HTTP/1.1",
                *(f"{name}: {value}" for name, value in request.headers.items() if name not in skip),
            ]
            up_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + request.body)
            await up_writer.drain()
            await pipe(up_reader, writer)
        finally:
            up_writer.close()


@dataclass(frozen=True, slots=True)
class SocksRequest:
    command: int
    address_type: int
    host: str
    port: int
    username: str | None


class FakeSocks5Proxy(_Server):
    """Proxy SOCKS5 (RFC 1928, đăng nhập RFC 1929). `reply` ép mã lỗi; `bound_type` chọn kiểu địa chỉ trả về."""

    def __init__(
        self, *, username: str | None = None, password: str | None = None, reply: int = 0, bound_type: int = 1
    ) -> None:
        super().__init__()
        self.username = username
        self.password = password
        self.reply = reply
        self.bound_type = bound_type
        self.requests: list[SocksRequest] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        version, count = await reader.readexactly(2)
        methods = await reader.readexactly(count)
        wanted = 0x02 if self.username is not None else 0x00
        if version != 5 or wanted not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(bytes([5, wanted]))
        await writer.drain()
        username = None
        if wanted == 0x02:
            _auth_version, size = await reader.readexactly(2)
            username = (await reader.readexactly(size)).decode()
            (size,) = await reader.readexactly(1)
            password = (await reader.readexactly(size)).decode()
            accepted = username == self.username and password == self.password
            writer.write(b"\x01\x00" if accepted else b"\x01\x01")
            await writer.drain()
            if not accepted:
                return
        _version, command, _reserved, address_type = await reader.readexactly(4)
        if address_type == 1:
            host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
        elif address_type == 4:
            host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
        else:
            (size,) = await reader.readexactly(1)
            host = (await reader.readexactly(size)).decode()
        port = int.from_bytes(await reader.readexactly(2), "big")
        self.requests.append(SocksRequest(command, address_type, host, port, username))
        address = resolve(host)
        reply = self.reply or (7 if command != 1 else 4 if address is None else 0)
        upstream = None
        if reply == 0 and address is not None:
            try:
                upstream = await asyncio.open_connection(address, port)
            except OSError:
                reply = 5
        writer.write(bytes([5, reply, 0]) + self._bound_address())
        await writer.drain()
        if upstream is not None:
            await relay(reader, writer, *upstream)

    def _bound_address(self) -> bytes:
        if self.bound_type == 3:
            name = b"proxy.test"
            return b"\x03" + bytes([len(name)]) + name + b"\x04\x38"
        if self.bound_type == 4:
            return b"\x04" + bytes(16) + b"\x04\x38"
        return b"\x01" + bytes(4) + b"\x04\x38"


ERROR_DETAILS = {
    401: "Agent token không hợp lệ",
    404: "Không tìm thấy lượt thuê proxy",
    410: "Lượt thuê proxy đã kết thúc, hãy thuê proxy mới",
}


@dataclass
class FakeControlPlane:
    """API agent của VPS cho httpx.MockTransport, trả dữ liệu đúng khuôn của server thật.

    `fail_next`: mã lỗi trả cho các request kế tiếp (ví dụ 502 khi Caddy chưa tới được app).
    `clock_skew_sec`: đồng hồ VPS chạy nhanh (dương) hoặc chậm (âm) hơn máy PC.
    """

    token: str = "test-agent-token"
    check_urls: list[str] = field(default_factory=list)
    proxy: dict[str, Any] | None = None
    unavailable_times: int = 0
    retry_after_sec: int = 5
    unavailable_message: str = "Chưa có proxy rảnh trong pool default"
    agent_api_enabled: bool = True
    clock_skew_sec: float = 0.0
    fail_next: list[int] = field(default_factory=list)
    renew_statuses: list[int] = field(default_factory=list)
    release_status: int | None = None
    release_delay: float = 0.0
    released: bool = True
    rotation_scheduled: bool = False
    quarantined_until: datetime | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    pings: int = 0
    leases: list[dict[str, Any]] = field(default_factory=list)
    renewals: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    releases: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self, **kwargs: Any) -> ControlPlaneClient:
        kwargs.setdefault("retry_delays", ())
        return ControlPlaneClient("https://vps.test", self.token, transport=self.transport(), **kwargs)

    def use_proxy(
        self,
        protocol: str,
        port: int,
        *,
        host: str = "127.0.0.1",
        username: str | None = None,
        password: str | None = None,
        kind: str = "static",
        pool: str = "default",
        proxy_id: int = 7,
    ) -> None:
        auth = f"{username}:{password}@" if username else ""
        self.proxy = {
            "id": proxy_id,
            "kind": kind,
            "protocol": protocol,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "url": f"{protocol}://{auth}{host}:{port}",
            "pool": pool,
            "exit_ip": EXIT_IP,
            "country": "VN",
            "playwright": {"server": f"{protocol}://{host}:{port}", "username": username, "password": password},
        }

    @property
    def last_release(self) -> dict[str, Any]:
        assert self.releases, "agent chưa trả proxy"
        return self.releases[-1][1]

    def _time(self, offset_sec: float = 0.0) -> str:
        return (datetime.now(UTC) + timedelta(seconds=self.clock_skew_sec + offset_sec)).isoformat()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        if not self.agent_api_enabled:
            detail = "Server chưa cấu hình AGENT_TOKENS nên chưa nhận kết nối từ agent"
            return httpx.Response(503, json={"detail": detail})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"detail": ERROR_DETAILS[401]})
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        path = request.url.path
        if request.method == "GET" and path == "/api/agent/ping":
            self.pings += 1
            ping = {
                "ok": True,
                "agent": "agent-token-1",
                "server_time": self._time(),
                "version": "0.1.0",
                "check_urls": self.check_urls,
            }
            return httpx.Response(200, json=ping)
        if request.method == "POST" and path == "/api/agent/proxies/lease":
            return self._lease(body)
        match = re.fullmatch(r"/api/agent/leases/([^/]+)/(renew|release)", path)
        if request.method == "POST" and match is not None:
            lease_id, action = match.groups()
            if action == "renew":
                self.renewals.append((lease_id, body))
                if self.renew_statuses:
                    status = self.renew_statuses.pop(0)
                    return httpx.Response(status, json={"detail": ERROR_DETAILS.get(status, "Lỗi thử")})
                return httpx.Response(
                    200, json={"lease_id": lease_id, "expires_at": self._time(body.get("ttl_sec") or 600)}
                )
            self.releases.append((lease_id, body))
            if self.release_delay:
                await asyncio.sleep(self.release_delay)
            if self.release_status is not None:
                return httpx.Response(
                    self.release_status, json={"detail": ERROR_DETAILS.get(self.release_status, "Lỗi thử")}
                )
            result = {
                "released": self.released,
                "rotation_scheduled": self.rotation_scheduled,
                "quarantined_until": self.quarantined_until.isoformat() if self.quarantined_until else None,
            }
            return httpx.Response(200, json=result)
        return httpx.Response(404, json={"detail": "Not Found"})

    def _lease(self, body: dict[str, Any]) -> httpx.Response:
        self.leases.append(body)
        if self.proxy is None or self.unavailable_times > 0:
            self.unavailable_times -= 1
            no_proxy = {
                "lease_id": None,
                "expires_at": None,
                "proxy": None,
                "retry_after_sec": self.retry_after_sec,
                "message": self.unavailable_message,
            }
            return httpx.Response(200, json=no_proxy)
        lease = {
            "lease_id": f"lease-{len(self.leases)}",
            "expires_at": self._time(body.get("ttl_sec") or 600),
            "proxy": self.proxy,
            "retry_after_sec": None,
            "message": None,
        }
        return httpx.Response(200, json=lease)


@dataclass(frozen=True, slots=True)
class CliRun:
    code: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.stdout)
        return data


def agent_env(fake: FakeControlPlane, **extra: str) -> dict[str, str]:
    return {
        "COMMENTSCOPE_SERVER_URL": "https://vps.test",
        "COMMENTSCOPE_AGENT_TOKEN": fake.token,
        "COMMENTSCOPE_WORKER_ID": "pc-test",
        **extra,
    }


async def run_cli(fake: FakeControlPlane, *argv: str, env: Mapping[str, str] | None = None) -> CliRun:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = await execute_async(
        list(argv),
        env=agent_env(fake) if env is None else env,
        transport=fake.transport(),
        stdout=stdout,
        stderr=stderr,
    )
    return CliRun(code, stdout.getvalue(), stderr.getvalue())
