"""Đọc link Facebook thành id trang hoặc username. Không gọi mạng."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

_USERNAME = re.compile(r"^[A-Za-z0-9.]{2,100}$")
_DIGITS = re.compile(r"^\d{5,20}$")
_HOSTS = {
    "facebook.com",
    "m.facebook.com",
    "web.facebook.com",
    "mbasic.facebook.com",
    "business.facebook.com",
    "lm.facebook.com",
}

# Đường dẫn không phải Fanpage. Chỉ dùng để trả lời người dùng, không để gọi mạng.
_NOT_A_PAGE = {
    "marketplace": "Đây là link Marketplace, không phải Fanpage.",
    "stories": "Đây là link story, không phải Fanpage.",
    "story.php": "Đây là link story, không phải Fanpage.",
    "watch": "Đây là link video, không phải Fanpage.",
    "reel": "Đây là link reel, không phải Fanpage.",
    "reels": "Đây là link reel, không phải Fanpage.",
    "login": "Đây là trang đăng nhập, không phải Fanpage.",
    "login.php": "Đây là trang đăng nhập, không phải Fanpage.",
    "checkpoint": "Đây là trang xác minh, không phải Fanpage.",
    "help": "Đây là trang trợ giúp, không phải Fanpage.",
    "privacy": "Đây là trang quyền riêng tư, không phải Fanpage.",
    "policies": "Đây là trang chính sách, không phải Fanpage.",
    "settings": "Đây là trang cài đặt, không phải Fanpage.",
    "share": "Đây là link chia sẻ, không phải Fanpage.",
    "sharer": "Đây là link chia sẻ, không phải Fanpage.",
    "sharer.php": "Đây là link chia sẻ, không phải Fanpage.",
    "dialog": "Đây là hộp thoại Facebook, không phải Fanpage.",
    "plugins": "Đây là plugin Facebook, không phải Fanpage.",
    "search": "Đây là link tìm kiếm, không phải Fanpage.",
    "groups": "Đây là link nhóm, không phải Fanpage.",
    "events": "Đây là link sự kiện, không phải Fanpage.",
    "messages": "Đây là link tin nhắn, không phải Fanpage.",
    "photo.php": "Đây là link ảnh, không phải Fanpage.",
    "permalink.php": "Đây là link bài viết, không phải Fanpage.",
    "l.php": "Đây là link chuyển hướng, không phải Fanpage.",
    "hashtag": "Đây là hashtag, không phải Fanpage.",
    "people": "Đây là link người dùng, không phải Fanpage.",
    "friends": "Đây là link bạn bè, không phải Fanpage.",
    "gaming": "Đây là link gaming, không phải Fanpage.",
    "recover": "Đây là trang khôi phục, không phải Fanpage.",
    "bookmarks": "Đây là link bookmark, không phải Fanpage.",
}


class PageRefError(ValueError):
    """Link không trỏ tới một Fanpage."""


@dataclass(frozen=True)
class PageRef:
    kind: Literal["id", "username"]
    value: str


def _host(hostname: str | None) -> str:
    host = (hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        return host[4:]
    return host


def _digits(value: str | None) -> str | None:
    if value and _DIGITS.fullmatch(value):
        return value
    return None


def parse_page_ref(raw: str) -> PageRef:
    """Trả về id số hoặc username. Link rút gọn và link không phải Fanpage bị từ chối."""
    text = raw.strip()
    if not text:
        raise PageRefError("Nhập link Fanpage.")
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise PageRefError("Link không hợp lệ.") from exc
    if parsed.scheme not in {"http", "https"}:
        raise PageRefError("Chỉ nhận link http hoặc https.")
    host = _host(parsed.hostname)
    if host in {"fb.me", "m.fb.me"}:
        raise PageRefError("Hãy dán link facebook.com đầy đủ, không dùng link rút gọn fb.me.")
    if host not in _HOSTS:
        raise PageRefError("Đây không phải link Facebook.")

    query = parse_qs(parsed.query, keep_blank_values=False)
    path = unquote(parsed.path or "")
    segments = [part for part in path.split("/") if part]

    page_id = _digits((query.get("view_all_page_id") or [None])[0])
    if page_id:
        return PageRef("id", page_id)
    if segments[:2] == ["ads", "library"] or segments == ["ads", "library"]:
        raise PageRefError(
            "Link thư viện này không có Fanpage. Dán link Fanpage hoặc link có view_all_page_id."
        )

    if segments[:1] == ["profile.php"]:
        profile_id = _digits((query.get("id") or [None])[0])
        if not profile_id:
            raise PageRefError("Link profile.php thiếu tham số id.")
        return PageRef("id", profile_id)

    if not segments:
        raise PageRefError("Link này không có tên Fanpage.")

    head = segments[0]
    if head in _NOT_A_PAGE:
        raise PageRefError(_NOT_A_PAGE[head])

    if head == "pages":
        numeric = [part for part in segments[1:] if _digits(part)]
        if not numeric:
            raise PageRefError("Link /pages/ này không có id Fanpage.")
        return PageRef("id", numeric[-1])

    if len(segments) == 1 and _digits(head):
        return PageRef("id", head)

    if not _USERNAME.fullmatch(head) or head in {".", ".."}:
        raise PageRefError("Không đọc được tên Fanpage từ link này.")
    return PageRef("username", head)
