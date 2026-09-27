"""Mở Chromium (Playwright) đi qua cầu nối proxy của agent."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Self

from commentscope_agent.verdict import PageVisit

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Playwright, ProxySettings

# Không cho WebRTC gửi UDP đi thẳng ra ngoài proxy (lộ IP thật của máy PC).
CHROMIUM_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",)
INSTALL_HINT = "python -m playwright install chromium"
POLL_SEC = 0.5


class BrowserUnavailableError(Exception):
    """Không mở được Chromium: chưa cài Playwright/Chromium hoặc máy thiếu thư viện hệ thống."""


class ProxiedBrowser:
    def __init__(
        self,
        proxy_server: str,
        *,
        username: str,
        password: str,
        headless: bool = True,
        navigation_timeout_sec: float = 45.0,
        ignore_https_errors: bool = False,
    ) -> None:
        self._proxy: ProxySettings = {"server": proxy_server, "username": username, "password": password}
        self._headless = headless
        self._timeout_sec = navigation_timeout_sec
        self._ignore_https_errors = ignore_https_errors
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Self:
        try:
            from playwright.async_api import Error, async_playwright
        except ImportError:
            raise BrowserUnavailableError(
                f"Chưa cài Playwright: chạy pip install -r requirements.txt rồi {INSTALL_HINT}"
            ) from None
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                headless=self._headless, proxy=self._proxy, args=list(CHROMIUM_ARGS)
            )
            context = await browser.new_context(
                ignore_https_errors=self._ignore_https_errors, no_viewport=not self._headless
            )
        except Error as exc:
            await playwright.stop()
            raise BrowserUnavailableError(_launch_error(exc.message)) from None
        context.set_default_navigation_timeout(self._timeout_sec * 1000)
        self._playwright, self._browser, self._context = playwright, browser, context
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        playwright, browser = self._playwright, self._browser
        self._playwright = self._browser = self._context = None
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
        if playwright is not None:
            with contextlib.suppress(Exception):
                await playwright.stop()

    async def visit(self, url: str, *, screenshot: Path | None = None, keep_page: bool = False) -> PageVisit:
        from playwright.async_api import Error
        from playwright.async_api import TimeoutError as NavigationTimeout

        page = await self._require_context().new_page()
        started = time.perf_counter()
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
        except NavigationTimeout:
            visit = PageVisit(
                url=url, error=f"Quá {self._timeout_sec:g} giây mà trang chưa tải xong", elapsed_ms=_since(started)
            )
        except Error as exc:
            visit = PageVisit(url=url, error=_navigation_error(exc.message), elapsed_ms=_since(started))
        else:
            elapsed_ms = _since(started)
            title: str | None = None
            with contextlib.suppress(Error):
                title = await page.title() or None
            saved: Path | None = None
            if screenshot is not None:
                with contextlib.suppress(Error):
                    await page.screenshot(path=screenshot)
                    saved = screenshot
            visit = PageVisit(
                url=url,
                status=response.status if response is not None else None,
                final_url=page.url,
                title=title,
                elapsed_ms=elapsed_ms,
                screenshot=saved,
            )
        if not keep_page:
            with contextlib.suppress(Error):
                await page.close()
        return visit

    async def wait_until_closed(self, stop: asyncio.Event) -> bool:
        """Chờ người dùng đóng hết tab hoặc cửa sổ Chromium; trả về False nếu phải dừng sớm vì `stop`."""
        context = self._require_context()
        while self._browser is not None and self._browser.is_connected() and context.pages:
            try:
                await asyncio.wait_for(stop.wait(), POLL_SEC)
            except TimeoutError:
                continue
            return False
        return True

    def _require_context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Chromium chưa được mở")
        return self._context


def _launch_error(message: str) -> str:
    first_line = message.strip().splitlines()[0] if message.strip() else "không rõ lỗi"
    if "Executable doesn't exist" in message:
        return f"Chưa tải Chromium cho Playwright: chạy {INSTALL_HINT}"
    if "missing dependencies" in message:
        return "Máy thiếu thư viện hệ thống cho Chromium: chạy python -m playwright install-deps chromium"
    if "DISPLAY" in message or "X server" in message:
        return "Không mở được cửa sổ Chromium (máy không có màn hình?): thêm --headless"
    return f"Không mở được Chromium: {first_line}"


def _navigation_error(message: str) -> str:
    lines = message.strip().splitlines()
    line = lines[0] if lines else "không rõ lỗi"
    return line.removeprefix("Page.goto: ").split(" at http", 1)[0].strip()


def _since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
