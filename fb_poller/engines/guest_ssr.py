"""Rank 7 — one guest HTTP fetch, parse comments embedded in public HTML."""

from __future__ import annotations

import re
from typing import Any

import httpx

from fb_poller.engines.guard import (
    first_comment_id,
    invalid_limit,
    is_comment_typename,
    nested_text,
    objects_with_comments,
    person_name,
    placeholder_text,
    public_error,
    redact_text,
    time_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 7
NAME = "guest-ssr"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
def comments_from_html(html: str, limit: int = 25) -> list[PublicComment]:
    """Pull comment objects from script JSON, including ones that sit after other values."""
    if limit < 1:
        return []
    found: list[PublicComment] = []
    seen: set[str] = set()
    for obj in objects_with_comments(html):
        if len(found) >= limit:
            break
        _walk(obj, found, seen, limit)
    return found


def looks_like_login(final_url: str, body: str) -> bool:
    """True when the response itself is a login page, not merely a page that links to login."""
    url = final_url.lower()
    if "/login" in url or "login.php" in url:
        return True
    title = re.search(r"<title>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
    if title and title.group(1).strip().lower() in {"log in to facebook", "đăng nhập facebook", "facebook login"}:
        return True
    return False


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
        cid = first_comment_id(node)
        key = cid or f"{author}|{text[:80]}"
        if key not in seen:
            seen.add(key)
            created = time_text(node.get("created_time")) or time_text(node.get("created_at"))
            found.append(
                PublicComment(
                    text=text,
                    author_name=author,
                    comment_id=cid,
                    created_time=created,
                )
            )
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, found, seen, limit)


def _comment_text(node: dict[str, Any]) -> str | None:
    text = (
        nested_text(node.get("body"))
        or nested_text(node.get("preferred_body"))
        or nested_text(node.get("message"))
        or nested_text(node.get("content"))
        or nested_text(node.get("commentBody"))
        or nested_text(node.get("bodyText"))
        or nested_text(node.get("message_text"))
        or nested_text(node.get("plain_body"))
    )
    if not text or len(text) > 8000:
        return None
    if not is_comment_typename(node.get("__typename")) or placeholder_text(text):
        return None
    return text


def _author(node: dict[str, Any]) -> str | None:
    for key in ("author", "from", "user"):
        found = person_name(node.get(key))
        if found:
            return found
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
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
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
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    body = resp.text or ""
    comments = comments_from_html(body, limit=limit) if resp.status_code < 400 else []
    if comments:
        return EngineResult(
            NAME,
            RANK,
            True,
            comments=comments,
            note="First server-rendered batch only (about 15–25 comments).",
        )
    final_url = str(resp.url)
    if resp.status_code >= 400:
        return EngineResult(
            NAME,
            RANK,
            False,
            error=redact_text(f"http_{resp.status_code}: {body}", 220),
            note="Guest HTML from this IP did not include a public thread.",
        )
    if looks_like_login(final_url, body):
        return EngineResult(
            NAME,
            RANK,
            False,
            error="login_wall",
            note="Guest HTML from this IP did not include a public thread.",
        )
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_embedded_comments",
        note="First server-rendered batch only (about 15–25 comments).",
    )
