from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from fb_poller.config import Settings
from fb_poller.workers.proxy_manager import ProxyConfig

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


@dataclass
class WorkerBrowser:
    slot: int
    proxy: ProxyConfig | None
    context: BrowserContext
    page: Page
    jobs_done: int = 0


class BrowserPool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.workers: list[WorkerBrowser] = []

    async def start(self, proxies: list[ProxyConfig | None], count: int) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.settings.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        for i in range(count):
            proxy = proxies[i] if i < len(proxies) else None
            context = await self._new_context(proxy)
            page = await context.new_page()
            page.set_default_timeout(self.settings.hot_job_timeout_ms)
            page.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
            self.workers.append(WorkerBrowser(slot=i, proxy=proxy, context=context, page=page))

    async def _new_context(self, proxy: ProxyConfig | None) -> BrowserContext:
        assert self._browser is not None
        kwargs: dict = {
            "viewport": {"width": 1280, "height": 720},
            "locale": "en-US",
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
        }
        if proxy and proxy.as_playwright():
            kwargs["proxy"] = proxy.as_playwright()
        context = await self._browser.new_context(**kwargs)

        async def _route(route, request):
            if request.resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", _route)
        return context

    async def maybe_recycle(self, worker: WorkerBrowser) -> None:
        worker.jobs_done += 1
        if worker.jobs_done < self.settings.browser_restart_every_jobs:
            return
        await worker.context.close()
        context = await self._new_context(worker.proxy)
        page = await context.new_page()
        page.set_default_timeout(self.settings.hot_job_timeout_ms)
        page.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
        worker.context = context
        worker.page = page
        worker.jobs_done = 0

    async def close(self) -> None:
        for w in self.workers:
            try:
                await w.context.close()
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self.workers.clear()
