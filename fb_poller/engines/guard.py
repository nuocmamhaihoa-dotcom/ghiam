"""Small helpers shared by engines. Not a scan engine."""

from __future__ import annotations

import json
import re
from typing import Any

_PROXY_USERINFO = re.compile(r"://[^\s/@:]+:[^\s/@]+@")
_QUERY_SECRET = re.compile(r"(?i)(token|access_token|api_key|apikey|key)=([^&\s]+)")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_COMMENT_TYPE = re.compile(r'"__typename"\s*:\s*"(?:Comment|FeedbackComment|XFBComment)"')


def redact_text(text: str, limit: int = 300) -> str:
    """Remove proxy passwords and API tokens from text that may be printed."""
    cleaned = _PROXY_USERINFO.sub("://*:*@", text)
    cleaned = _QUERY_SECRET.sub(r"\1=*", cleaned)
    cleaned = _BEARER.sub(r"\1*", cleaned)
    return cleaned[:limit]


def public_error(exc: BaseException, limit: int = 300) -> str:
    """Error text safe to print. Proxy passwords and API tokens are removed."""
    return redact_text(f"{type(exc).__name__}: {exc}", limit)


def invalid_limit(limit: int) -> bool:
    return limit < 1


_PLACEHOLDERS = {
    "write a comment",
    "write a comment…",
    "write a public comment",
    "write a public comment…",
    "viết bình luận",
    "viết bình luận…",
    "viết bình luận công khai…",
}


def placeholder_text(text: str) -> bool:
    return text.strip().lower() in _PLACEHOLDERS


def comment_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    return None


def scalar_text(value: object) -> str | None:
    """Keep plain names and timestamps. Do not stringify objects."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def objects_with_comments(html: str, max_hits: int = 80) -> list[Any]:
    """Decode JSON objects that contain a comment typename, even late in a large script."""
    if max_hits < 1:
        return []
    chunks = _SCRIPT.findall(html) or [html]
    decoder = json.JSONDecoder()
    found: list[Any] = []
    seen: set[tuple[int, int]] = set()
    hits = 0
    for chunk_index, chunk in enumerate(chunks):
        for match in _COMMENT_TYPE.finditer(chunk):
            if hits >= max_hits:
                return found
            hits += 1
            start = max(0, match.start() - 20000)
            window = chunk[start:]
            relative = match.start() - start
            positions = [index for index, char in enumerate(window[:relative]) if char == "{"]
            for pos in reversed(positions[-40:]):
                absolute = start + pos
                if (chunk_index, absolute) in seen:
                    break
                try:
                    obj, end = decoder.raw_decode(window, pos)
                except json.JSONDecodeError:
                    continue
                if end <= relative or not isinstance(obj, (dict, list)):
                    continue
                seen.add((chunk_index, absolute))
                found.append(obj)
                break
    return found
