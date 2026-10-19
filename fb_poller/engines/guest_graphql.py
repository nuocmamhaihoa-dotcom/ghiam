"""Rank 2 — logged-out browser, keep comment objects the page itself returns."""

from __future__ import annotations

import json
from typing import Any

from fb_poller.engines.guard import comment_id, invalid_limit, placeholder_text, public_error
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 2
NAME = "guest-graphql"


def comments_from_payload(payload: Any, limit: int) -> list[PublicComment]:
    found: list[PublicComment] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        body = node.get("body")
        text = body.get("text") if isinstance(body, dict) else None
        text = text or node.get("message")
        typename = str(node.get("__typename") or "")
        if isinstance(text, str) and text.strip() and "comment" in typename.lower():
            author = node.get("author") if isinstance(node.get("author"), dict) else {}
            name = author.get("name") if isinstance(author, dict) else None
            text = text.strip()
            if placeholder_text(text):
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        walk(value)
                return
            cid = comment_id(node.get("id")) or comment_id(node.get("legacy_fbid"))
            key = cid or text[:80]
            if key not in seen:
                seen.add(key)
                created = node.get("created_time")
                found.append(
                    PublicComment(
                        text=text.strip(),
                        author_name=str(name) if name else None,
                        comment_id=cid,
                        created_time=str(created) if created else None,
                    )
                )
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value)

    walk(payload)
    return found


def comments_from_html(html: str, limit: int) -> list[PublicComment]:
    """Read comment objects embedded in the document the browser already received."""
    import re

    if limit < 1:
        return []
    chunks = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL)
    if not chunks:
        chunks = [html]
    found: list[PublicComment] = []
    seen: set[str] = set()
    decoder = json.JSONDecoder()
    for chunk in chunks:
        if "comment" not in chunk.lower():
            continue
        index = 0
        attempts = 0
        while index < len(chunk) and len(found) < limit and attempts < 400:
            brace = chunk.find("{", index)
            if brace < 0:
                break
            attempts += 1
            try:
                obj, end = decoder.raw_decode(chunk, brace)
            except json.JSONDecodeError:
                index = brace + 1
                continue
            for item in comments_from_payload(obj, limit - len(found)):
                key = item.comment_id or item.text
                if key not in seen:
                    seen.add(key)
                    found.append(item)
            index = max(end, brace + 1)
    return found


def _browser_proxy(proxy: str) -> dict[str, str] | None:
    if not proxy.strip():
        return None
    from fb_poller.workers.proxy_manager import parse_proxy_line

    parsed = parse_proxy_line(proxy)
    if parsed is None:
        raise ValueError("proxy must be host:port, host:port:user:pass, or a URL")
    return parsed.as_playwright()


async def scan(
    url: str,
    *,
    limit: int = 50,
    timeout_ms: int = 60000,
    proxy: str = "",
    **_: object,
) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return EngineResult(NAME, RANK, False, error=f"playwright_missing: {exc}")

    blobs: list[Any] = []
    html = ""

    async def on_response(response: Any) -> None:
        target = response.url.lower()
        if "graphql" not in target and "api/graphql" not in target:
            return
        try:
            blobs.append(await response.json())
            return
        except Exception:
            pass
        try:
            raw = await response.text()
        except Exception:
            return
        raw = raw.lstrip()
        if raw.startswith("for (;;);"):
            raw = raw[len("for (;;);") :].lstrip()
        try:
            blobs.append(json.loads(raw))
        except Exception:
            return

    try:
        async with async_playwright() as pw:
            launch: dict[str, Any] = {"headless": True}
            browser_proxy = _browser_proxy(proxy)
            if browser_proxy:
                launch["proxy"] = browser_proxy
            browser = await pw.chromium.launch(**launch)
            context = await browser.new_context(
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()
            page.on("response", on_response)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)
            for label in ("Newest", "Mới nhất", "Most relevant", "Phù hợp nhất"):
                loc = page.get_by_text(label, exact=True)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(2500)
                    except Exception:
                        pass
                    break
            html = await page.content()
            await context.close()
            await browser.close()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    comments: list[PublicComment] = []
    seen: set[str] = set()
    for item in comments_from_html(html, limit):
        key = item.comment_id or item.text
        if key not in seen:
            seen.add(key)
            comments.append(item)
    for blob in blobs:
        for item in comments_from_payload(blob, limit):
            key = item.comment_id or item.text
            if key in seen:
                continue
            seen.add(key)
            comments.append(item)
            if len(comments) >= limit:
                break
    # Also parse inline JSON the document already shipped
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments[:limit],
        error=None if comments else "no_graphql_comments",
        note=f"Captured {len(blobs)} public GraphQL responses plus embedded comment JSON. No cookies.",
    )


def payload_from_text(raw: str) -> Any:
    return json.loads(raw)
