"""Rank 5 — guest browser, Newest tab, first page only. Own browser, no shared pool."""

from __future__ import annotations

from fb_poller.engines.types import EngineResult, PublicComment

RANK = 5
NAME = "pa1-newest"

_NEWEST_LABELS = ("Newest", "Mới nhất", "Most recent")


async def scan(url: str, *, limit: int = 20, timeout_ms: int = 25000, **_: object) -> EngineResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return EngineResult(NAME, RANK, False, error=f"playwright_missing: {exc}")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(locale="en-US")
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if resp and resp.status >= 400:
                await browser.close()
                return EngineResult(NAME, RANK, False, error=f"http_{resp.status}")
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
