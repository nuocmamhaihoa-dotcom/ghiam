from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from tiktok_osint.config import TikTokSettings
from tiktok_osint.domain.models import PublicSnapshot
from tiktok_osint.domain.normalize import public_profile_url
from tiktok_osint.errors import ProfileNotFound, ProfileNotPublic, TransientScrapeError
from tiktok_osint.policy import navigation_host_allowed
from tiktok_osint.scrape.parser import parse_public_profile_html

logger = logging.getLogger(__name__)


class PlaywrightPublicScraper:
    """Open the public profile URL only. No login, cookies, or private APIs."""

    def __init__(self, settings: TikTokSettings, browser: Any | None = None) -> None:
        self.settings = settings
        self._browser = browser
        self._playwright: Any | None = None
        self._owns_browser = browser is None

    async def fetch(self, username: str) -> PublicSnapshot:
        url = public_profile_url(username)
        browser = await self._ensure_browser()
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.route("**/*", _limit_public_navigation)
            try:
                await page.goto(url, timeout=self.settings.navigation_timeout_ms, wait_until="domcontentloaded")
            except Exception as exc:
                raise TransientScrapeError(str(exc)) from exc
            page_html = await page.content()
        finally:
            await context.close()
        parsed = parse_public_profile_html(page_html, source_url=url)
        if parsed.status == "not_found":
            raise ProfileNotFound(username)
        if parsed.status != "ok" or parsed.snapshot is None:
            raise ProfileNotPublic(username, parsed.detail or "Không có thẻ hồ sơ công khai")
        return parsed.snapshot

    async def aclose(self) -> None:
        if self._owns_browser and self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.settings.headless)
        return self._browser


async def _limit_public_navigation(route: Any) -> None:
    host = (urlparse(route.request.url).hostname or "").lower()
    if navigation_host_allowed(host):
        await route.continue_()
        return
    await route.abort()
