"""Mở bài viết Facebook công khai và đọc comment trang đang trả về, không đăng nhập."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from commentscope_agent.facebook import (
    ExtractedComment,
    is_facebook_url,
    parent_hint,
    parse_html,
    parse_json_text,
    to_mbasic,
)

_MAX_JSON_CHARS = 5_000_000


class PageLike(Protocol):
    url: str

    def on(self, event: str, handler: Any) -> None: ...

    async def goto(self, url: str, **kwargs: Any) -> Any: ...

    async def content(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ReadOptions:
    max_comments: int = 200
    include_replies: bool = True
    max_replies_per_comment: int = 20
    time_budget_sec: float = 180
    max_pages: int = 6
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReadResult:
    outcome: str
    detail: str
    comments: tuple[ExtractedComment, ...]
    comments_reported: int | None
    complete: bool
    stop_reason: str
    pages: int
    final_url: str | None = None
    blocked_url: str | None = field(default=None)

    def payloads(self) -> list[dict[str, Any]]:
        return [comment.as_payload() for comment in self.comments]


async def read_public_facebook(page: PageLike, url: str, options: ReadOptions | None = None) -> ReadResult:
    """Đọc comment từ trang đã mở. `url` là permalink; máy cục bộ dùng nguyên URL để thử."""

    options = options or ReadOptions()
    started = time.monotonic()
    deadline = started + options.time_budget_sec
    now = options.now or datetime.now().astimezone()
    blobs: list[str] = []
    pending: list[asyncio.Task[None]] = []

    def on_response(response: Any) -> None:
        pending.append(asyncio.create_task(_read_response(response, blobs)))

    page.on("response", on_response)
    first = to_mbasic(url) if is_facebook_url(url) else url
    targets = [first]
    if first != url and is_facebook_url(url):
        targets.append(url)

    comments: list[ExtractedComment] = []
    seen: set[str] = set()
    reported: int | None = None
    pages = 0
    stop_reason = "exhausted"
    complete = False
    saw_post = False
    blocked_detail = (
        "Facebook yêu cầu đăng nhập hoặc kiểm tra bảo mật. CommentScope không đăng nhập và không giải captcha."
    )
    current = targets.pop(0)
    tried_www = current == url

    while pages < options.max_pages and len(comments) < options.max_comments:
        if time.monotonic() > deadline:
            stop_reason = "time_budget"
            break
        try:
            await page.goto(current, wait_until="domcontentloaded")
        except Exception as exc:
            if comments:
                stop_reason = "error"
                break
            return ReadResult(
                "failed",
                _short(exc),
                (),
                reported,
                False,
                "error",
                pages,
                getattr(page, "url", None),
            )
        pages += 1
        if pending:
            await asyncio.gather(*pending)
            pending.clear()
        final_url = page.url or current
        if _blocked_url(final_url):
            if comments:
                stop_reason = "login_wall"
                break
            return ReadResult("blocked", blocked_detail, (), reported, False, "login_wall", pages, final_url, final_url)
        html = await page.content()
        for blob in blobs:
            extra, extra_reported = parse_json_text(blob, now=now)
            reported = _max_reported(reported, extra_reported)
            _add(comments, seen, extra, options)
        blobs.clear()
        parsed = parse_html(html, final_url, now=now, parent_id=parent_hint(final_url))
        reported = _max_reported(reported, parsed.comments_reported)
        if parsed.kind == "blocked" and not comments:
            return ReadResult("blocked", blocked_detail, (), reported, False, "login_wall", pages, final_url, final_url)
        if parsed.kind == "not_available" and not comments:
            return ReadResult(
                "not_available",
                "Bài viết không còn công khai hoặc không tồn tại",
                (),
                reported,
                True,
                "unavailable",
                pages,
                final_url,
            )
        if parsed.kind == "comments":
            saw_post = True
        _add(comments, seen, parsed.comments, options)
        if len(comments) >= options.max_comments:
            stop_reason = "max_comments"
            break
        if parsed.kind == "unknown" and not comments and targets and not tried_www:
            current = targets.pop(0)
            tried_www = True
            continue
        if parsed.next_url is None or _blocked_url(parsed.next_url):
            complete = parsed.next_url is None and parsed.kind == "comments"
            stop_reason = "exhausted" if complete else "login_wall"
            if parsed.kind == "unknown" and not saw_post:
                return ReadResult(
                    "failed",
                    "Không đọc được comment: trang không có phần bình luận công khai nhận ra được",
                    (),
                    reported,
                    False,
                    "unknown_layout",
                    pages,
                    final_url,
                )
            break
        current = parsed.next_url
    else:
        if len(comments) >= options.max_comments:
            stop_reason = "max_comments"
        elif pages >= options.max_pages:
            stop_reason = "max_pages"

    trimmed = comments[: options.max_comments]
    if len(comments) > len(trimmed):
        stop_reason = "max_comments"
        complete = False
    detail = f"Đọc được {len(trimmed)} comment công khai"
    if not complete:
        detail += f", dừng vì {stop_reason}"
    return ReadResult("done", detail, tuple(trimmed), reported, complete, stop_reason, pages, page.url)


def _add(
    comments: list[ExtractedComment],
    seen: set[str],
    incoming: tuple[ExtractedComment, ...] | list[ExtractedComment],
    options: ReadOptions,
) -> None:
    replies: dict[str, int] = {}
    for comment in comments:
        if comment.parent_external_id:
            replies[comment.parent_external_id] = replies.get(comment.parent_external_id, 0) + 1
    for comment in incoming:
        if comment.external_id in seen:
            continue
        if comment.parent_external_id and not options.include_replies:
            continue
        if comment.parent_external_id:
            used = replies.get(comment.parent_external_id, 0)
            if used >= options.max_replies_per_comment:
                continue
            replies[comment.parent_external_id] = used + 1
        seen.add(comment.external_id)
        comments.append(comment)


async def _read_response(response: Any, blobs: list[str]) -> None:
    try:
        headers = response.headers
        content_type = headers.get("content-type", "") if isinstance(headers, dict) else ""
        if "json" not in content_type and "javascript" not in content_type:
            return
        text = await response.text()
    except Exception:
        return
    if text and len(text) <= _MAX_JSON_CHARS:
        blobs.append(text)


def _blocked_url(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    return "login" in path or "checkpoint" in path


def _max_reported(current: int | None, extra: int | None) -> int | None:
    if extra is None:
        return current
    return extra if current is None else max(current, extra)


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:300]
