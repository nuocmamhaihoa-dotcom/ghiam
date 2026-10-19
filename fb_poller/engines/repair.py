"""Reapply every comment-parse repair when the same defect shows up again.

Engines stay independent. This module is not an engine. ``scan_one`` runs
``heal_result`` on every result, including a crash, so a regression in one
parser is corrected before the table is printed.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from urllib.parse import unquote

from fb_poller.engines.guard import (
    comment_id,
    first_comment_id,
    first_person_name,
    nested_text,
    placeholder_text,
    redact_text,
    scalar_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

# Codes the healer can emit. Each one corresponds to a defect already seen
# or a close variant of one (chrome glued back on, a short duplicate, a
# stringified JSON row, a secret in an error, a bool id, an HTML entity).
REPAIR_CODES = (
    "normalize_text",
    "unescape_text",
    "strip_chrome_lines",
    "drop_placeholder",
    "cap_text",
    "expand_json_text",
    "clear_bool_id",
    "clear_chrome_author",
    "dedupe_comment_id",
    "prefer_longer_text",
    "recover_comments_from_error",
    "redact_error",
    "strip_glued_chrome",
    "strip_markup",
    "decode_percent",
    "comments_win",
    "clear_null_time",
)

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CHROME_LINE = re.compile(
    r"^(?:"
    r"like|comment|share|reply|thích|bình luận|chia sẻ|trả lời|"
    r"most relevant|newest|most recent|mới nhất|phù hợp nhất|"
    r"see more|see less|xem thêm|"
    r"write a comment\.\.\.|write a comment…|write a comment|"
    r"viết bình luận\.\.\.|viết bình luận…|viết bình luận|"
    r"all comments|tất cả bình luận|"
    r"top fan|rising fan|tác giả|author|"
    r"just now|vừa xong|yesterday|hôm qua|"
    r"[·•]?\s*\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|"
    r"\d+\s*(?:s|m|h|d|w|y)|"
    r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[kmb]|"
    r"\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm)(?:\s+trước)?|"
    r"\d+(?:\.\d+)?\s*[kmb]?\s+(?:likes?|reactions?|lượt thích)|"
    r"view all \d+ replies|xem tất cả \d+ phản hồi|\d+ replies|\d+ phản hồi|"
    r"see translation|hide translation|xem bản dịch|ẩn bản dịch|"
    r"edited|đã chỉnh sửa|"
    r"all reactions:?|tất cả cảm xúc:?"
    r")$",
    re.IGNORECASE,
)
_BOOL_ID = {"true", "false", "none", "null"}
_GRAPHQL_PREFIX = re.compile(r"^\s*for\s*\(\s*;;\s*\)\s*;\s*")
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_BREAK = re.compile(r"(?i)<br\s*/?>")
_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")
_GLUED_CHROME = re.compile(
    r"(?:\s+(?:"
    r"see translation|hide translation|xem bản dịch|ẩn bản dịch|"
    r"see more|see less|xem thêm|"
    r"view all \d+ replies|xem tất cả \d+ phản hồi|"
    r"top fan|rising fan|"
    r"just now|vừa xong|"
    r"edited|đã chỉnh sửa"
    r"))+$",
    re.IGNORECASE,
)
_CHROME_AUTHOR = {
    "like",
    "reply",
    "share",
    "top fan",
    "rising fan",
    "author",
    "tác giả",
    "thích",
    "trả lời",
    "chia sẻ",
}


def heal_result(result: EngineResult) -> EngineResult:
    """Correct a scan result in place of the caller. Safe to call twice."""
    comments, codes = heal_comments(result.comments)
    error = result.error
    if error:
        redacted = redact_text(error)
        if redacted != error:
            codes.append("redact_error")
        error = redacted
    if not comments and error:
        recovered = _recover_from_error(result.error or error)
        if recovered:
            comments, extra = heal_comments(recovered)
            codes.extend(extra)
            codes.append("recover_comments_from_error")
            error = None
    ok = result.ok
    if comments:
        ok = True
        if error:
            codes.append("comments_win")
            error = None
    elif result.ok:
        ok = False
        error = error or "empty_after_repair"
    note = result.note
    unique = _unique(codes)
    if unique:
        suffix = "repaired:" + ",".join(unique)
        note = f"{note} {suffix}".strip() if note else suffix
    return EngineResult(
        engine=result.engine,
        rank=result.rank,
        ok=ok,
        comments=comments,
        error=error,
        note=note,
    )


def heal_comments(comments: list[PublicComment]) -> tuple[list[PublicComment], list[str]]:
    """Drop chrome, expand stringified rows, and keep the longer copy of one id."""
    staged: list[PublicComment] = []
    codes: list[str] = []
    for comment in comments:
        expanded = _expand_json_text(comment.text)
        if expanded is None:
            staged.append(comment)
            continue
        codes.append("expand_json_text")
        for item in expanded:
            staged.append(
                PublicComment(
                    text=item.text,
                    author_name=item.author_name or comment.author_name,
                    comment_id=item.comment_id or comment.comment_id,
                    created_time=item.created_time or comment.created_time,
                )
            )
    cleaned: list[PublicComment] = []
    for comment in staged:
        text, text_codes = _clean_text(comment.text)
        codes.extend(text_codes)
        if text is None:
            continue
        author, author_codes = _clean_author(comment.author_name)
        codes.extend(author_codes)
        cid, id_codes = _clean_id(comment.comment_id)
        codes.extend(id_codes)
        created, time_codes = _clean_time(comment.created_time)
        codes.extend(time_codes)
        cleaned.append(
            PublicComment(
                text=text,
                author_name=author,
                comment_id=cid,
                created_time=created,
            )
        )
    merged, merge_codes = _dedupe(cleaned)
    codes.extend(merge_codes)
    return merged, _unique(code for code in codes if code in REPAIR_CODES)


def _clean_text(text: str) -> tuple[str | None, list[str]]:
    codes: list[str] = []
    cleaned = text.replace("\u00a0", " ")
    without_hidden = _ZERO_WIDTH.sub("", cleaned)
    without_hidden = _CONTROLS.sub("", without_hidden)
    if without_hidden != cleaned:
        codes.append("normalize_text")
    cleaned = without_hidden
    if " " not in cleaned and _PERCENT.search(cleaned):
        decoded = unquote(cleaned)
        if decoded != cleaned:
            codes.append("decode_percent")
            cleaned = decoded
    decoded = html.unescape(html.unescape(cleaned))
    unescaped = decoded.replace("\u00a0", " ")
    if decoded != cleaned:
        codes.append("unescape_text")
    if unescaped != decoded:
        codes.append("normalize_text")
    cleaned = unescaped
    marked = _TAG.sub("", _BREAK.sub("\n", cleaned))
    if marked != cleaned:
        codes.append("strip_markup")
    cleaned = marked
    kept: list[str] = []
    removed_chrome = False
    removed_glued = False
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        unglued = _GLUED_CHROME.sub("", stripped).strip()
        if unglued != stripped:
            removed_glued = True
        stripped = unglued
        if not stripped:
            continue
        if _CHROME_LINE.match(stripped):
            removed_chrome = True
            continue
        kept.append(stripped)
    if removed_glued:
        codes.append("strip_glued_chrome")
    if removed_chrome:
        codes.append("strip_chrome_lines")
    cleaned = "\n".join(kept).strip()
    if not cleaned or placeholder_text(cleaned):
        codes.append("drop_placeholder")
        return None, codes
    if len(cleaned) > 8000:
        cleaned = cleaned[:8000]
        codes.append("cap_text")
    return cleaned, codes


def _clean_author(author: str | None) -> tuple[str | None, list[str]]:
    text = scalar_text(author)
    if text is None:
        return None, []
    visible = _ZERO_WIDTH.sub("", html.unescape(text)).replace("\u00a0", " ").strip()
    codes: list[str] = []
    if visible != text.strip():
        codes.append("normalize_text")
    if not visible:
        return None, codes
    if visible.lower() in _CHROME_AUTHOR or _CHROME_LINE.match(visible):
        codes.append("clear_chrome_author")
        return None, codes
    return visible, codes


def _clean_id(value: str | None) -> tuple[str | None, list[str]]:
    cid = comment_id(value)
    if cid is None:
        return None, []
    if cid.lower() in _BOOL_ID:
        return None, ["clear_bool_id"]
    return cid, []


def _clean_time(value: str | None) -> tuple[str | None, list[str]]:
    text = scalar_text(value)
    if text is None:
        return None, []
    if text.lower() in _BOOL_ID:
        return None, ["clear_null_time"]
    return text, []


def _json_payload(text: str) -> object | None:
    stripped = _GRAPHQL_PREFIX.sub("", text.strip())
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _expand_json_text(text: str) -> list[PublicComment] | None:
    payload = _json_payload(text)
    if payload is None:
        return None
    rows = _loose_comments(payload)
    if not rows:
        return None
    bare = _GRAPHQL_PREFIX.sub("", text.strip())
    if len(rows) == 1 and rows[0].text == bare:
        return None
    return rows


def _recover_from_error(error: str) -> list[PublicComment]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(error):
        if char not in "{[":
            continue
        try:
            payload, _end = decoder.raw_decode(error[index:])
        except json.JSONDecodeError:
            continue
        rows = _loose_comments(payload)
        if rows:
            return rows
    return []


def _loose_comments(payload: object, depth: int = 0) -> list[PublicComment]:
    if depth > 6:
        return []
    if isinstance(payload, list):
        found: list[PublicComment] = []
        for item in payload:
            found.extend(_loose_comments(item, depth + 1))
        return found
    if not isinstance(payload, dict):
        return []
    rendered = None
    for key in ("text", "message", "comment", "body"):
        rendered = nested_text(payload.get(key))
        if rendered:
            break
    own: list[PublicComment] = []
    if rendered and not placeholder_text(rendered):
        own.append(
            PublicComment(
                text=rendered,
                author_name=first_person_name(
                    payload,
                    ("author", "author_name", "from", "profileName", "user_name", "name"),
                ),
                comment_id=first_comment_id(payload),
                created_time=scalar_text(payload.get("created_time"))
                or scalar_text(payload.get("created_at"))
                or scalar_text(payload.get("date")),
            )
        )
    nested: list[PublicComment] = []
    for key in ("comments", "replies", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, (list, dict)):
            nested.extend(_loose_comments(value, depth + 1))
    return own + nested


def _dedupe(comments: list[PublicComment]) -> tuple[list[PublicComment], list[str]]:
    chosen: dict[str, PublicComment] = {}
    order: list[str] = []
    codes: list[str] = []
    for comment in comments:
        key = f"id:{comment.comment_id}" if comment.comment_id else f"body:{comment.author_name}|{comment.text}"
        current = chosen.get(key)
        if current is None:
            chosen[key] = comment
            order.append(key)
            continue
        if len(comment.text) > len(current.text):
            author = comment.author_name or current.author_name
            created = comment.created_time or current.created_time
            chosen[key] = PublicComment(comment.text, author, comment.comment_id, created)
            codes.append("prefer_longer_text")
        else:
            codes.append("dedupe_comment_id")
    return [chosen[key] for key in order], codes


def _unique(codes: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return seen
