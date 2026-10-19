"""Small helpers shared by engines. Not a scan engine."""

from __future__ import annotations

import json
import re
from typing import Any

_PROXY_USERINFO = re.compile(r"://[^\s/@:]+:[^\s/@]+@")
_QUERY_SECRET = re.compile(r"(?i)(token|access_token|api_key|apikey|key)=([^&\s]+)")
_JSON_SECRET = re.compile(
    r"""(?i)(['"](?:access_token|api_key|apikey|token|password|secret|authorization)['"]\s*:\s*['"])([^'"]*)(['"])"""
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_COMMENT_TYPE = re.compile(r'"__typename"\s*:\s*"(?:Comment|FeedbackComment|XFBComment)"')


def redact_text(text: str, limit: int = 300) -> str:
    """Remove proxy passwords and API tokens from text that may be printed."""
    cleaned = _PROXY_USERINFO.sub("://*:*@", text)
    cleaned = _QUERY_SECRET.sub(r"\1=*", cleaned)
    cleaned = _JSON_SECRET.sub(r"\1*\3", cleaned)
    cleaned = _BEARER.sub(r"\1*", cleaned)
    return cleaned[:limit]


def public_error(exc: BaseException, limit: int = 300) -> str:
    """Error text safe to print. Proxy passwords and API tokens are removed."""
    return redact_text(f"{type(exc).__name__}: {exc}", limit)


def invalid_limit(limit: int) -> bool:
    return limit < 1


def is_comment_typename(value: object) -> bool:
    """True for comment objects. A count or composer that merely contains the word is not one."""
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return bool(lowered) and lowered.endswith("comment")


def placeholder_text(text: str) -> bool:
    """Composer hints, whether they end with an ellipsis or three dots."""
    normalized = text.strip().lower().replace("…", "...").rstrip(".")
    return normalized in {
        "write a comment",
        "write a public comment",
        "viết bình luận",
        "viết bình luận công khai",
    }


def comment_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+\.0+", text):
            return text.split(".", 1)[0]
        return text or None
    return None


def first_comment_id(row: dict[str, object]) -> str | None:
    """The first real id. A bool ``id`` must not hide ``legacy_fbid``."""
    for key in ("id", "legacy_fbid", "comment_id", "commentId"):
        found = comment_id(row.get(key))
        if found:
            return found
    return None


def person_name(value: object, depth: int = 0) -> str | None:
    """A display name, or the name inside an author object."""
    if depth > 2:
        return None
    plain = scalar_text(value)
    if plain:
        return plain
    if isinstance(value, dict):
        lowered = {str(key).strip().lower(): item for key, item in value.items()}
        for key in ("name", "short_name", "full_name", "profilename", "profile_name", "username"):
            if key not in lowered:
                continue
            child = lowered.get(key)
            found = person_name(child, depth + 1) or nested_text(child)
            if found:
                return found
    return None


def first_person_name(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    """Try each author field. An object without a name must not hide the next field."""
    for key in keys:
        found = person_name(row.get(key))
        if found:
            return found
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


def time_text(value: object, depth: int = 0) -> str | None:
    """A timestamp string or number, including one wrapped as text or timestamp."""
    if depth > 2:
        return None
    plain = scalar_text(value)
    if plain:
        return plain
    if isinstance(value, dict):
        for key in ("text", "message", "timestamp", "time", "date", "created_time", "created_at"):
            if key not in value:
                continue
            found = time_text(value.get(key), depth + 1)
            if found:
                return found
    return None


def nested_text(value: object, depth: int = 0) -> str | None:
    """Plain comment text, or the text inside a nested message or body object.

    Call this on one known field (``text``, ``message``, ``body``). Do not pass
    a whole comment row: a wrapper ``content`` key can be the post, not the comment.
    """
    if depth > 3:
        return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "message", "comment", "body", "content"):
            if key not in value:
                continue
            found = nested_text(value.get(key), depth + 1)
            if found:
                return found
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
            # The opening brace can sit behind dozens of small objects (ranges, tracking).
            for pos in reversed(positions[-250:]):
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
