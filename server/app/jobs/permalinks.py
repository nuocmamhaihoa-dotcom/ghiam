"""Chuẩn hoá permalink bài viết Facebook công khai."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = frozenset(
    {
        "fbclid",
        "mibextid",
        "ref",
        "refid",
        "eav",
        "paipv",
        "_rdc",
        "_rdr",
        "hc_ref",
        "rdid",
        "share_url",
        "comment_id",
        "reply_comment_id",
        "__cft__",
        "__tn__",
    }
)
_TRACKING_PREFIXES = ("utm_", "__xts__")
_POST_PATH = re.compile(
    r"^/(?:[\w.\-]+/)?(?:posts|videos|photos|reel|reels)/[\w.\-]+$"
    r"|^/permalink\.php$"
    r"|^/story\.php$"
    r"|^/photo(?:\.php)?$"
    r"|^/watch/?$"
    r"|^/share/(?:p|v|r)/[\w.\-]+/?$"
    r"|^/groups/[\w.\-]+/(?:posts|permalink)/[\w.\-]+/?$"
    r"|^/reel/[\w.\-]+/?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PermalinkLine:
    line: int
    raw: str
    status: str
    url: str | None = None
    platform: str | None = None
    message: str | None = None


def parse_permalinks(text: str, *, limit: int) -> list[PermalinkLine]:
    lines = text.splitlines()
    if len(lines) > limit:
        raise ValueError(f"Danh sách dài tối đa {limit} dòng")
    seen: set[str] = set()
    parsed: list[PermalinkLine] = []
    for index, raw_line in enumerate(lines, start=1):
        raw = raw_line.strip().strip("\"'")
        if not raw or raw.startswith("#"):
            continue
        item = _one(index, raw)
        url = item.url
        if item.status == "new" and url is not None:
            if url in seen:
                item = PermalinkLine(index, raw, "duplicate", url, item.platform, "Trùng một dòng phía trên")
            else:
                seen.add(url)
        parsed.append(item)
    return parsed


def _one(line: int, raw: str) -> PermalinkLine:
    candidate = raw if "://" in raw else f"https://{raw}"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").casefold().removeprefix("www.")
    if parts.scheme not in {"http", "https"} or not host:
        return PermalinkLine(line, raw, "invalid", message="Mỗi dòng phải là một địa chỉ http hoặc https")
    if "." not in host and host != "localhost":
        return PermalinkLine(line, raw, "invalid", message="Mỗi dòng phải là một địa chỉ http hoặc https")
    if host in {"youtube.com", "youtu.be", "tiktok.com", "instagram.com"} or host.endswith(
        (".youtube.com", ".tiktok.com", ".instagram.com")
    ):
        return PermalinkLine(line, raw, "unsupported", message="Hiện chỉ đọc comment công khai của bài viết Facebook")
    if host in {"fb.watch"} or host.endswith(".fb.watch"):
        url = _clean("https", "www.fb.watch", parts.path or "/", _query(parts.query))
        if parts.path.strip("/"):
            return PermalinkLine(line, raw, "new", url, "facebook")
        return PermalinkLine(line, raw, "invalid", message="Link fb.watch thiếu mã bài viết")
    if host not in {"facebook.com", "fb.com"} and not host.endswith((".facebook.com", ".fb.com")):
        return PermalinkLine(line, raw, "unsupported", message="Hiện chỉ đọc comment công khai của bài viết Facebook")
    path = parts.path or "/"
    if any(token in path.casefold() for token in ("/login", "/checkpoint", "/recover", "/messages", "/marketplace")):
        return PermalinkLine(line, raw, "invalid", message="Đây không phải permalink bài viết công khai")
    query = _query(parts.query)
    url = _clean("https", "www.facebook.com", path if path.startswith("/") else f"/{path}", query)
    if not _is_post(path, query):
        return PermalinkLine(
            line,
            raw,
            "invalid",
            message="Đây không phải permalink bài viết. Hãy dán link bài (posts, video, reel, ảnh, story)",
        )
    return PermalinkLine(line, raw, "new", url, "facebook")


def _is_post(path: str, query: list[tuple[str, str]]) -> bool:
    keys = {key for key, _ in query}
    normalized = path.rstrip("/") or "/"
    if normalized == "/permalink.php":
        return "story_fbid" in keys and "id" in keys
    if normalized == "/story.php":
        return "story_fbid" in keys
    if normalized in {"/photo.php", "/photo"}:
        return "fbid" in keys
    if normalized in {"/watch", "/watch/"}:
        return "v" in keys
    return _POST_PATH.match(normalized) is not None


def _query(raw: str) -> list[tuple[str, str]]:
    kept = [
        (key, value)
        for key, value in parse_qsl(raw, keep_blank_values=False)
        if key not in _TRACKING and not key.startswith(_TRACKING_PREFIXES)
    ]
    return sorted(kept)


def _clean(scheme: str, host: str, path: str, query: list[tuple[str, str]]) -> str:
    return urlunsplit((scheme, host, path, urlencode(query), ""))
