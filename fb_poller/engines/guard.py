"""Small helpers shared by engines. Not a scan engine."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

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
    folded = {fold_key(key): value for key, value in row.items()}
    for key in (
        "id",
        "legacy_fbid",
        "legacy_comment_id",
        "comment_legacy_id",
        "comment_id",
        "comment_fbid",
        "feedback_id",
    ):
        found = _id_value(folded.get(fold_key(key)))
        if found:
            return comment_id_from_url(found) or found
    for key in ("comment_url", "permalink_url", "permalink", "url", "link"):
        found = comment_id_from_url(folded.get(fold_key(key)))
        if found:
            return found
    return None


def _id_value(value: object, depth: int = 0) -> str | None:
    """A string id, or the id inside one nested id object."""
    found = comment_id(value)
    if found:
        return found
    if depth > 1 or not isinstance(value, dict):
        return None
    lowered = {fold_key(key): item for key, item in value.items()}
    for key in (
        "id",
        "legacy_fbid",
        "legacy_comment_id",
        "comment_legacy_id",
        "comment_id",
        "comment_fbid",
        "feedback_id",
    ):
        folded_key = fold_key(key)
        if folded_key not in lowered:
            continue
        found = _id_value(lowered.get(folded_key), depth + 1)
        if found:
            return found
    return None


_COMMENT_IN_URL = re.compile(r"(?:comment_id|reply_comment_id)=([A-Za-z0-9_]+)")
_REPLY_IN_URL = re.compile(r"reply_comment_id=([A-Za-z0-9_]+)")


def comment_id_from_url(value: object) -> str | None:
    """The comment id in a permalink. A reply id wins when both are present."""
    text = scalar_text(value)
    if text is None or "://" not in text:
        return None
    decoded = unquote(text)
    reply = _REPLY_IN_URL.search(decoded)
    if reply:
        return comment_id(reply.group(1))
    match = _COMMENT_IN_URL.search(decoded)
    if match is None:
        return None
    return comment_id(match.group(1))


_PROFILE_HOST = re.compile(r"(?:^|\.)(?:facebook|fb)\.com$", re.IGNORECASE)
_PROFILE_BLOCKED = {
    "posts",
    "photos",
    "videos",
    "reel",
    "reels",
    "watch",
    "groups",
    "share",
    "login.php",
    "story.php",
    "permalink.php",
    "photo.php",
    "profile.php",
    "people",
}


def _profile_slug(value: str) -> str | None:
    """A single vanity slug from a Facebook profile URL."""
    if "://" not in value:
        return None
    path = unquote(value.split("?", 1)[0].split("#", 1)[0]).rstrip("/")
    parts = [part for part in path.split("/") if part]
    host_at = None
    for index, part in enumerate(parts):
        if _PROFILE_HOST.search(part):
            host_at = index
            break
    if host_at is None:
        return None
    slug_parts = parts[host_at + 1 :]
    if len(slug_parts) != 1:
        return None
    slug = slug_parts[0]
    if not slug or slug.isdigit() or slug.lower() in _PROFILE_BLOCKED:
        return None
    if re.fullmatch(r"[A-Za-z0-9.\-]+", slug) is None:
        return None
    return slug


def person_name(value: object, depth: int = 0) -> str | None:
    """A display name, or the name inside an author object."""
    if depth > 2:
        return None
    plain = scalar_text(value)
    if plain:
        if depth < 2 and plain.startswith("{") and plain.endswith("}"):
            try:
                loaded = json.loads(plain)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, (dict, list)):
                found = person_name(loaded, depth + 1)
                if found:
                    return found
        vanity = _profile_slug(plain)
        if vanity:
            return vanity
        return plain
    if isinstance(value, list):
        for item in value:
            found = person_name(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, dict):
        lowered = {fold_key(key): item for key, item in value.items()}
        for key in (
            "name",
            "short_name",
            "full_name",
            "profile_name",
            "username",
            "display_name",
            "nickname",
            "screen_name",
            "actor",
            "page_name",
            "owner_name",
            "author_title",
            "user_full_name",
            "author_nickname",
            "commenter_full_name",
            "i18n_name",
            "alternate_name",
            "localized_name",
            "structured_name",
            "commenter_name",
            "sort_name",
            "author_sort_name",
            "actor_name",
            "from_name",
            "author_name",
            "commenter",
            "poster_name",
            "owner_full_name",
            "user_display_name",
            "comment_author",
            "writer_name",
            "display_title",
            "profile_title",
            "user_title",
            "author_label",
            "sender_name",
            "entity_name",
            "commenter_title",
            "profile_label",
            "actor_title",
            "creator_name",
            "owner_title",
            "user_label",
            "commenter_label",
            "author_text",
        ):
            token = fold_key(key)
            if token not in lowered:
                continue
            child = lowered.get(token)
            found = person_name(child, depth + 1) or nested_text(child)
            if found:
                return found
        given = person_name(lowered.get("firstname"), depth + 1) or person_name(lowered.get("givenname"), depth + 1)
        middle = person_name(lowered.get("middlename"), depth + 1)
        family = person_name(lowered.get("lastname"), depth + 1) or person_name(lowered.get("familyname"), depth + 1)
        parts = [part for part in (given, middle, family) if part]
        if parts:
            return " ".join(parts)
    return None


def fold_key(key: object) -> str:
    """Compare headers ignoring case, spaces, and underscores."""
    return "".join(ch for ch in str(key).strip().lower() if ch.isalnum())


def first_person_name(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    """Try each author field. An object without a name must not hide the next field."""
    folded = {fold_key(key): value for key, value in row.items()}
    for key in keys:
        found = person_name(folded.get(fold_key(key)))
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
        folded = {fold_key(key): item for key, item in value.items()}
        for key in (
            "text",
            "message",
            "timestamp",
            "time",
            "date",
            "relative",
            "created_time",
            "creation_time",
            "created_at",
            "created",
        ):
            token = fold_key(key)
            if token not in folded:
                continue
            found = time_text(folded.get(token), depth + 1)
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
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, list):
                return None
            if isinstance(item, dict):
                if any(
                    key in item
                    for key in ("author", "from", "author_name", "id", "comment_id", "commentId", "legacy_fbid")
                ):
                    return None
            found = nested_text(item, depth + 1)
            if found:
                parts.append(found)
        return " ".join(parts) if parts else None
    if isinstance(value, dict):
        for key in (
            "text",
            "message",
            "comment",
            "body",
            "content",
            "commentBody",
            "comment_body",
            "bodyText",
            "body_text",
            "message_text",
            "plain_body",
            "plaintext",
            "comment_message",
            "body_html",
        ):
            if key not in value:
                continue
            found = nested_text(value.get(key), depth + 1)
            if found:
                return found
    return None


def timestamp_of(row: object) -> str | None:
    """A timestamp field on a row. The comment message is not a timestamp."""
    if not isinstance(row, dict):
        return None
    folded = {fold_key(key): value for key, value in row.items()}
    for key in (
        "created_time",
        "creation_time",
        "created_at",
        "created",
        "comment_time",
        "updated_time",
        "posted_at",
        "published_at",
        "date_created",
        "posted_time",
        "comment_create_time",
        "creation_timestamp",
        "created_time_seconds",
        "created_time_ms",
        "created_seconds",
        "publish_time",
        "published_time",
        "comment_created_at",
        "commented_at",
        "comment_timestamp",
        "created_unix",
        "posted_at_ms",
        "time_created",
        "edited_time",
        "publish_at",
        "comment_date",
        "message_time",
        "created_ts",
        "posted_date",
        "comment_at",
        "sent_at",
        "created_epoch",
        "datetime",
        "date",
        "time",
        "timestamp",
    ):
        found = time_text(folded.get(fold_key(key)))
        if found:
            return found
    return None


def text_carrier(row: dict[str, object]) -> dict[str, object] | None:
    """A nested object that holds the comment text together with its author and id."""
    for key in ("comment", "commentText", "comment_text", "message", "body", "text"):
        value = row.get(key)
        if isinstance(value, dict) and nested_text(value):
            return value
    return None


def connection_rows(value: object) -> list[object]:
    """A list, ``{data: [...]}``, or ``{edges: [{node: ...}]}``. An empty list is empty."""
    if isinstance(value, list):
        return [_unwrap_node(item) for item in value]
    if not isinstance(value, dict):
        return []
    for key in ("data", "edges", "nodes"):
        child = value.get(key)
        if isinstance(child, list) and child:
            return [_unwrap_node(item) for item in child]
    return []


def _unwrap_node(item: object) -> object:
    """An edge is ``{node, cursor}``. A row that already has comment text stays itself."""
    if not isinstance(item, dict):
        return item
    node = item.get("node")
    if not isinstance(node, dict):
        return item
    for key in ("text", "message", "comment", "comment_text", "commentText", "commentBody", "body", "bodyText", "preferred_body"):
        if nested_text(item.get(key)):
            return item
    return node


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
