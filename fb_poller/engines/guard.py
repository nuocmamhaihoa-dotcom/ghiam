"""Small helpers shared by engines. Not a scan engine."""

from __future__ import annotations

import re

_PROXY_USERINFO = re.compile(r"://[^\s/@:]+:[^\s/@]+@")
_QUERY_SECRET = re.compile(r"(?i)(token|access_token|api_key|apikey|key)=([^&\s]+)")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")


def public_error(exc: BaseException, limit: int = 300) -> str:
    """Error text safe to print. Proxy passwords and API tokens are removed."""
    text = f"{type(exc).__name__}: {exc}"
    text = _PROXY_USERINFO.sub("://*:*@", text)
    text = _QUERY_SECRET.sub(r"\1=*", text)
    text = _BEARER.sub(r"\1*", text)
    return text[:limit]


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
