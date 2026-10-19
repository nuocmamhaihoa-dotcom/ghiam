"""Rank 5 — guest browser, Newest tab, first page only. Own browser, no shared pool."""

from __future__ import annotations

from typing import Any

from fb_poller.engines.types import EngineResult, PublicComment

RANK = 5
NAME = "pa1-newest"

_NEWEST_LABELS = ("Newest", "Mới nhất", "Most recent")


def _browser_proxy(proxy: str) -> dict[str, str] | None:
    if not proxy.strip():
        return None
    from fb_poller.workers.proxy_manager import parse_proxy_line

    parsed = parse_proxy_line(proxy)
    if parsed is None:
        raise ValueError("proxy must be host:port, host:port:user:pass, or a URL")
    return parsed.as_playwright()


async def _dismiss_gate(page: Any) -> None:
    for label in ("Close", "Đóng"):
        loc = page.get_by_role("button", name=label)
        if await loc.count():
            try:
                await loc.first.click(timeout=1500)
            except Exception:
                return


async def scan(
    url: str,
    *,
    limit: int = 20,
    timeout_ms: int = 60000,
    proxy: str = "",
    **_: object,
) -> EngineResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return EngineResult(NAME, RANK, False, error=f"playwright_missing: {exc}")

    try:
        async with async_playwright() as pw:
            launch: dict[str, Any] = {"headless": True}
            browser_proxy = _browser_proxy(proxy)
            if browser_proxy:
                launch["proxy"] = browser_proxy
            browser = await pw.chromium.launch(**launch)
            page = await browser.new_page(
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if resp and resp.status >= 400:
                await browser.close()
                return EngineResult(NAME, RANK, False, error=f"http_{resp.status}")
            await page.wait_for_timeout(2500)
            await _dismiss_gate(page)
            for label in _NEWEST_LABELS:
                loc = page.get_by_text(label, exact=True)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                    break
            texts = await page.locator('[role="article"]').all_inner_texts()
            await browser.close()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=f"{type(exc).__name__}: {exc}"[:300])

    comments: list[PublicComment] = []
    for block in texts:
        lowered = block.lower()
        if "all reactions" in lowered or "tất cả cảm xúc" in lowered:
            continue
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        author, text = lines[0], " ".join(lines[1:])[:2000]
        if len(text) < 1:
            continue
        comments.append(PublicComment(text=text, author_name=author))
        if len(comments) >= limit:
            break
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_visible_comments",
        note="Newest first page only. Nested replies are not expanded.",
    )
