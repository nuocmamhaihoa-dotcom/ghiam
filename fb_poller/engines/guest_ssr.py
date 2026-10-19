"""Rank 7 — one guest HTTP fetch, parse comments embedded in public HTML."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from fb_poller.engines.types import EngineResult, PublicComment

RANK = 7
NAME = "guest-ssr"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_LOGIN_MARKERS = ("login.php", "log in to facebook", "đăng nhập facebook")


def comments_from_html(html: str, limit: int = 25) -> list[PublicComment]:
    """Pull comment-like objects from embedded JSON. Stops at the first page."""
    found: list[PublicComment] = []
    seen: set[str] = set()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", html):
        if len(found) >= limit:
            break
        try:
            obj, _ = decoder.raw_decode(html, match.start())
        except json.JSONDecodeError:
            continue
        _walk(obj, found, seen, limit)
    return found


def _walk(node: Any, found: list[PublicComment], seen: set[str], limit: int) -> None:
    if len(found) >= limit:
        return
    if isinstance(node, list):
        for item in node:
            _walk(item, found, seen, limit)
        return
    if not isinstance(node, dict):
        return
    text = _comment_text(node)
    if text:
        author = _author(node)
        cid = str(node.get("id") or node.get("legacy_fbid") or "") or None
        key = cid or f"{author}|{text[:80]}"
        if key not in seen:
            seen.add(key)
            created = node.get("created_time") or node.get("created_at")
            found.append(
                PublicComment(
                    text=text,
                    author_name=author,
                    comment_id=cid,
                    created_time=str(created) if created else None,
                )
            )
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, found, seen, limit)


def _comment_text(node: dict[str, Any]) -> str | None:
    body = node.get("body")
    text = body.get("text") if isinstance(body, dict) else None
    text = text or node.get("message")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) < 1 or len(text) > 8000:
        return None
    typename = str(node.get("__typename") or "")
    looks = typename in {"Comment", "FeedbackComment", "XFBComment"} or (
        "author" in node and isinstance(body, dict)
    )
    if not looks and "comment" not in typename.lower():
        return None
    return text


def _author(node: dict[str, Any]) -> str | None:
    author = node.get("author") or node.get("from") or node.get("user")
    if isinstance(author, dict):
        name = author.get("name") or author.get("short_name")
        return str(name) if name else None
    return None


def _httpx_proxy(proxy: str) -> str | None:
    if not proxy.strip():
        return None
    from fb_poller.workers.proxy_manager import parse_proxy_line

    parsed = parse_proxy_line(proxy)
    if parsed is None:
        raise ValueError("proxy must be host:port, host:port:user:pass, or a URL")
    return parsed.as_httpx()


async def scan(
    url: str,
    *,
    limit: int = 25,
    timeout: float = 20.0,
    proxy: str = "",
    **_: object,
) -> EngineResult:
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
            proxy=_httpx_proxy(proxy),
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
        ) as client:
            resp = await client.get(url)
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=f"{type(exc).__name__}: {exc}")

    body = resp.text or ""
    lowered = body.lower()
    if resp.status_code >= 400 or any(mark in lowered for mark in _LOGIN_MARKERS):
        return EngineResult(
            NAME,
            RANK,
            False,
            error=f"http_{resp.status_code}" if resp.status_code >= 400 else "login_wall",
            note="Guest HTML from this IP did not include a public thread.",
        )
    comments = comments_from_html(body, limit=limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_embedded_comments",
        note="First server-rendered batch only (about 15–25 comments).",
    )
