"""Mạng giả lập cho test: proxy HTTP/SOCKS5 giả, cổng xoay theo session và API đổi IP kiểu nhà cung cấp 4G.

Proxy giả trả lời thẳng mọi request đi qua nó bằng JSON giống trang kiểm tra IP,
nên test không cần Internet và biết chính xác IP ra của từng proxy.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

HOST = "127.0.0.1"
COUNTRY = "VN"
ISP = "AS7552 Viettel Group"
CHECK_URL = "http://ip-check.sim/json"

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        port: int = sock.getsockname()[1]
        return port


def session_ip(session: str) -> str:
    digest = hashlib.sha256(session.encode()).digest()
    return f"198.18.{digest[0]}.{digest[1] or 1}"


@dataclass
class SimProxy:
    protocol: str
    ip: str
    username: str | None = None
    password: str | None = None
    session_prefix: str | None = None
    port: int = 0
    hits: int = 0
    rotations: int = 0
    last_username: str | None = None

    def exit_ip(self, username: str | None) -> str:
        if self.session_prefix and username and username.startswith(self.session_prefix):
            return session_ip(username.removeprefix(self.session_prefix))
        return self.ip

    @property
    def requires_auth(self) -> bool:
        return self.username is not None or self.session_prefix is not None

    def accepts(self, username: str | None, password: str | None) -> bool:
        if self.session_prefix is not None:
            return bool(username and username.startswith(self.session_prefix)) and password == self.password
        if self.username is None:
            return True
        return username == self.username and password == self.password

    @property
    def colon_line(self) -> str:
        if self.session_prefix is not None:
            return f"{HOST}:{self.port}:{self.session_prefix}{{session}}:{self.password}"
        if self.username is None:
            return f"{HOST}:{self.port}"
        return f"{HOST}:{self.port}:{self.username}:{self.password}"


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    request_line = (await reader.readline()).decode("latin-1").strip()
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    return request_line, headers


def _response(status: int, body: bytes = b"", *, extra_headers: tuple[str, ...] = ()) -> bytes:
    head = [
        f"HTTP/1.1 {status} {'OK' if status < 400 else 'Error'}",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
        "Connection: close",
        *extra_headers,
    ]
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


def _ip_response(ip: str) -> bytes:
    return _response(200, json.dumps({"ip": ip, "country": COUNTRY, "org": ISP}).encode())


def _parse_basic(value: str | None) -> tuple[str | None, str | None]:
    if not value or not value.lower().startswith("basic "):
        return None, None
    username, _, password = base64.b64decode(value[6:].strip()).decode().partition(":")
    return username, password


class NetSim:
    def __init__(self) -> None:
        self.api_key = "SIMKEY-0123456789abcdef"
        self.api_port = 0
        self.proxies: list[SimProxy] = []
        self.by_port: dict[int, SimProxy] = {}
        self.api_calls: list[tuple[str, str, dict[str, str]]] = []
        self._servers: list[asyncio.Server] = []
        self._writers: set[asyncio.StreamWriter] = set()
        self._ip_counter = 0

    async def start(self) -> None:
        self.api_port = await self._listen(self._serve_api)

    async def stop(self) -> None:
        for server in self._servers:
            server.close()
        for writer in list(self._writers):
            writer.close()
        for server in self._servers:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(server.wait_closed(), 5)

    def next_ip(self) -> str:
        self._ip_counter += 1
        return f"203.0.113.{self._ip_counter}"

    def api_url(self, path: str, **params: str | int) -> str:
        query = f"?{urlencode(params)}" if params else ""
        return f"http://{HOST}:{self.api_port}{path}{query}"

    def change_url(self, proxy: SimProxy) -> str:
        return self.api_url("/change", port=proxy.port, key=self.api_key)

    async def add_proxy(
        self,
        protocol: str = "http",
        *,
        username: str | None = None,
        password: str | None = None,
        session_prefix: str | None = None,
    ) -> SimProxy:
        proxy = SimProxy(
            protocol=protocol,
            ip=f"198.51.100.{len(self.proxies) + 1}",
            username=username,
            password=password,
            session_prefix=session_prefix,
        )
        serve = self._serve_socks5 if protocol == "socks5" else self._serve_http_proxy

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await serve(proxy, reader, writer)

        proxy.port = await self._listen(handler)
        self.proxies.append(proxy)
        self.by_port[proxy.port] = proxy
        return proxy

    async def _listen(self, handler: Handler) -> int:
        async def tracked(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self._writers.add(writer)
            try:
                await handler(reader, writer)
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                self._writers.discard(writer)
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        server = await asyncio.start_server(tracked, HOST, 0)
        self._servers.append(server)
        port: int = server.sockets[0].getsockname()[1]
        return port

    async def _serve_http_proxy(
        self, proxy: SimProxy, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_line, headers = await _read_head(reader)
        if not request_line:
            return
        username, password = _parse_basic(headers.get("proxy-authorization"))
        if not proxy.accepts(username, password):
            writer.write(_response(407, extra_headers=('Proxy-Authenticate: Basic realm="sim"',)))
        else:
            proxy.hits += 1
            proxy.last_username = username
            writer.write(_ip_response(proxy.exit_ip(username)))
        await writer.drain()

    async def _serve_socks5(self, proxy: SimProxy, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _version, count = await reader.readexactly(2)
        methods = await reader.readexactly(count)
        method = 0x02 if proxy.requires_auth else 0x00
        if method not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return
        writer.write(bytes([5, method]))
        username: str | None = None
        if method == 0x02:
            _auth_version, username_length = await reader.readexactly(2)
            username = (await reader.readexactly(username_length)).decode()
            (password_length,) = await reader.readexactly(1)
            password = (await reader.readexactly(password_length)).decode()
            if not proxy.accepts(username, password):
                writer.write(b"\x01\x01")
                await writer.drain()
                return
            writer.write(b"\x01\x00")
        _version, _command, _reserved, address_type = await reader.readexactly(4)
        if address_type == 1:
            await reader.readexactly(4)
        elif address_type == 3:
            (length,) = await reader.readexactly(1)
            await reader.readexactly(length)
        else:
            await reader.readexactly(16)
        await reader.readexactly(2)
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        await _read_head(reader)
        proxy.hits += 1
        proxy.last_username = username
        writer.write(_ip_response(proxy.exit_ip(username)))
        await writer.drain()

    async def _serve_api(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line, headers = await _read_head(reader)
        if not request_line:
            return
        length = int(headers.get("content-length") or 0)
        if length:
            await reader.readexactly(length)
        method, target, _ = request_line.split(" ", 2)
        parts = urlsplit(target)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        self.api_calls.append((method, parts.path, query))
        status, payload = await self._api_route(parts.path, query)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        writer.write(_response(status, body))
        await writer.drain()

    async def _api_route(self, path: str, query: dict[str, str]) -> tuple[int, Any]:
        port = query.get("port", "")
        proxy = self.by_port.get(int(port)) if port.isdigit() else None
        if path == "/change":
            if query.get("key") != self.api_key:
                return 200, {"status": "error", "message": "Key không hợp lệ"}
            if proxy is None:
                return 404, {"status": "error", "message": "Không tìm thấy cổng"}
            proxy.ip = self.next_ip()
            proxy.rotations += 1
            return 200, {"status": "success", "message": "Đổi IP thành công"}
        if path == "/sticky":
            return 200, {"status": "success", "message": "Đã nhận yêu cầu"}
        if path == "/broken":
            return 500, b"Internal Server Error"
        if path == "/move" and proxy is not None:
            moved = await self.add_proxy(proxy.protocol, username=proxy.username, password=proxy.password)
            return 200, {"success": True, "data": {"proxyhttp": moved.colon_line}}
        return 404, {"message": "not found"}
