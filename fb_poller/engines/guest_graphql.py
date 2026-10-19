"""Rank 2 — logged-out browser, keep comment objects the page itself returns."""

from __future__ import annotations

import json
from typing import Any

from fb_poller.engines.guard import (
    first_comment_id,
    invalid_limit,
    is_comment_typename,
    nested_text,
    objects_with_comments,
    person_name,
    placeholder_text,
    public_error,
    time_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 2
NAME = "guest-graphql"


def _node_text(node: dict[str, Any]) -> str | None:
    """Comment text, including a message object nested under body."""
    return (
        nested_text(node.get("body"))
        or nested_text(node.get("preferred_body"))
        or nested_text(node.get("message"))
        or nested_text(node.get("content"))
        or nested_text(node.get("commentBody"))
        or nested_text(node.get("bodyText"))
        or nested_text(node.get("message_text"))
        or nested_text(node.get("plain_body"))
    )


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
        text = _node_text(node)
        if isinstance(text, str) and text.strip() and is_comment_typename(node.get("__typename")):
            author_node = node.get("author") if node.get("author") is not None else node.get("from")
            name = person_name(author_node)
            text = text.strip()
            if placeholder_text(text):
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        walk(value)
                return
            cid = first_comment_id(node)
            key = cid or text[:80]
            if key not in seen:
                seen.add(key)
                found.append(
                    PublicComment(
                        text=text.strip(),
                        author_name=name,
                        comment_id=cid,
                        created_time=time_text(node.get("created_time"))
                        or time_text(node.get("created_at")),
                    )
                )
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value)

    walk(payload)
    return found


def merge_comments(groups: list[list[PublicComment]], limit: int) -> list[PublicComment]:
    """Keep the first sighting of each comment. A later copy with the same id replaces a shorter one."""
    if limit < 1:
        return []
    chosen: dict[str, PublicComment] = {}
    order: list[str] = []
    for group in groups:
        for item in group:
            key = item.comment_id or item.text
            if not key:
                continue
            current = chosen.get(key)
            if current is None:
                chosen[key] = item
                order.append(key)
                continue
            if len(item.text) > len(current.text):
                chosen[key] = item
    return [chosen[key] for key in order][:limit]


def comments_from_html(html: str, limit: int) -> list[PublicComment]:
    """Read comment objects embedded in the document the browser already received."""
    if limit < 1:
        return []
    found: list[PublicComment] = []
    seen: set[str] = set()
    for obj in objects_with_comments(html):
        if len(found) >= limit:
            break
        for item in comments_from_payload(obj, limit - len(found)):
            key = item.comment_id or item.text
            if key not in seen:
                seen.add(key)
                found.append(item)
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

    groups = [comments_from_html(html, limit)]
    groups.extend(comments_from_payload(blob, limit) for blob in blobs)
    comments = merge_comments(groups, limit)
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
