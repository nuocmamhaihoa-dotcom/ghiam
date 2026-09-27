"""Cầu nối proxy: httpx/Chromium → cầu nối trên 127.0.0.1 → proxy thật (HTTP, HTTPS, SOCKS5) → trang đích."""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from commentscope_agent.bridge import NOT_A_WEBSITE, BridgeCredentials, ProxyBridge, Upstream
from tests.support import (
    BIG_BODY,
    CHUNKED_PARTS,
    EOF_BODY,
    EXIT_IP,
    PASSWORD,
    USER,
    Certs,
    FakeHttpProxy,
    FakeOrigin,
    FakeSocks5Proxy,
    basic,
    unused_port,
)


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode()


def parse_response(raw: bytes) -> RawResponse:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {name.strip().lower(): value.strip() for name, _, value in (line.partition(":") for line in lines[1:])}
    return RawResponse(int(lines[0].split(" ")[1]), headers, body)


async def exchange(bridge: ProxyBridge, request: bytes) -> RawResponse:
    """Gửi nguyên văn một request tới cầu nối và đọc phản hồi tới khi cầu nối đóng kết nối."""
    reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
    try:
        writer.write(request)
        await writer.drain()
        async with asyncio.timeout(10):
            raw = await reader.read()
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    return parse_response(raw)


def bridge_auth(bridge: ProxyBridge) -> str:
    return basic(bridge.credentials.username, bridge.credentials.password)


