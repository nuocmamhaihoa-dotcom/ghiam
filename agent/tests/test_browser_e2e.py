"""Chromium thật (Playwright) đi qua cầu nối tới proxy giả. Bỏ qua nếu máy chưa tải Chromium cho Playwright."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from commentscope_agent.bridge import ProxyBridge, Upstream
from commentscope_agent.browser import INSTALL_HINT, ProxiedBrowser
from tests.support import (
    PAGE_TITLE,
    PASSWORD,
    USER,
    Certs,
    FakeControlPlane,
    FakeHttpProxy,
    FakeOrigin,
    FakeSocks5Proxy,
    run_cli,
)


@pytest.fixture(scope="module", autouse=True)
def _chromium_installed() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            playwright.chromium.launch().close()
        except sync_api.Error as exc:
            if "Executable doesn't exist" not in exc.message:
                raise
            pytest.skip(f"Chưa tải Chromium cho Playwright: {INSTALL_HINT}")


def browser_for(bridge: ProxyBridge, **options: Any) -> ProxiedBrowser:
    credentials = bridge.credentials
    return ProxiedBrowser(bridge.server_url, username=credentials.username, password=credentials.password, **options)


async def test_http_and_https_pages_through_an_http_proxy_with_a_password(certs: Certs) -> None:
    async with (
        FakeOrigin() as plain,
        FakeOrigin(tls=certs.server_context()) as secure,
        FakeHttpProxy(username=USER, password=PASSWORD) as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port, USER, PASSWORD)) as bridge,
        browser_for(bridge, ignore_https_errors=True) as browser,
    ):
        first = await browser.visit(plain.url("/page"))
        second = await browser.visit(secure.url("/page"))
    assert (first.status, first.title, first.error) == (200, PAGE_TITLE, None)
    assert (second.status, second.title, second.final_url) == (200, PAGE_TITLE, secure.url("/page"))
    assert {"GET", "CONNECT"} <= {request.method for request in proxy.requests}
    assert bridge.stats.upstream_failures == 0


async def test_socks5_proxy_with_a_password_and_a_screenshot(certs: Certs, tmp_path: Path) -> None:
    screenshot = tmp_path / "trang.png"
    async with (
        FakeOrigin(tls=certs.server_context()) as origin,
        FakeSocks5Proxy(username=USER, password=PASSWORD) as proxy,
        ProxyBridge(Upstream("socks5", "127.0.0.1", proxy.port, USER, PASSWORD)) as bridge,
        browser_for(bridge, ignore_https_errors=True) as browser,
    ):
        visit = await browser.visit(origin.url("/page"), screenshot=screenshot)
    assert (visit.status, visit.title, visit.screenshot) == (200, PAGE_TITLE, screenshot)
    assert screenshot.read_bytes().startswith(b"\x89PNG")
    assert {(request.host, request.username) for request in proxy.requests} == {("origin.test", USER)}


async def test_page_the_proxy_cannot_reach_gives_a_short_error() -> None:
    async with (
        FakeHttpProxy() as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
        browser_for(bridge) as browser,
    ):
        visit = await browser.visit("https://unknown.test/")
    assert visit.status is None
    assert visit.error == "net::ERR_TUNNEL_CONNECTION_FAILED"
    assert visit.broken


async def test_waiting_for_the_user_ends_when_the_tabs_close_or_the_lease_is_lost() -> None:
    async with (
        FakeOrigin() as origin,
        FakeHttpProxy() as proxy,
        ProxyBridge(Upstream("http", "127.0.0.1", proxy.port)) as bridge,
        browser_for(bridge) as browser,
    ):
        await browser.visit(origin.url("/page"))
        assert await asyncio.wait_for(browser.wait_until_closed(asyncio.Event()), 5) is True
        await browser.visit(origin.url("/page"), keep_page=True)
        lost = asyncio.Event()
        waiting = asyncio.create_task(browser.wait_until_closed(lost))
        await asyncio.sleep(0.8)
        assert not waiting.done()
        lost.set()
        assert await asyncio.wait_for(waiting, 5) is False


async def test_open_command_checks_the_proxy_then_browses(tmp_path: Path) -> None:
    shots = tmp_path / "anh"
    async with FakeOrigin() as origin, FakeHttpProxy(username=USER, password=PASSWORD) as proxy:
        fake = FakeControlPlane(check_urls=[origin.url("/json")])
        fake.use_proxy("http", proxy.port, username=USER, password=PASSWORD)
        pages = [origin.url("/page"), origin.url("/status/404")]
        run = await run_cli(fake, "open", "--headless", "--json", "--screenshot-dir", str(shots), *pages)
    assert run.code == 0, run.stderr
    summary = run.json
    assert (summary["outcome"], summary["detail"]) == ("ok", "Mở được 3 trang qua proxy")
    assert [(visit["url"], visit["status"]) for visit in summary["visits"]] == [
        (origin.url("/json"), 200),
        (pages[0], 200),
        (pages[1], 404),
    ]
    assert summary["visits"][1]["title"] == PAGE_TITLE
    assert sorted(path.name for path in shots.iterdir()) == ["01-origin.test.png", "02-origin.test.png"]
    assert fake.last_release == {"outcome": "ok", "detail": "Mở được 3 trang qua proxy", "request_rotation": False}
    assert PASSWORD not in run.stdout + run.stderr
