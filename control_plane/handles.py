"""Lấy @handle từ link / chữ dán / QR. Không qua Tesseract."""

from __future__ import annotations

import re
from urllib.parse import urlparse, unquote

from control_plane.people import _PROFILE_LABELS, clean_name, clean_username, profile_from_line

_URL = re.compile(
    r"https?://[^\s<>\"']+|www\.[^\s<>\"']+"
    r"|(?:(?:www|m|vm|vt)\.)?(?:tiktok\.com|instagram\.com|facebook\.com|fb\.com|zalo\.me|youtube\.com|youtu\.be)"
    r"/[^\s<>\"']+",
    re.I,
)
_SKIP = {
    "tiktok": frozenset(
        {
            "foryou",
            "explore",
            "live",
            "search",
            "tag",
            "music",
            "video",
            "photo",
            "inbox",
            "messages",
            "following",
            "friends",
            "activity",
            "setting",
            "settings",
            "about",
            "login",
            "signup",
            "download",
            "discover",
        }
    ),
    "instagram": frozenset(
        {
            "p",
            "reel",
            "reels",
            "stories",
            "explore",
            "accounts",
            "direct",
            "tv",
            "about",
            "legal",
            "developer",
            "directory",
            "lite",
            "nametag",
            "login",
        }
    ),
    "facebook": frozenset(
        {
            "watch",
            "groups",
            "pages",
            "events",
            "marketplace",
            "photo",
            "photos",
            "posts",
            "reel",
            "reels",
            "share",
            "story",
            "stories",
            "login",
            "gaming",
            "ads",
            "privacy",
            "help",
            "policies",
            "bookmarks",
            "friends",
            "messages",
            "notifications",
            "settings",
            "profile.php",
            "people",
            "hashtag",
        }
    ),
    "zalo": frozenset({"qr", "g", "article", "oa", "login"}),
    "youtube": frozenset({"watch", "shorts", "playlist", "channel", "results", "feed", "account"}),
}


def urls_in(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _URL.findall(str(text or "")):
        raw = match.rstrip(").,;]")
        if "://" not in raw:
            raw = "https://" + raw
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(raw)
    return found


def _host_kind(host: str) -> str:
    name = host.casefold()
    if name.endswith("tiktok.com"):
        return "tiktok"
    if name.endswith("instagram.com"):
        return "instagram"
    if name.endswith("facebook.com") or name == "fb.com" or name.endswith(".fb.com"):
        return "facebook"
    if name.endswith("zalo.me"):
        return "zalo"
    if name.endswith("youtube.com") or name.endswith("youtu.be"):
        return "youtube"
    return ""


def handle_from_url(value: str) -> str:
    """Trả về @handle từ URL hồ sơ. Link rút gọn hoặc trang không phải hồ sơ thì rỗng."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    kind = _host_kind(parsed.netloc.split("@")[-1].split(":")[0])
    if not kind:
        return ""
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    reserved = _SKIP.get(kind, frozenset())
    if kind in {"tiktok", "youtube"}:
        token = next((part for part in parts if part.startswith("@")), "")
        if token.lstrip("@").casefold() in reserved:
            return ""
        return clean_username(token)
    if not parts:
        return ""
    token = parts[0]
    if token.casefold() in reserved:
        return ""
    return clean_username(token)


def profile_from_share(text: str) -> dict[str, str] | None:
    """Chữ hệ thống / share / QR: lấy tên và @handle đúng như nguồn, không OCR."""
    blob = str(text or "")
    if not blob.strip():
        return None
    handle = ""
    for url in urls_in(blob):
        handle = handle_from_url(url)
        if handle:
            break
    leftover = blob
    for url in urls_in(blob):
        leftover = leftover.replace(url, " ")
    if not handle:
        from_line = profile_from_line(leftover)
        if from_line is not None:
            return from_line
        handle = clean_username(leftover)
    if not handle:
        return None
    leftover = leftover.replace(handle, " ")
    leftover = leftover.replace(handle.lstrip("@"), " ")
    for label in _PROFILE_LABELS:
        leftover = leftover.replace(label, " ")
    name = clean_name(leftover.replace("·", " "))
    if len(name) < 2:
        name = handle.lstrip("@")[:80]
    if len(name) < 2:
        return None
    return {"kind": "profile", "name": name, "contactName": "", "username": handle}


def exact_line(text: str) -> str:
    """Giữ nguyên chữ dán / share. Không lọc như OCR."""
    return " ".join(str(text or "").split())[:400]