def request_bytes(start: str, *headers: str | None, body: bytes = b"") -> bytes:
    lines = [start, *(header for header in headers if header is not None)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def connect(authority: str, auth: str | None) -> bytes:
    return request_bytes(
        f"CONNECT {authority} HTTP/1.1",
        f"Host: {authority}",
        f"Proxy-Authorization: {auth}" if auth else None,
    )


def get(url: str, auth: str | None) -> bytes:
    return request_bytes(f"GET {url} HTTP/1.1", "Host: origin.test", f"Proxy-Authorization: {auth}" if auth else None)


def via(bridge: ProxyBridge, verify: ssl.SSLContext | bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(proxy=bridge.proxy_url, verify=verify, trust_env=False, timeout=10)


def bridge_to(upstream: Upstream, **kwargs: Any) -> ProxyBridge:
    kwargs.setdefault("connect_timeout", 5)
    kwargs.setdefault("idle_timeout", 5)
    return ProxyBridge(upstream, **kwargs)


@pytest.fixture(params=["http", "socks5"])
async def upstream(request: pytest.FixtureRequest) -> AsyncIterator[Upstream]:
    fake = FakeHttpProxy() if request.param == "http" else FakeSocks5Proxy()
    async with fake:
        yield Upstream(request.param, "127.0.0.1", fake.port)


async def test_https_page_through_http_proxy_with_password(certs: Certs) -> None:
    async with (
        FakeOrigin(tls=certs.server_context()) as origin,
        FakeHttpProxy(username=USER, password=PASSWORD) as proxy,
        bridge_to(Upstream("http", "127.0.0.1", proxy.port, USER, PASSWORD)) as bridge,
        via(bridge, certs.client_context()) as client,
    ):
        echo = await client.get(origin.url("/echo?lan=1"))
        exit_ip = await client.get(origin.url("/json"))
    assert echo.status_code == 200
    assert echo.json()["target"] == "/echo?lan=1"
    assert exit_ip.json() == {"ip": EXIT_IP, "country": "VN"}
    assert {request.method for request in proxy.requests} == {"CONNECT"}
    assert proxy.requests[0].target == f"origin.test:{origin.port}"
    assert proxy.requests[0].header("proxy-authorization") == basic(USER, PASSWORD)
    assert bridge.stats.upstream_ok >= 1
    assert bridge.stats.upstream_failures == bridge.stats.target_failures == 0
    assert bridge.stats.bytes_sent > 0
    assert bridge.stats.bytes_received > 0


async def test_plain_http_is_forwarded_with_proxy_password_and_without_hop_by_hop_headers() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy(username=USER, password=PASSWORD) as proxy,
        bridge_to(Upstream("http", "127.0.0.1", proxy.port, USER, PASSWORD)) as bridge,
    ):
        url = origin.url("/echo?q=b%C3%ACnh-lu%E1%BA%ADn")
        response = await exchange(
            bridge,
            request_bytes(
                f"GET {url} HTTP/1.1",
                f"Host: origin.test:{origin.port}",
                f"Proxy-Authorization: {bridge_auth(bridge)}",
                "Proxy-Connection: keep-alive",
                "Connection: keep-alive, X-Chi-Cho-Cau-Noi",
                "X-Chi-Cho-Cau-Noi: 1",
                "X-Giu-Lai: co",
            ),
        )
    assert response.status == 200
    assert response.headers["connection"] == "close"
    forwarded, received = proxy.requests[0], origin.requests[0]
    assert forwarded.target == url
    assert forwarded.header("proxy-authorization") == basic(USER, PASSWORD)
    assert received.target == "/echo?q=b%C3%ACnh-lu%E1%BA%ADn"
    assert received.header("x-giu-lai") == "co"
    assert received.header("connection") == "close"
    assert received.header("x-chi-cho-cau-noi") is None
    assert received.header("proxy-connection") is None
    bridge_secrets = (bridge.credentials.password, bridge_auth(bridge).split()[1])
    for seen in (forwarded, received):
        assert not any(secret in value for secret in bridge_secrets for value in seen.headers.values())


@pytest.mark.parametrize("bound_type", [1, 3, 4])
async def test_socks5_proxy_resolves_the_site_name_itself(certs: Certs, bound_type: int) -> None:
    async with (
        FakeOrigin(tls=certs.server_context()) as origin,
        FakeSocks5Proxy(username=USER, password=PASSWORD, bound_type=bound_type) as proxy,
        bridge_to(Upstream("socks5", "127.0.0.1", proxy.port, USER, PASSWORD)) as bridge,
        via(bridge, certs.client_context()) as client,
    ):
        response = await client.get(origin.url("/json"))
    assert response.json() == {"ip": EXIT_IP, "country": "VN"}
    request = proxy.requests[0]
    assert (request.address_type, request.host, request.port, request.username) == (3, "origin.test", origin.port, USER)


async def test_plain_http_over_socks5_sends_origin_form_requests() -> None:
    async with (
        FakeOrigin() as origin,
        FakeSocks5Proxy() as proxy,
        bridge_to(Upstream("socks5", "127.0.0.1", proxy.port)) as bridge,
        via(bridge) as client,
    ):
        echo = await client.post(origin.url("/echo"), json={"permalink": "https://example.com/p/1"})
        by_ip = await client.get(f"http://127.0.0.1:{origin.port}/json")
    assert echo.json()["target"] == "/echo"
    assert json.loads(echo.json()["body"]) == {"permalink": "https://example.com/p/1"}
    assert by_ip.json()["ip"] == EXIT_IP
    assert [(request.address_type, request.host, request.username) for request in proxy.requests] == [
        (3, "origin.test", None),
        (1, "127.0.0.1", None),
    ]


async def test_https_proxy_is_reached_over_tls(certs: Certs) -> None:
    async with (
        FakeOrigin() as plain,
        FakeOrigin(tls=certs.server_context()) as secure,
        FakeHttpProxy(tls=certs.server_context(), username=USER, password=PASSWORD) as proxy,
        bridge_to(
            Upstream("https", "127.0.0.1", proxy.port, USER, PASSWORD), tls_context=certs.client_context()
        ) as bridge,
        via(bridge, certs.client_context()) as client,
    ):
        over_http = await client.get(plain.url("/json"))
        over_connect = await client.get(secure.url("/json"))
    assert over_http.json()["ip"] == over_connect.json()["ip"] == EXIT_IP
    assert [request.method for request in proxy.requests] == ["GET", "CONNECT"]


async def test_untrusted_https_proxy_certificate_is_a_proxy_failure(certs: Certs) -> None:
    async with (
        FakeHttpProxy(tls=certs.server_context()) as proxy,
        bridge_to(Upstream("https", "127.0.0.1", proxy.port, label="#9 proxy HTTPS")) as bridge,
    ):
        response = await exchange(bridge, connect("origin.test:443", bridge_auth(bridge)))
    assert response.status == 502
    assert "Chứng chỉ TLS của proxy #9 proxy HTTPS không hợp lệ" in response.text
    assert bridge.stats.upstream_failures == 1
    assert bridge.stats.last_proxy_error is not None
    assert "Chứng chỉ TLS" in bridge.stats.last_proxy_error
    assert proxy.requests == []


@pytest.mark.parametrize("auth", [None, basic("agent", "sai-mat-khau"), "Bearer abc", "Basic %%%"])
async def test_bridge_asks_for_its_own_password_before_touching_the_proxy(auth: str | None) -> None:
    async with FakeHttpProxy() as proxy, bridge_to(Upstream("http", "127.0.0.1", proxy.port)) as bridge:
        tunnel = await exchange(bridge, connect("origin.test:443", auth))
        forward = await exchange(bridge, get("http://origin.test/json", auth))
    assert tunnel.status == forward.status == 407
    assert tunnel.headers["proxy-authenticate"] == 'Basic realm="CommentScope agent"'
    assert proxy.connections == 0
    assert bridge.stats.auth_rejected == 2
    assert bridge.stats.requests == 0


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "localhost", "::"])
def test_bridge_only_listens_on_loopback(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        ProxyBridge(Upstream("http", "127.0.0.1", 3128), host=host)


def test_ipv6_loopback_is_allowed() -> None:
    ProxyBridge(Upstream("http", "127.0.0.1", 3128), host="::1")


async def test_opening_the_bridge_in_a_browser_tab_explains_what_it_is() -> None:
    async with bridge_to(Upstream("http", "127.0.0.1", unused_port())) as bridge:
        page = await exchange(bridge, b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        garbage = await exchange(bridge, b"hello\r\n\r\n")
    assert page.status == 400
    assert NOT_A_WEBSITE in page.text
    assert page.headers["content-type"] == "text/plain; charset=utf-8"
    assert garbage.status == 400


@pytest.mark.parametrize("authority", ["origin.test", "user@origin.test:443", "origin.test:99999", "origin.test:443/x"])
async def test_invalid_connect_targets_are_rejected(authority: str) -> None:
    async with FakeHttpProxy() as proxy, bridge_to(Upstream("http", "127.0.0.1", proxy.port)) as bridge:
        response = await exchange(bridge, connect(authority, bridge_auth(bridge)))
    assert response.status == 400
    assert proxy.connections == 0


async def test_dead_proxy_is_a_proxy_failure() -> None:
    async with bridge_to(Upstream("http", "127.0.0.1", unused_port(), label="#7 proxy chết")) as bridge:
        tunnel = await exchange(bridge, connect("origin.test:443", bridge_auth(bridge)))
        forward = await exchange(bridge, get("http://origin.test/json", bridge_auth(bridge)))
    assert tunnel.status == forward.status == 502
    assert "Không kết nối được tới proxy #7 proxy chết" in tunnel.text
    assert "Không kết nối được tới proxy #7 proxy chết" in forward.text
    assert bridge.stats.upstream_failures == 2
    assert bridge.stats.target_failures == 0
    assert bridge.stats.last_proxy_error is not None
    assert "Không kết nối được tới proxy" in bridge.stats.last_proxy_error


async def test_wrong_proxy_password_is_a_proxy_failure() -> None:
    async with (
        FakeHttpProxy(username=USER, password=PASSWORD) as proxy,
        bridge_to(Upstream("http", "127.0.0.1", proxy.port, USER, "sai")) as bridge,
    ):
        tunnel = await exchange(bridge, connect("origin.test:443", bridge_auth(bridge)))
        forward = await exchange(bridge, get("http://origin.test/json", bridge_auth(bridge)))
    assert tunnel.status == forward.status == 502
    assert "từ chối tên đăng nhập/mật khẩu (HTTP 407)" in tunnel.text
    assert "từ chối tên đăng nhập/mật khẩu (HTTP 407)" in forward.text
    assert bridge.stats.upstream_failures == 2


async def test_proxy_refusing_the_site_is_not_blamed_on_the_proxy() -> None:
    async with (
        FakeHttpProxy(connect_status=403) as proxy,
        bridge_to(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
    ):
        response = await exchange(bridge, connect("blocked.test:443", bridge_auth(bridge)))
    assert response.status == 502
    assert "không mở được kết nối tới blocked.test:443 (HTTP 403)" in response.text
    assert bridge.stats.target_failures == 1
    assert bridge.stats.upstream_failures == 0
    assert bridge.stats.last_proxy_error is None


async def test_proxy_hanging_up_without_answer_is_a_proxy_failure() -> None:
    async with FakeHttpProxy(hang_up=True) as proxy, bridge_to(Upstream("http", "127.0.0.1", proxy.port)) as bridge:
        tunnel = await exchange(bridge, connect("origin.test:443", bridge_auth(bridge)))
        forward = await exchange(bridge, get("http://origin.test/json", bridge_auth(bridge)))
    assert tunnel.status == forward.status == 502
    assert "đóng kết nối giữa chừng" in tunnel.text
    assert "đóng kết nối mà không trả lời" in forward.text
    assert bridge.stats.upstream_failures == 2


@pytest.mark.parametrize(
    ("reply", "reason", "proxy_fault"),
    [
        (4, "không tới được máy đích", False),
        (5, "máy đích từ chối kết nối", False),
        (7, "proxy không hỗ trợ lệnh CONNECT", True),
    ],
)
async def test_socks5_errors_are_classified(reply: int, reason: str, proxy_fault: bool) -> None:
    async with FakeSocks5Proxy(reply=reply) as proxy, bridge_to(Upstream("socks5", "127.0.0.1", proxy.port)) as bridge:
        response = await exchange(bridge, connect("origin.test:443", bridge_auth(bridge)))
    assert response.status == 502
    assert f"không kết nối được tới origin.test:443: {reason}" in response.text
    assert bridge.stats.upstream_failures == int(proxy_fault)
    assert bridge.stats.target_failures == int(not proxy_fault)


@pytest.mark.parametrize(
    ("username", "password", "message"),
    [
        (USER, "sai", "từ chối tên đăng nhập/mật khẩu"),
        (None, None, "không chấp nhận cách đăng nhập nào (proxy cần tên đăng nhập/mật khẩu)"),
    ],
)
async def test_socks5_login_problems_are_proxy_failures(
    username: str | None, password: str | None, message: str
) -> None:
    async with (
        FakeSocks5Proxy(username=USER, password=PASSWORD) as proxy,
        bridge_to(Upstream("socks5", "127.0.0.1", proxy.port, username, password)) as bridge,
    ):
        response = await exchange(bridge, connect("origin.test:443", bridge_auth(bridge)))
    assert response.status == 502
    assert message in response.text
    assert bridge.stats.upstream_failures == 1


async def test_upload_body_is_forwarded_after_100_continue(upstream: Upstream) -> None:
    body = json.dumps({"permalinks": ["https://example.com/p/1"]}).encode()
    async with FakeOrigin() as origin, bridge_to(upstream) as bridge:
        reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
        writer.write(
            request_bytes(
                f"POST {origin.url('/echo')} HTTP/1.1",
                f"Host: origin.test:{origin.port}",
                f"Proxy-Authorization: {bridge_auth(bridge)}",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                "Expect: 100-continue",
            )
        )
        await writer.drain()
        async with asyncio.timeout(10):
            interim = await reader.readuntil(b"\r\n\r\n")
            writer.write(body)
            await writer.drain()
            raw = await reader.read()
        writer.close()
        await writer.wait_closed()
    assert interim.startswith(b"HTTP/1.1 100 Continue")
    echo = json.loads(parse_response(raw).body)
    assert (echo["method"], echo["body"]) == ("POST", body.decode())
    assert origin.requests[0].header("expect") is None


async def test_chunked_upload_is_refused_with_a_clear_message() -> None:
    async with FakeHttpProxy() as proxy, bridge_to(Upstream("http", "127.0.0.1", proxy.port)) as bridge:
        response = await exchange(
            bridge,
            request_bytes(
                "POST http://origin.test/echo HTTP/1.1",
                "Host: origin.test",
                f"Proxy-Authorization: {bridge_auth(bridge)}",
                "Transfer-Encoding: chunked",
                body=b"5\r\nhello\r\n0\r\n\r\n",
            ),
        )
    assert response.status == 501
    assert "chunked" in response.text
    assert proxy.connections == 0


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/big", BIG_BODY), ("/chunked", "".join(CHUNKED_PARTS).encode()), ("/eof", EOF_BODY)],
)
async def test_response_bodies_arrive_whole(upstream: Upstream, path: str, expected: bytes) -> None:
    async with FakeOrigin() as origin, bridge_to(upstream) as bridge, via(bridge) as client:
        response = await client.get(origin.url(path))
    assert response.status_code == 200
    assert response.content == expected


async def test_site_that_never_answers_gives_504() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy() as proxy,
        bridge_to(Upstream("http", "127.0.0.1", proxy.port), idle_timeout=0.3) as bridge,
    ):
        response = await exchange(bridge, get(origin.url("/slow"), bridge_auth(bridge)))
    assert response.status == 504
    assert "không trả lời qua proxy sau 0.3 giây" in response.text


async def test_stopping_the_bridge_closes_open_tunnels() -> None:
    async with FakeOrigin() as origin, FakeHttpProxy() as proxy:
        bridge = bridge_to(Upstream("http", "127.0.0.1", proxy.port))
        await bridge.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
        writer.write(connect(f"origin.test:{origin.port}", bridge_auth(bridge)))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        await bridge.stop()
        await bridge.stop()
        rest = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    assert head.startswith(b"HTTP/1.1 200 Connection established")
    assert rest == b""
    with pytest.raises(RuntimeError):
        _ = bridge.port


@pytest.mark.parametrize("protocol", ["http", "https", "socks5"])
async def test_closing_a_tunnel_frees_the_proxy_connection_at_once(certs: Certs, protocol: str) -> None:
    tls = certs.server_context() if protocol == "https" else None
    fake = FakeSocks5Proxy() if protocol == "socks5" else FakeHttpProxy(tls=tls)
    async with FakeOrigin() as origin, fake as proxy:
        upstream = Upstream(protocol, "127.0.0.1", proxy.port)
        async with bridge_to(upstream, tls_context=certs.client_context(), idle_timeout=60) as bridge:
            reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
            writer.write(connect(f"origin.test:{origin.port}", bridge_auth(bridge)))
            await writer.drain()
            established = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            writer.write(b"GET /json HTTP/1.1\r\nHost: origin.test\r\n\r\n")
            await writer.drain()
            head = parse_response(await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5))
            body = await reader.readexactly(int(head.headers["content-length"]))
            writer.close()
            await writer.wait_closed()
            async with asyncio.timeout(3):
                await proxy.idle.wait()
    assert established.startswith(b"HTTP/1.1 200")
    assert json.loads(body)["ip"] == EXIT_IP


def test_passwords_stay_out_of_reprs() -> None:
    upstream = Upstream("socks5", "::1", 1080, "khach", "bi-mat-1")
    credentials = BridgeCredentials.generate()
    assert "bi-mat-1" not in repr(upstream)
    assert credentials.password not in repr(credentials)
    assert upstream.label == "socks5://[::1]:1080"


def test_every_run_gets_a_new_bridge_password() -> None:
    assert len({BridgeCredentials.generate().password for _ in range(5)}) == 5


@pytest.mark.parametrize(("protocol", "port"), [("socks4", 1080), ("http", 0), ("http", 70_000)])
def test_invalid_upstreams_are_rejected(protocol: str, port: int) -> None:
    with pytest.raises(ValueError, match=r"hỗ trợ|Cổng"):
        Upstream(protocol, "127.0.0.1", port)
