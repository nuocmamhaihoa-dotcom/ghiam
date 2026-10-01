"""Đọc comment công khai trên HTML/JSON mà Facebook trả cho bài viết công khai.

Không đăng nhập, không điền mật khẩu, không giải captcha. Trang yêu cầu đăng nhập hoặc
kiểm tra bảo mật được coi là bị chặn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from commentscope_agent.commentnorm import clean_text, parse_likes, parse_time, stable_id

_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"})
_ACTION_LABELS = frozenset(
    {
        "thích",
        "like",
        "likes",
        "phản hồi",
        "reply",
        "trả lời",
        "xem thêm",
        "see more",
        "đăng nhập",
        "log in",
        "login",
        "chia sẻ",
        "share",
    }
)
_MORE_COMMENTS = re.compile(
    r"^(xem thêm bình luận|xem các bình luận(?: trước)?|see more comments|view more comments|more comments)\b",
    re.IGNORECASE,
)
_MORE_REPLIES = re.compile(
    r"^(xem thêm phản hồi|xem các phản hồi|view more replies|see more replies)\b",
    re.IGNORECASE,
)
_REPLY_COUNT = re.compile(r"(\d+)\s*(?:phản hồi|trả lời|replies|reply)\b", re.IGNORECASE)
_LIKE_TOKEN = re.compile(
    r"\d[\d.,]*\s*(?:triệu|trieu|nghìn|nghin|tỷ|ty|tr|k|n|m|b|t)?",
    re.IGNORECASE,
)
_ARTICLE_LABEL = re.compile(r"^(?:bình luận|phản hồi|comment|reply)(?:\s+(?:của|by)\s+(.+))?$", re.IGNORECASE)
_UNAVAILABLE = (
    "nội dung này hiện không khả dụng",
    "nội dung này không còn tồn tại",
    "this content isn't available",
    "sorry, this content isn't available",
    "bài viết này không hiển thị",
)
_LOGIN_TEXT = (
    "bạn phải đăng nhập",
    "you must log in",
    "log in to continue",
    "đăng nhập để tiếp tục",
    "kiểm tra bảo mật",
    "security check",
    "confirm you're human",
    "xác nhận bạn là người",
)
_STORY_TYPES = frozenset({"Story", "Post", "Video", "Photo", "User", "Page", "Group"})
_COMMENT_TYPES = frozenset({"Comment", "XFBComment"})


@dataclass
class Elem:
    tag: str
    attrs: dict[str, str]
    children: list[Elem | str]
    parent: Elem | None = None

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return clean_text("".join(parts))

    def descendants(self) -> list[Elem]:
        found = [self]
        for child in self.children:
            if isinstance(child, Elem):
                found.extend(child.descendants())
        return found


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Elem("document", {}, [])
        self._stack: list[Elem] = [self.root]
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._skip += 1
            return
        if self._skip:
            return
        element = Elem(lowered, {key.lower(): value or "" for key, value in attrs}, [], self._stack[-1])
        self._stack[-1].children.append(element)
        if lowered not in _VOID_TAGS:
            self._stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == lowered:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self._stack[-1].children.append(data)


@dataclass(frozen=True, slots=True)
class ExtractedComment:
    external_id: str
    parent_external_id: str | None
    text: str
    author: str
    author_id: str | None
    author_url: str | None
    time: datetime | None
    time_raw: str | None
    likes: int | None
    likes_raw: str | None
    reply_count: int | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "parent_external_id": self.parent_external_id,
            "text": self.text,
            "author": self.author,
            "author_id": self.author_id,
            "author_url": self.author_url,
            "time": None if self.time is None else self.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_raw": self.time_raw,
            "likes": self.likes,
            "likes_raw": self.likes_raw,
            "reply_count": self.reply_count,
        }


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    kind: str
    comments: tuple[ExtractedComment, ...]
    next_url: str | None
    comments_reported: int | None


def parse_html(html: str, page_url: str, *, now: datetime, parent_id: str | None = None) -> ParsedDocument:
    builder = _Builder()
    builder.feed(html)
    root = builder.root
    comments = _html_comments(root, page_url, now=now, parent_id=parent_id)
    reported = _reported_in_html(html)
    if _is_login_url(page_url) or (_is_login_wall(html) and not comments):
        return ParsedDocument("blocked", (), None, reported)
    if not comments and _is_unavailable(html):
        return ParsedDocument("not_available", (), None, reported)
    if comments or _looks_like_post(html):
        return ParsedDocument("comments", tuple(comments), _next_url(root, page_url), reported)
    return ParsedDocument("unknown", (), None, reported)


def parse_json_blob(payload: Any, *, now: datetime) -> tuple[list[ExtractedComment], int | None]:
    comments: list[ExtractedComment] = []
    seen: set[str] = set()
    reported: int | None = None

    def walk(node: Any) -> None:
        nonlocal reported
        if isinstance(node, dict):
            count = _reported_count(node)
            if count is not None:
                reported = count if reported is None else max(reported, count)
            comment = _comment_from_json(node, now)
            if comment is not None and comment.external_id not in seen:
                seen.add(comment.external_id)
                comments.append(comment)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return comments, reported


def parse_json_text(text: str, *, now: datetime) -> tuple[list[ExtractedComment], int | None]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [], None
    return parse_json_blob(payload, now=now)


def is_facebook_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    return host in {"facebook.com", "fb.watch", "fb.com"} or host.endswith((".facebook.com", ".fb.watch", ".fb.com"))


def to_mbasic(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    if host.endswith("facebook.com") and not host.startswith("mbasic."):
        return urlunsplit(("https", "mbasic.facebook.com", parts.path or "/", parts.query, ""))
    return url


def parent_hint(url: str) -> str | None:
    query = parse_qs(urlsplit(url).query)
    if "reply_comment_id" in query and query["reply_comment_id"][0]:
        return query["reply_comment_id"][0]
    if "replies" in url and query.get("comment_id", [""])[0]:
        return query["comment_id"][0]
    return None


def _html_comments(root: Elem, page_url: str, *, now: datetime, parent_id: str | None) -> list[ExtractedComment]:
    found: list[ExtractedComment] = []
    seen: set[str] = set()
    for element in root.descendants():
        if element.tag != "abbr":
            continue
        time_raw = element.text()
        if parse_time(time_raw, now) is None:
            continue
        container = _comment_container(element)
        if container is None:
            continue
        comment = _comment_from_container(container, time_raw, page_url, now, parent_id)
        if comment is not None and comment.external_id not in seen:
            seen.add(comment.external_id)
            found.append(comment)
    for element in root.descendants():
        if element.attrs.get("role") != "article":
            continue
        comment = _comment_from_article(element, page_url, now, parent_id)
        if comment is not None and comment.external_id not in seen:
            seen.add(comment.external_id)
            found.append(comment)
    return found


def _comment_container(abbr: Elem) -> Elem | None:
    current = abbr.parent
    for _ in range(8):
        if current is None:
            return None
        if (
            current.tag in {"div", "li", "article"}
            and _profile_anchor(current) is not None
            and _abbr_count(current) == 1
        ):
            return current
        current = current.parent
    return None


def _abbr_count(element: Elem) -> int:
    return sum(1 for item in element.descendants() if item.tag == "abbr")


def _profile_anchor(element: Elem) -> Elem | None:
    for item in element.descendants():
        if item.tag == "a" and _is_profile_link(item):
            return item
    return None


def _is_profile_link(element: Elem) -> bool:
    href = element.attrs.get("href", "")
    lowered = href.casefold()
    if any(token in lowered for token in ("/login", "login.php", "/checkpoint", "comment.php", "/ufi/", "story.php")):
        return False
    label = element.text()
    if not label or label.casefold() in _ACTION_LABELS:
        return False
    if "profile.php" in lowered or "/people/" in lowered or "/user/" in lowered:
        return True
    path = urlsplit(urljoin("https://mbasic.facebook.com/", href)).path.strip("/")
    return bool(path) and "/" not in path and path not in {"posts", "watch", "share", "groups", "reel", "reels"}


def _comment_from_container(
    container: Elem, time_raw: str, page_url: str, now: datetime, parent_id: str | None
) -> ExtractedComment | None:
    anchor = _profile_anchor(container)
    if anchor is None:
        return None
    author = anchor.text()
    body = _body_text(container, author, time_raw)
    if not body:
        return None
    author_id, author_url = _author_identity(anchor.attrs.get("href", ""))
    meta = abbr_parent_text(container, time_raw).replace(time_raw, " ")
    likes_raw, likes = _likes_in(meta)
    reply_count = _reply_count(container.text())
    external_id = _comment_identifier(container, page_url) or stable_id(author, body, time_raw, parent_id)
    return _make_comment(
        external_id=external_id,
        parent_id=parent_id,
        text=body,
        author=author,
        author_id=author_id,
        author_url=author_url,
        time_raw=time_raw,
        now=now,
        likes=likes,
        likes_raw=likes_raw,
        reply_count=reply_count,
    )


def abbr_parent_text(container: Elem, time_raw: str) -> str:
    for element in container.descendants():
        if element.tag == "abbr" and element.text() == time_raw and element.parent is not None:
            return element.parent.text()
    return time_raw


def _body_text(container: Elem, author: str, time_raw: str) -> str:
    best = ""
    for element in container.descendants():
        if element is container or element.tag in {"a", "abbr", "h3", "button"}:
            continue
        if any(item.tag == "abbr" for item in element.descendants() if item is not element):
            continue
        text = element.text()
        if not text or text == author or text.casefold() in _ACTION_LABELS:
            continue
        if time_raw and time_raw in text and len(text) < len(time_raw) + 12:
            continue
        if len(text) > len(best):
            best = text
    return best


def _comment_from_article(
    article: Elem, page_url: str, now: datetime, parent_id: str | None
) -> ExtractedComment | None:
    label = article.attrs.get("aria-label", "")
    match = _ARTICLE_LABEL.match(clean_text(label))
    if match is None:
        return None
    anchor = _profile_anchor(article)
    author = (match.group(1) or (anchor.text() if anchor else "")).strip()
    if not author:
        return None
    time_raw = ""
    for element in article.descendants():
        candidate = element.text() if element.tag in {"abbr", "span", "a"} else ""
        if candidate and parse_time(candidate, now) is not None and len(candidate) <= 40:
            time_raw = candidate
            break
    body = ""
    for element in article.descendants():
        if element.attrs.get("dir") == "auto":
            text = element.text()
            if text and text != author and len(text) > len(body):
                body = text
    if not body:
        body = _body_text(article, author, time_raw)
    if not body:
        return None
    author_id, author_url = _author_identity(anchor.attrs.get("href", "")) if anchor else (None, None)
    likes_raw, likes = _likes_in(article.text().replace(body, " ").replace(time_raw, " "))
    external_id = _comment_identifier(article, page_url) or stable_id(author, body, time_raw or None, parent_id)
    return _make_comment(
        external_id=external_id,
        parent_id=parent_id,
        text=body,
        author=author,
        author_id=author_id,
        author_url=author_url,
        time_raw=time_raw or None,
        now=now,
        likes=likes,
        likes_raw=likes_raw,
        reply_count=_reply_count(article.text()),
    )


def _make_comment(
    *,
    external_id: str,
    parent_id: str | None,
    text: str,
    author: str,
    author_id: str | None,
    author_url: str | None,
    time_raw: str | None,
    now: datetime,
    likes: int | None,
    likes_raw: str | None,
    reply_count: int | None,
) -> ExtractedComment:
    return ExtractedComment(
        external_id=external_id[:128],
        parent_external_id=None if parent_id is None else parent_id[:128],
        text=text[:8000],
        author=author[:300],
        author_id=None if author_id is None else author_id[:128],
        author_url=author_url,
        time=parse_time(time_raw, now),
        time_raw=None if time_raw is None else time_raw[:80],
        likes=likes,
        likes_raw=None if likes_raw is None else likes_raw[:32],
        reply_count=reply_count,
    )


def _author_identity(href: str) -> tuple[str | None, str | None]:
    if not href:
        return None, None
    parts = urlsplit(urljoin("https://www.facebook.com/", href))
    query = parse_qs(parts.query)
    author_id = query.get("id", [""])[0]
    if "profile.php" in parts.path and author_id:
        return author_id, f"https://www.facebook.com/profile.php?id={author_id}"
    path = parts.path.strip("/")
    if path and "/" not in path:
        return None, f"https://www.facebook.com/{path}"
    return None, None


def _comment_identifier(container: Elem, page_url: str) -> str | None:
    own_id = container.attrs.get("id", "")
    if own_id.startswith(("comment_", "ufi_")) and own_id not in {"ufi_"}:
        token = own_id.split("_", 1)[1]
        if (token and not token.isdigit()) or (token.isdigit() and len(token) >= 4):
            return token or own_id
    for element in container.descendants():
        if element.tag != "a":
            continue
        query = parse_qs(urlsplit(urljoin(page_url, element.attrs.get("href", ""))).query)
        for key in ("comment_id", "reply_comment_id", "ctoken"):
            if query.get(key, [""])[0]:
                return query[key][0]
    return None


def _likes_in(text: str) -> tuple[str | None, int | None]:
    for token in _LIKE_TOKEN.findall(text):
        value = parse_likes(token)
        if value is not None:
            return clean_text(token), value
    return None, None


def _reply_count(text: str) -> int | None:
    match = _REPLY_COUNT.search(text)
    return int(match.group(1)) if match else None


def _next_url(root: Elem, page_url: str) -> str | None:
    comments_url: str | None = None
    replies_url: str | None = None
    for element in root.descendants():
        if element.tag != "a":
            continue
        label = element.text()
        href = element.attrs.get("href", "").strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(page_url, href)
        if _is_login_url(absolute):
            continue
        if comments_url is None and _MORE_COMMENTS.match(label):
            comments_url = absolute
        elif replies_url is None and _MORE_REPLIES.match(label):
            replies_url = absolute
    return comments_url or replies_url


def _is_login_url(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    return "login" in path or "checkpoint" in path or path.startswith("/recover")


def _is_login_wall(html: str) -> bool:
    lowered = html.casefold()
    if any(phrase in lowered for phrase in _LOGIN_TEXT):
        return True
    return re.search(r"<form\b[^>]*\baction\s*=\s*['\"][^'\"]*login", lowered) is not None


def _is_unavailable(html: str) -> bool:
    lowered = html.casefold()
    return any(phrase in lowered for phrase in _UNAVAILABLE)


def _looks_like_post(html: str) -> bool:
    lowered = html.casefold()
    return any(
        token in lowered for token in ('id="ufi', "viết bình luận", "write a comment", "addcomment", "comment_form")
    )


def _reported_in_html(html: str) -> int | None:
    match = re.search(r"(\d[\d.]*)\s*(?:bình luận|comments)\b", html, re.IGNORECASE)
    if match is None:
        return None
    return parse_likes(match.group(1))


def _reported_count(node: dict[str, Any]) -> int | None:
    comments = node.get("comments")
    if isinstance(comments, dict) and isinstance(comments.get("total_count"), int):
        return int(comments["total_count"])
    feedback = node.get("feedback")
    if isinstance(feedback, dict):
        count = feedback.get("comment_count")
        if isinstance(count, dict) and isinstance(count.get("total_count"), int):
            return int(count["total_count"])
    return None


def _comment_from_json(node: dict[str, Any], now: datetime) -> ExtractedComment | None:
    typename = node.get("__typename")
    if isinstance(typename, str) and typename in _STORY_TYPES:
        return None
    body = node.get("body") if isinstance(node.get("body"), dict) else node.get("preferred_body")
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        return None
    author = node.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author.get("name"):
        return None
    created = node.get("created_time")
    legacy = node.get("legacy_fbid") or node.get("id")
    if typename not in _COMMENT_TYPES and created is None and not legacy:
        return None
    if typename not in _COMMENT_TYPES and created is None:
        return None
    text = clean_text(str(body["text"]))
    if not text:
        return None
    author_name = clean_text(str(author["name"]))
    author_id = author.get("id")
    author_url = author.get("url")
    time_raw = str(created) if isinstance(created, int | str) else None
    likes, likes_raw = _json_likes(node)
    parent = _json_parent(node)
    external = str(legacy or node.get("id") or stable_id(author_name, text, time_raw, parent))
    replies = _json_replies(node)
    return _make_comment(
        external_id=external,
        parent_id=parent,
        text=text,
        author=author_name,
        author_id=str(author_id) if isinstance(author_id, str | int) else None,
        author_url=author_url if isinstance(author_url, str) else None,
        time_raw=time_raw,
        now=now,
        likes=likes,
        likes_raw=likes_raw,
        reply_count=replies,
    )


def _json_likes(node: dict[str, Any]) -> tuple[int | None, str | None]:
    feedback = node.get("feedback")
    if isinstance(feedback, dict):
        reactors = feedback.get("reactors")
        if isinstance(reactors, dict):
            if isinstance(reactors.get("count"), int):
                return int(reactors["count"]), str(reactors["count"])
            reduced = reactors.get("count_reduced")
            if isinstance(reduced, str):
                return parse_likes(reduced), reduced
        if isinstance(feedback.get("like_count"), int):
            return int(feedback["like_count"]), str(feedback["like_count"])
    if isinstance(node.get("like_count"), int):
        return int(node["like_count"]), str(node["like_count"])
    return None, None


def _json_replies(node: dict[str, Any]) -> int | None:
    feedback = node.get("feedback")
    if not isinstance(feedback, dict):
        return None
    replies = feedback.get("replies") or feedback.get("reply_count")
    if isinstance(replies, dict) and isinstance(replies.get("count"), int):
        return int(replies["count"])
    if isinstance(replies, int):
        return replies
    return None


def _json_parent(node: dict[str, Any]) -> str | None:
    parent = node.get("parent_comment") or node.get("comment_parent")
    if isinstance(parent, dict) and parent.get("id") is not None:
        return str(parent["id"])
    return None
