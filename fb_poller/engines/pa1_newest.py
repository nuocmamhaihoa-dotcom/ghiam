"""Rank 5 — guest browser, Newest tab, first page only. Own browser, no shared pool."""

from __future__ import annotations

import re
from typing import Any

from fb_poller.engines.guard import invalid_limit, placeholder_text, public_error
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 5
NAME = "pa1-newest"

_NEWEST_LABELS = ("Newest", "Mới nhất", "Most recent")
_DROP_LINE = re.compile(
    r"^(?:"
    r"like|liked|comment|share|reply|follow|message|more|"
    r"thích|đã thích|bình luận|chia sẻ|trả lời|theo dõi|nhắn tin|thêm|"
    r"liked by \S.{0,80}|được thích bởi \S.{0,80}|thích bởi \S.{0,80}|"
    r"most relevant|newest|most recent|mới nhất|phù hợp nhất|"
    r"see more|see less|xem thêm|"
    r"write a comment(?:\.\.\.|…)?|viết bình luận(?:\.\.\.|…)?|"
    r"write a reply(?:\.\.\.|…)?|viết phản hồi(?:\.\.\.|…)?|"
    r"view more comments|xem thêm bình luận|"
    r"view \d+ more comments?|xem thêm \d+ bình luận|"
    r"hide \d+ replies|ẩn \d+ phản hồi|"
    r"reply to \S.{0,60}|trả lời \S.{0,60}|"
    r"comment as \S.{0,60}|reply as \S.{0,60}|bình luận với tư cách \S.{0,80}|"
    r"most relevant comments|newest comments|bình luận phù hợp nhất|bình luận mới nhất|"
    r"sponsored|được tài trợ|active now|đang hoạt động|"
    r"[·•]+|"
    r"all comments|tất cả bình luận|"
    r"top fan|rising fan|tác giả|author|admin|verified|đã xác minh|"
    r"just now|vừa xong|"
    r"yesterday(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]m)?)?|"
    r"hôm qua(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})?|"
    r"(?:today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]m)?|"
    r"hôm nay(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"thứ\s+(?:hai|ba|tư|năm|sáu|bảy|nhật)(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"translated by \S.{0,40}|được dịch bởi \S.{0,40}|"
    r"[·•]?\s*(?:about\s+|khoảng\s+)?(?:an?|one|\d+)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"(?:khoảng\s+)?\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"\d+\s*(?:s|m|h|d|w|y)\s*[·•]?|"
    r"\d+\s*(?:secs?|mins?|hrs?|seconds?|minutes?|hours?)|"
    r"\d+\s*(?:days?|weeks?|months?|years?)|"
    r"[·•]?\s*\d{1,2}:\d{2}(?:\s*[ap]m)?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]m)?)?|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:ngày\s+)?\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?|"
    r"\d{1,2}\s+thg\s+\d{1,2}(?:,\s*\d{4})?|"
    r"see \d+ more comments?|"
    r"see \d+ repl(?:y|ies)|xem \d+ phản hồi|"
    r"translated from \S.{0,40}|được dịch từ \S.{0,60}|"
    r"rate translation|đánh giá bản dịch|"
    r"love|haha|wow|sad|angry|care|yêu thích|buồn|phẫn nộ|"
    r"\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kmb]?)\s+(?:likes?|reactions?|comments?|shares?|lượt thích|bình luận|chia sẻ)|"
    r"view all \d+ replies|xem tất cả \d+ phản hồi|\d+ replies|\d+ phản hồi|"
    r"see translation|hide translation|xem bản dịch|ẩn bản dịch|"
    r"see original(?:\s*\([^)\n]{0,80}\))?|hide original(?:\s*\([^)\n]{0,80}\))?|"
    r"xem bản gốc(?:\s*\([^)\n]{0,80}\))?|ẩn bản gốc(?:\s*\([^)\n]{0,80}\))?|"
    r"edited(?:\s*[·•]\s*(?:\d+\s*[smhdwy]|\d{1,2}:\d{2}(?:\s*[ap]m)?))?|"
    r"đã chỉnh sửa(?:\s*[·•]\s*(?:\d+\s*[smhdwy]|\d{1,2}:\d{2}))?|"
    r"\d+\s*[smhdwy]\s*[·•]\s*(?:edited|đã chỉnh sửa)?"
    r")$",
    re.IGNORECASE,
)
_AUTHOR_STAMP = re.compile(
    r"(?:"
    r"\s*[·•]\s*(?:"
    r"just now|vừa xong|"
    r"yesterday(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]m)?)?|"
    r"hôm qua(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})?|"
    r"(?:today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]m)?|"
    r"hôm nay(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"thứ\s+(?:hai|ba|tư|năm|sáu|bảy|nhật)(?:\s+(?:lúc|vào)\s+\d{1,2}:\d{2})|"
    r"(?:about\s+|khoảng\s+)?(?:an?|one|\d+)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"(?:khoảng\s+)?\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"\d+\s*(?:s|m|h|d|w|y)|"
    r"\d+\s*(?:secs?|mins?|hrs?|seconds?|minutes?|hours?)|"
    r"\d+\s*(?:days?|weeks?|months?|years?)|"
    r"[·•]?\s*\d{1,2}:\d{2}(?:\s*[ap]m)?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[ap]m)?)?|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:ngày\s+)?\d{1,2}\s+tháng\s+\d{1,2}(?:\s+năm\s+\d{4})?|"
    r"\d{1,2}\s+thg\s+\d{1,2}(?:,\s*\d{4})?|"
    r"edited|đã chỉnh sửa|"
    r"top fan|rising fan|verified|đã xác minh|tác giả"
    r")"
    r")+$",
    re.IGNORECASE,
)
_COUNT_LINE = re.compile(
    r"^(?:\d{1,7}|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kmb])$",
    re.IGNORECASE,
)
_POST_LABELS = {"all reactions", "tất cả cảm xúc"}


def _is_post_block(block: str) -> bool:
    """The post article has a reaction label on its own line. A comment may mention the words."""
    for line in block.splitlines():
        label = line.strip().lower().rstrip(":").strip()
        if label in _POST_LABELS:
            return True
    return False


def comments_from_articles(blocks: list[str], limit: int) -> list[PublicComment]:
    """Turn visible article text into comments. Drops the post and UI chrome."""
    if limit < 1:
        return []
    found: list[PublicComment] = []
    seen: set[str] = set()
    for block in blocks:
        if _is_post_block(block):
            continue
        lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not _DROP_LINE.match(ln.strip())]
        if len(lines) > 2 and _COUNT_LINE.match(lines[-1]):
            lines.pop()
        if len(lines) < 2:
            continue
        author = _AUTHOR_STAMP.sub("", lines[0]).strip(" ·•")
        text = " ".join(lines[1:]).strip()
        if len(author) < 1 or len(text) < 1 or _DROP_LINE.match(author) or placeholder_text(text):
            continue
        key = f"{author}|{text[:80]}"
        if key in seen:
            continue
        seen.add(key)
        found.append(PublicComment(text=text[:2000], author_name=author))
        if len(found) >= limit:
            break
    return found


