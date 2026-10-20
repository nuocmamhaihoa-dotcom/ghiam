"""Rank 8 — read an Octoparse export. The desktop app stays outside this process."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fb_poller.engines.guard import (
    first_comment_id,
    first_person_name,
    fold_key,
    invalid_limit,
    nested_text,
    public_error,
    time_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 8
NAME = "octoparse"

_TEXT_KEYS = (
    "text",
    "comment",
    "comment_text",
    "content",
    "message",
    "body",
    "nội dung",
    "binh luan",
    "bình luận",
    "plaintext",
    "comment_message",
    "body_html",
    "nội dung bình luận",
    "noi dung binh luan",
    "nội dung comment",
    "noi dung comment",
    "text comment",
    "comment body",
    "comment body text",
    "nội dung gốc",
    "noi dung goc",
    "message_text",
    "plain_body",
)
_AUTHOR_KEYS = (
    "author",
    "author_name",
    "profileName",
    "profile",
    "name",
    "user",
    "user_name",
    "username",
    "commenter",
    "commenter_name",
    "display_name",
    "page_name",
    "tên trang",
    "ten trang",
    "nickname",
    "screen_name",
    "owner_name",
    "display_name",
    "full_name",
    "screen_name",
    "short_name",
    "họ và tên",
    "ho va ten",
    "người bình luận",
    "nguoi binh luan",
    "author_title",
    "user_full_name",
    "author_nickname",
    "commenter_full_name",
    "i18n_name",
    "alternate_name",
    "biệt danh",
    "biet danh",
    "tên người dùng",
    "ten nguoi dung",
    "user_handle",
    "commenter_handle",
    "profile_handle",
    "tên hiển thị",
    "ten hien thi",
    "from_name",
    "actor_name",
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
    "publisher_name",
    "account_name",
    "profile_display",
    "user_caption",
    "byline",
    "signed_by",
    "handle_name",
    "display_handle",
    "credit",
    "contributor_name",
    "member_name",
    "poster_title",
    "user_alias",
    "alias",
    "author_alias",
    "posted_by",
    "writer",
    "account_title",
    "comment_owner",
    "author_display",
    "person_name",
    "handle",
    "commenter_alias",
    "author_handle",
    "profile_alias",
    "preferred_name",
    "real_name",
    "signed_name",
    "credited_to",
    "owner_label",
    "public_name",
    "visible_name",
    "shown_name",
    "legal_name",
    "stage_name",
    "pen_name",
    "label_name",
    "tác giả",
    "tac gia",
)
_TIME_KEYS = (
    "time",
    "date",
    "created_time",
    "created_at",
    "created",
    "comment_time",
    "creation_time",
    "updated_time",
    "posted_at",
    "published_at",
    "thời gian",
    "thoi gian",
    "ngày đăng",
    "ngay dang",
    "date_created",
    "posted_time",
    "comment_create_time",
    "thời gian đăng",
    "thoi gian dang",
    "ngày",
    "ngay",
    "time_text",
    "thời điểm",
    "thoi diem",
    "created_time_seconds",
    "created_seconds",
    "created_ms",
    "ngày tạo",
    "ngay tao",
    "comment_timestamp",
    "published_time",
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
    "written_at",
    "commented_time",
    "message_date",
    "post_time",
    "created_ms",
    "authored_at",
    "submitted_at",
    "unix_ms",
    "recorded_at",
    "event_time",
    "occurred_at",
    "epoch_ms",
    "created_timestamp",
    "time_posted",
    "published_on",
    "comment_created_time",
    "unix_time",
    "created_at_ms",
    "posted_on",
    "comment_time_ms",
    "created_on",
    "date_posted",
    "publish_date",
    "written_time",
    "message_timestamp",
    "activity_time",
    "posted_timestamp",
    "comment_unix",
    "created_date",
    "post_date",
    "commented_on",
    "left_at",
    "wrote_at",
    "updated_at",
    "ts_ms",
    "epoch",
    "unix",
    "said_at",
)


def comments_from_export(path: Path, limit: int = 500) -> list[PublicComment]:
    if limit < 1:
        return []
    raw = path.read_text(encoding="utf-8-sig")
    stripped = raw.lstrip()
    if path.suffix.lower() == ".json" or stripped.startswith(("{", "[")):
        try:
            return _from_json(json.loads(raw), limit)
        except json.JSONDecodeError:
            return _from_json_lines(raw, limit)
    if stripped.startswith("{") is False and "\n{" in raw:
        lined = _from_json_lines(raw, limit)
        if lined:
            return lined
    return _from_csv(raw, limit)


def _from_json_lines(raw: str, limit: int) -> list[PublicComment]:
    rows: list[object] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _from_json(rows, limit)


def _from_json(payload: object, limit: int, depth: int = 0) -> list[PublicComment]:
    if depth > 6 or limit < 1:
        return []
    if isinstance(payload, list):
        out: list[PublicComment] = []
        for row in payload:
            out.extend(_from_json(row, limit - len(out), depth + 1))
            if len(out) >= limit:
                break
        return out
    if not isinstance(payload, dict):
        return []
    out = []
    item = _from_mapping({str(k): v for k, v in payload.items()})
    if item:
        out.append(item)
    if len(out) >= limit:
        return out
    for key in ("comments", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, (list, dict)) and value:
            out.extend(_from_json(value, limit - len(out), depth + 1))
            if len(out) >= limit:
                break
    return out


def _delimiter(header: str) -> str:
    """Count separators outside quotes. A comma inside a header cell is not the delimiter."""
    semicolons = 0
    commas = 0
    tabs = 0
    quoted = False
    for char in header:
        if char == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == ";":
            semicolons += 1
        elif char == ",":
            commas += 1
        elif char == "\t":
            tabs += 1
    if tabs > commas and tabs > semicolons:
        return "\t"
    return ";" if semicolons > commas else ","


def _from_csv(raw: str, limit: int) -> list[PublicComment]:
    if not raw.strip():
        return []
    header = raw.splitlines()[0]
    delimiter = _delimiter(header)
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    out: list[PublicComment] = []
    for row in reader:
        item = _from_mapping(row)
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _pick_time(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    folded = {fold_key(key): value for key, value in row.items()}
    for key in keys:
        found = time_text(folded.get(fold_key(key)))
        if found:
            return found
    return None


def _pick(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    folded = {fold_key(key): value for key, value in row.items()}
    for key in keys:
        text = nested_text(folded.get(fold_key(key)))
        if text:
            return text
    return None


def _from_mapping(row: dict[str, object]) -> PublicComment | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    text = _pick(lowered, _TEXT_KEYS)
    if not text:
        return None
    return PublicComment(
        text=text,
        author_name=first_person_name(lowered, _AUTHOR_KEYS),
        comment_id=first_comment_id(lowered),
        created_time=_pick_time(lowered, _TIME_KEYS),
    )


async def scan(url: str = "", *, limit: int = 500, export_path: str = "", **_: object) -> EngineResult:
    del url  # Octoparse already fetched; this engine only imports the file.
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    if not export_path:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_export_file",
            note="Export CSV or JSON from Octoparse, then pass --file.",
        )
    path = Path(export_path)
    if not path.is_file():
        return EngineResult(NAME, RANK, False, error=f"file_not_found: {path.name}")
    try:
        comments = comments_from_export(path, limit=limit)
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "no_rows",
        note="Imported a local Octoparse export. This process does not drive the desktop app.",
    )
