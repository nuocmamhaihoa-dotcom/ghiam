from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any

from tiktok_osint.domain.models import PublicSnapshot
from tiktok_osint.domain.normalize import public_profile_url, valid_username

_SCRIPT_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"', re.IGNORECASE)
_OG_DESC_RE = re.compile(r'<meta property="og:description" content="([^"]*)"', re.IGNORECASE)


@dataclass(frozen=True)
class ParseResult:
    status: str
    snapshot: PublicSnapshot | None
    detail: str | None = None


def parse_public_profile_html(page_html: str, *, source_url: str) -> ParseResult:
    """Read the public rehydration JSON. Never follows secondary private endpoints."""
    match = _SCRIPT_RE.search(page_html)
    if match:
        payload = _load_json(match.group(1))
        if payload is not None:
            return _from_payload(payload, source_url=source_url)
    partial = _from_open_graph(page_html, source_url=source_url)
    if partial is not None:
        return ParseResult("ok", partial, "open_graph")
    if _looks_like_login_wall(page_html):
        return ParseResult("not_visible", None, "login_wall")
    return ParseResult("not_visible", None, "missing_public_card")


def _load_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    for candidate in (text, html.unescape(text)):
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _from_payload(payload: dict[str, Any], *, source_url: str) -> ParseResult:
    scope = payload.get("__DEFAULT_SCOPE__")
    if not isinstance(scope, dict):
        return ParseResult("not_visible", None, "missing_scope")
    detail = scope.get("webapp.user-detail")
    if not isinstance(detail, dict):
        return ParseResult("not_visible", None, "missing_user_detail")
    status_code = detail.get("statusCode")
    user_info = detail.get("userInfo")
    if not isinstance(user_info, dict):
        if status_code in {10221, 10202}:
            return ParseResult("not_found", None, "status_code")
        return ParseResult("not_visible", None, "missing_user_info")
    user = user_info.get("user")
    stats = user_info.get("stats") if isinstance(user_info.get("stats"), dict) else {}
    if not isinstance(user, dict):
        return ParseResult("not_found" if status_code else "not_visible", None, "missing_user")
    username = user.get("uniqueId")
    if not isinstance(username, str) or not valid_username(username):
        return ParseResult("not_visible", None, "invalid_unique_id")
    bio_link = _bio_link(user.get("bioLink"))
    snapshot = PublicSnapshot(
        username=username,
        nickname=_optional_str(user.get("nickname")),
        bio=_optional_str(user.get("signature")),
        followers=_optional_int(stats.get("followerCount")),
        following=_optional_int(stats.get("followingCount")),
        likes=_optional_int(stats.get("heartCount") if stats.get("heartCount") is not None else stats.get("heart")),
        verified=bool(user.get("verified")),
        private_account=bool(user.get("privateAccount")),
        avatar_url=_optional_str(user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb")),
        bio_link=bio_link,
        source_url=source_url or public_profile_url(username),
        partial=False,
    )
    return ParseResult("ok", snapshot, None)


def _from_open_graph(page_html: str, *, source_url: str) -> PublicSnapshot | None:
    title_match = _OG_TITLE_RE.search(page_html)
    if not title_match:
        return None
    title = html.unescape(title_match.group(1))
    found = re.search(r"\(@([A-Za-z0-9._]{2,24})\)", title)
    if not found:
        return None
    username = found.group(1)
    nickname = title.split("(@", 1)[0].strip(" |") or None
    description = None
    desc_match = _OG_DESC_RE.search(page_html)
    if desc_match:
        description = html.unescape(desc_match.group(1)).strip() or None
    return PublicSnapshot(
        username=username,
        nickname=nickname,
        bio=description,
        followers=None,
        following=None,
        likes=None,
        verified=False,
        private_account=False,
        avatar_url=None,
        bio_link=None,
        source_url=source_url or public_profile_url(username),
        partial=True,
    )


def _looks_like_login_wall(page_html: str) -> bool:
    lowered = page_html.lower()
    return "log in" in lowered and "webapp.user-detail" not in lowered


def _bio_link(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        link = value.get("link")
        if isinstance(link, str) and link.strip():
            return link.strip()
    return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