def _browser_proxy(proxy: str) -> dict[str, str] | None:
    if not proxy.strip():
        return None
    from fb_poller.workers.proxy_manager import parse_proxy_line

    parsed = parse_proxy_line(proxy)
    if parsed is None:
        raise ValueError("proxy must be host:port, host:port:user:pass, or a URL")
    return parsed.as_playwright()


async def _dismiss_gate(page: Any) -> None:
    for label in ("Close", "Đóng"):
        loc = page.get_by_role("button", name=label)
        if await loc.count():
            try:
                await loc.first.click(timeout=1500)
            except Exception:
                return


async def scan(
    url: str,
    *,
    limit: int = 20,
    timeout_ms: int = 60000,
    proxy: str = "",
    **_: object,
) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return EngineResult(NAME, RANK, False, error=f"playwright_missing: {exc}")

    try:
        async with async_playwright() as pw:
            launch: dict[str, Any] = {"headless": True}
            browser_proxy = _browser_proxy(proxy)
            if browser_proxy:
                launch["proxy"] = browser_proxy
            browser = await pw.chromium.launch(**launch)
            page = await browser.new_page(
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if resp and resp.status >= 400:
                await browser.close()
                return EngineResult(NAME, RANK, False, error=f"http_{resp.status}")
            await page.wait_for_timeout(2500)
            await _dismiss_gate(page)
            for label in _NEWEST_LABELS:
                loc = page.get_by_text(label, exact=True)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                    break
            texts = await page.locator('[role="article"]').all_inner_texts()
            await browser.close()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    comments = comments_from_articles(texts, limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_visible_comments",
        note="Newest first page only. Nested replies are not expanded.",
    )
