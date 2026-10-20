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
    "comment_by",
    "written_by",
    "posted_as",
    "display_label",
    "profile_caption",
    "business_name",
    "store_name",
    "seller_name",
    "host_name",
    "organizer_name",
    "page_title",
    "brand_name",
    "company_name",
    "venue_name",
    "artist_name",
    "moderator_name",
    "admin_name",
    "tác giả",
    "tac gia",
    "tên tác giả",
    "ten tac gia",
    "người đăng",
    "nguoi dang",
    "speaker_name",
    "page_owner",
    "channel_name",
    "người viết",
    "nguoi viet",
    "tên người bình luận",
    "ten nguoi binh luan",
    "commenter_display",
    "participant_name",
    "member_display",
    "author_caption",
    "profile_owner",
    "account_owner",
    "presenter_name",
    "host_display",
    "voice_name",
    "người gửi",
    "nguoi gui",
    "tên thành viên",
    "ten thanh vien",
    "họ tên",
    "ho ten",
    "display_author",
    "reply_author_name",
    "parent_author_name",
    "byline_name",
    "credit_name",
    "author_figure",
    "from_display",
    "người comment",
    "nguoi comment",
    "tên tài khoản",
    "ten tai khoan",
    "tác giả bình luận",
    "tac gia binh luan",
    "comment_author_name",
    "user_display",
    "poster_display",
    "sender_display",
    "creator_display",
    "actor_display",
    "người phản hồi",
    "nguoi phan hoi",
    "tên profile",
    "ten profile",
    "tên nick",
    "ten nick",
    "writer_display",
    "owner_display",
    "member_title",
    "author_string",
    "người trả lời",
    "nguoi tra loi",
    "tên khách",
    "ten khach",
    "tên fan",
    "ten fan",
    "replier_name",
    "responder_name",
    "answerer_name",
    "guest_name",
    "visitor_name",
    "tên người mua",
    "ten nguoi mua",
    "khách hàng",
    "khach hang",
    "người xem",
    "nguoi xem",
    "fan_name",
    "buyer_name",
    "customer_name",
    "shopper_name",
    "donor_name",
    "tên ứng viên",
    "ten ung vien",
    "nhà tuyển dụng",
    "nha tuyen dung",
    "người ứng tuyển",
    "nguoi ung tuyen",
    "applicant_name",
    "candidate_name",
    "employer_name",
    "recruiter_name",
    "matcher_name",
    "tên cửa hàng",
    "ten cua hang",
    "chủ quán",
    "chu quan",
    "người check in",
    "nguoi check in",
    "checkin_author",
    "place_owner",
    "business_owner",
    "venue_owner",
    "merchant_name",
    "người tổ chức",
    "nguoi to chuc",
    "người mời",
    "nguoi moi",
    "người tham gia",
    "nguoi tham gia",
    "event_organizer",
    "event_host",
    "invitee_name",
    "attendee_name",
    "người lưu",
    "nguoi luu",
    "người báo cáo",
    "nguoi bao cao",
    "người chặn",
    "nguoi chan",
    "page_manager",
    "collection_owner",
    "group_creator",
    "saver_name",
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
    "time_ms",
    "created_datetime",
    "comment_created",
    "post_created_at",
    "publish_datetime",
    "comment_datetime",
    "posted_datetime",
    "time_created_ms",
    "creation_ms",
    "fb_timestamp",
    "fb_time",
    "graph_time",
    "comment_ts",
    "posted_ts",
    "update_time",
    "thời gian tạo",
    "thoi gian tao",
    "ngày giờ",
    "ngay gio",
    "thời điểm đăng",
    "thoi diem dang",
    "fetched_at",
    "scraped_at",
    "imported_at",
    "server_time",
    "thời gian bình luận",
    "thoi gian binh luan",
    "ngày bình luận",
    "ngay binh luan",
    "giờ đăng",
    "gio dang",
    "collected_at",
    "export_time",
    "crawl_time",
    "harvest_time",
    "capture_time",
    "observed_at",
    "thời điểm tạo",
    "thoi diem tao",
    "lúc đăng",
    "luc dang",
    "thời gian comment",
    "thoi gian comment",
    "extracted_at",
    "parsed_at",
    "ingest_time",
    "synced_at",
    "thời gian thu thập",
    "thoi gian thu thap",
    "ngày comment",
    "ngay comment",
    "giờ bình luận",
    "gio binh luan",
    "harvested_at",
    "pulled_at",
    "retrieved_at",
    "snapshot_time",
    "batch_time",
    "thời gian crawl",
    "thoi gian crawl",
    "thời điểm comment",
    "thoi diem comment",
    "timestamp comment",
    "scraped_time",
    "crawl_at",
    "fetch_time",
    "import_time",
    "scan_time",
    "thời gian xuất",
    "thoi gian xuat",
    "ngày xuất",
    "ngay xuat",
    "giờ comment",
    "gio comment",
    "exported_at",
    "dumped_at",
    "archived_at",
    "indexed_at",
    "queued_at",
    "thời điểm xuất",
    "thoi diem xuat",
    "ngày thu thập",
    "ngay thu thap",
    "lúc comment",
    "luc comment",
    "listed_at",
    "mirrored_at",
    "copied_at",
    "backed_up_at",
    "thời gian đồng bộ",
    "thoi gian dong bo",
    "ngày đồng bộ",
    "ngay dong bo",
    "lúc thu thập",
    "luc thu thap",
    "synced_time",
    "replicated_at",
    "duplicated_at",
    "transferred_at",
    "moved_at",
    "thời gian check in",
    "thoi gian check in",
    "ngày check in",
    "ngay check in",
    "lúc ghé",
    "luc ghe",
    "geo_tagged_at",
    "located_at",
    "checked_in_at",
    "visited_at",
    "arrived_at",
    "thời gian sự kiện",
    "thoi gian su kien",
    "ngày sự kiện",
    "ngay su kien",
    "giờ bắt đầu",
    "gio bat dau",
    "event_time",
    "starts_at",
    "event_at",
    "thời gian lưu",
    "thoi gian luu",
    "ngày lưu",
    "ngay luu",
    "lúc báo cáo",
    "luc bao cao",
    "thời gian chặn",
    "thoi gian chan",
    "saved_at",
    "reported_at",
    "blocked_at",
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
