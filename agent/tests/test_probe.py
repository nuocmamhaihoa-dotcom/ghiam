"""Kiểm tra IP ra của proxy qua cầu nối, với cùng danh sách trang kiểm tra như VPS."""

from __future__ import annotations

import re

import pytest

from commentscope_agent.bridge import ProxyBridge, Upstream
from commentscope_agent.probe import NO_CHECK_URLS, USER_AGENT, ProbeError, parse_ip_payload, probe_exit_ip
from tests.support import EXIT_IP, PASSWORD, USER, Certs, FakeHttpProxy, FakeOrigin, FakeSocks5Proxy, unused_port


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"ip": "1.2.3.4", "country": "VN"}', ("1.2.3.4", "VN")),
        ('{"query": "5.6.7.8", "countryCode": "vn", "country": "Vietnam"}', ("5.6.7.8", "VN")),
        ('{"origin": "9.9.9.9, 10.0.0.1"}', ("9.9.9.9", None)),
        ('{"ip": "2001:0db8:0000::0001"}', ("2001:db8::1", None)),
        ('{"ip": "1.2.3.4", "country": "Vietnam"}', ("1.2.3.4", None)),
        ("1.2.3.4\n", ("1.2.3.4", None)),
        ("<html>không phải IP</html>", (None, None)),
        ('{"ip": "999.1.1.1"}', (None, None)),
    ],
)
def test_parse_ip_payload_understands_common_ip_services(body: str, expected: tuple[str | None, str | None]) -> None:
    assert parse_ip_payload(body) == expected


async def test_probe_reads_the_exit_ip_through_the_proxy() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy() as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
    ):
        exit_ip = await probe_exit_ip(bridge, [origin.url("/json")], timeout_sec=5)
    assert (exit_ip.ip, exit_ip.country, exit_ip.check_url) == (EXIT_IP, "VN", origin.url("/json"))
    assert exit_ip.latency_ms >= 0
    assert origin.requests[0].header("user-agent") == USER_AGENT


async def test_probe_works_with_https_check_pages_over_socks5(certs: Certs) -> None:
    async with (
        FakeOrigin(tls=certs.server_context()) as origin,
        FakeSocks5Proxy(username=USER, password=PASSWORD) as proxy,
        ProxyBridge(Upstream("socks5", "127.0.0.1", proxy.port, USER, PASSWORD)) as bridge,
    ):
        exit_ip = await probe_exit_ip(bridge, [origin.url("/json")], timeout_sec=5, verify=certs.client_context())
    assert exit_ip.ip == EXIT_IP


async def test_probe_moves_on_when_a_check_page_fails() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy() as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
    ):
        urls = ["https://unknown.test/json", origin.url("/status/500"), origin.url("/text-ip")]
        exit_ip = await probe_exit_ip(bridge, urls, timeout_sec=5)
    assert (exit_ip.ip, exit_ip.country, exit_ip.check_url) == (EXIT_IP, None, origin.url("/text-ip"))


async def test_probe_stops_at_the_first_proxy_failure() -> None:
    upstream = Upstream("http", "127.0.0.1", unused_port(), label="#3 proxy chết")
    async with ProxyBridge(upstream, connect_timeout=5) as bridge:
        with pytest.raises(ProbeError, match="Không kết nối được tới proxy #3 proxy chết") as caught:
            await probe_exit_ip(bridge, ["http://origin.test/json", "https://origin.test/json"], timeout_sec=5)
    assert caught.value.url == "http://origin.test/json"
    assert bridge.stats.upstream_failures == 1


async def test_blocked_check_page_keeps_the_status() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy() as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
    ):
        with pytest.raises(ProbeError, match=re.escape("Trang kiểm tra origin.test trả HTTP 403")) as caught:
            await probe_exit_ip(bridge, [origin.url("/status/403")], timeout_sec=5)
    assert caught.value.status == 403


async def test_page_without_an_ip_is_reported() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy() as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
    ):
        with pytest.raises(ProbeError, match=re.escape("Không đọc được IP từ origin.test")):
            await probe_exit_ip(bridge, [origin.url("/page")], timeout_sec=5)


async def test_probe_needs_at_least_one_check_page() -> None:
    async with ProxyBridge(Upstream("http", "127.0.0.1", 3128)) as bridge:
        with pytest.raises(ProbeError, match=NO_CHECK_URLS):
            await probe_exit_ip(bridge, [], timeout_sec=5)
