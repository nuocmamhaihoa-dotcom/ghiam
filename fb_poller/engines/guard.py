"""Small helpers shared by engines. Not a scan engine."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

_PROXY_USERINFO = re.compile(r"://[^\s/@:]+:[^\s/@]+@")
_QUERY_SECRET = re.compile(r"(?i)(token|access_token|api_key|apikey|key)=([^&\s]+)")
_JSON_SECRET = re.compile(
    r"""(?i)(['"](?:access_token|api_key|apikey|token|password|secret|authorization)['"]\s*:\s*['"])([^'"]*)(['"])"""
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_COMMENT_TYPE = re.compile(r'"__typename"\s*:\s*"(?:Comment|FeedbackComment|XFBComment)"')


def redact_text(text: str, limit: int = 300) -> str:
    """Remove proxy passwords and API tokens from text that may be printed."""
    cleaned = _PROXY_USERINFO.sub("://*:*@", text)
    cleaned = _QUERY_SECRET.sub(r"\1=*", cleaned)
    cleaned = _JSON_SECRET.sub(r"\1*\3", cleaned)
    cleaned = _BEARER.sub(r"\1*", cleaned)
    return cleaned[:limit]


def public_error(exc: BaseException, limit: int = 300) -> str:
    """Error text safe to print. Proxy passwords and API tokens are removed."""
    return redact_text(f"{type(exc).__name__}: {exc}", limit)


def invalid_limit(limit: int) -> bool:
    return limit < 1


def is_comment_typename(value: object) -> bool:
    """True for comment objects. A count or composer that merely contains the word is not one."""
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return bool(lowered) and lowered.endswith("comment")


def placeholder_text(text: str) -> bool:
    """Composer hints, whether they end with an ellipsis or three dots."""
    normalized = text.strip().lower().replace("…", "...").rstrip(".")
    return normalized in {
        "write a comment",
        "write a public comment",
        "viết bình luận",
        "viết bình luận công khai",
    }


def comment_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+\.0+", text):
            return text.split(".", 1)[0]
        return text or None
    return None


def first_comment_id(row: dict[str, object]) -> str | None:
    """The first real id. A bool ``id`` must not hide ``legacy_fbid``."""
    folded = {fold_key(key): value for key, value in row.items()}
    for key in (
        "id",
        "legacy_fbid",
        "legacy_comment_id",
        "comment_legacy_id",
        "comment_id",
        "comment_fbid",
        "feedback_id",
        "comment_key",
        "fb_comment_id",
        "cid",
        "reply_id",
        "graph_comment_id",
        "comment_graph_id",
        "reply_fbid",
        "legacy_reply_id",
        "comment_reply_id",
        "root_comment_id",
        "top_level_comment_id",
        "comment_object_id",
        "thread_comment_id",
        "edge_comment_id",
        "node_comment_id",
        "object_fbid",
        "fb_object_id",
        "feedback_comment_id",
        "story_comment_id",
        "comment_node_id",
        "ugc_comment_id",
        "legacy_id",
        "messaging_comment_id",
        "graph_comment_fbid",
        "comment_xt",
        "xt_id",
        "comment_xid",
        "fb_feedback_id",
        "interaction_id",
        "engagement_id",
        "comment_pk",
        "comment_uuid",
        "fb_cid",
        "social_comment_id",
        "comment_sid",
        "external_comment_id",
        "source_comment_id",
        "remote_comment_id",
        "platform_comment_id",
        "provider_comment_id",
        "upstream_comment_id",
        "origin_comment_id",
        "place_comment_id",
        "location_comment_id",
        "checkin_comment_id",
        "venue_comment_id",
        "event_comment_id",
        "invite_comment_id",
        "rsvp_comment_id",
        "save_comment_id",
        "report_comment_id",
        "block_comment_id",
        "watch_comment_id",
        "listing_comment_id",
        "group_comment_id",
        "order_comment_id",
        "effect_comment_id",
        "payment_comment_id",
        "creator_comment_id",
        "collab_comment_id",
        "draft_comment_id",
        "boost_comment_id",
        "inbox_comment_id",
        "promo_comment_id",
    ):
        found = _id_value(folded.get(fold_key(key)))
        if found:
            return comment_id_from_url(found) or found
    for key in ("comment_url", "permalink_url", "permalink", "url", "link"):
        found = comment_id_from_url(folded.get(fold_key(key)))
        if found:
            return found
    return None


def _id_value(value: object, depth: int = 0) -> str | None:
    """A string id, or the id inside one nested id object."""
    found = comment_id(value)
    if found:
        return found
    if depth > 1 or not isinstance(value, dict):
        return None
    lowered = {fold_key(key): item for key, item in value.items()}
    for key in (
        "id",
        "legacy_fbid",
        "legacy_comment_id",
        "comment_legacy_id",
        "comment_id",
        "comment_fbid",
        "feedback_id",
        "comment_key",
        "fb_comment_id",
        "cid",
        "reply_id",
        "graph_comment_id",
        "comment_graph_id",
        "reply_fbid",
        "legacy_reply_id",
        "comment_reply_id",
        "root_comment_id",
        "top_level_comment_id",
        "comment_object_id",
        "thread_comment_id",
        "edge_comment_id",
        "node_comment_id",
        "object_fbid",
        "fb_object_id",
        "feedback_comment_id",
        "story_comment_id",
        "comment_node_id",
        "ugc_comment_id",
        "legacy_id",
        "messaging_comment_id",
        "graph_comment_fbid",
        "comment_xt",
        "xt_id",
        "comment_xid",
        "fb_feedback_id",
        "interaction_id",
        "engagement_id",
        "comment_pk",
        "comment_uuid",
        "fb_cid",
        "social_comment_id",
        "comment_sid",
        "external_comment_id",
        "source_comment_id",
        "remote_comment_id",
        "platform_comment_id",
        "provider_comment_id",
        "upstream_comment_id",
        "origin_comment_id",
        "place_comment_id",
        "location_comment_id",
        "checkin_comment_id",
        "venue_comment_id",
        "event_comment_id",
        "invite_comment_id",
        "rsvp_comment_id",
        "save_comment_id",
        "report_comment_id",
        "block_comment_id",
        "watch_comment_id",
        "listing_comment_id",
        "group_comment_id",
        "order_comment_id",
        "effect_comment_id",
        "payment_comment_id",
        "creator_comment_id",
        "collab_comment_id",
        "draft_comment_id",
        "boost_comment_id",
        "inbox_comment_id",
        "promo_comment_id",
    ):
        folded_key = fold_key(key)
        if folded_key not in lowered:
            continue
        found = _id_value(lowered.get(folded_key), depth + 1)
        if found:
            return found
    return None


_COMMENT_IN_URL = re.compile(r"(?:comment_id|reply_comment_id)=([A-Za-z0-9_]+)")
_REPLY_IN_URL = re.compile(r"reply_comment_id=([A-Za-z0-9_]+)")


def comment_id_from_url(value: object) -> str | None:
    """The comment id in a permalink. A reply id wins when both are present."""
    text = scalar_text(value)
    if text is None or "://" not in text:
        return None
    decoded = unquote(text)
    reply = _REPLY_IN_URL.search(decoded)
    if reply:
        return comment_id(reply.group(1))
    match = _COMMENT_IN_URL.search(decoded)
    if match is None:
        return None
    return comment_id(match.group(1))


_PROFILE_HOST = re.compile(r"(?:^|\.)(?:facebook|fb)\.com$", re.IGNORECASE)
_PROFILE_BLOCKED = {
    "posts",
    "photos",
    "videos",
    "reel",
    "reels",
    "watch",
    "groups",
    "share",
    "login.php",
    "story.php",
    "permalink.php",
    "photo.php",
    "profile.php",
    "people",
}


def _profile_slug(value: str) -> str | None:
    """A single vanity slug from a Facebook profile URL."""
    if "://" not in value:
        return None
    path = unquote(value.split("?", 1)[0].split("#", 1)[0]).rstrip("/")
    parts = [part for part in path.split("/") if part]
    host_at = None
    for index, part in enumerate(parts):
        if _PROFILE_HOST.search(part):
            host_at = index
            break
    if host_at is None:
        return None
    slug_parts = parts[host_at + 1 :]
    if len(slug_parts) != 1:
        return None
    slug = slug_parts[0]
    if not slug or slug.isdigit() or slug.lower() in _PROFILE_BLOCKED:
        return None
    if re.fullmatch(r"[A-Za-z0-9.\-]+", slug) is None:
        return None
    return slug


def person_name(value: object, depth: int = 0) -> str | None:
    """A display name, or the name inside an author object."""
    if depth > 2:
        return None
    plain = scalar_text(value)
    if plain:
        if depth < 2 and plain.startswith("{") and plain.endswith("}"):
            try:
                loaded = json.loads(plain)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, (dict, list)):
                found = person_name(loaded, depth + 1)
                if found:
                    return found
        vanity = _profile_slug(plain)
        if vanity:
            return vanity
        return plain
    if isinstance(value, list):
        for item in value:
            found = person_name(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, dict):
        lowered = {fold_key(key): item for key, item in value.items()}
        for key in (
            "name",
            "short_name",
            "full_name",
            "profile_name",
            "username",
            "display_name",
            "nickname",
            "screen_name",
            "actor",
            "page_name",
            "owner_name",
            "author_title",
            "user_full_name",
            "author_nickname",
            "commenter_full_name",
            "i18n_name",
            "alternate_name",
            "localized_name",
            "structured_name",
            "commenter_name",
            "sort_name",
            "author_sort_name",
            "actor_name",
            "from_name",
            "author_name",
            "commenter",
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
            "user_handle",
            "handle",
            "commenter_alias",
            "author_handle",
            "profile_alias",
            "account_alias",
            "display_alias",
            "poster_alias",
            "user_nickname",
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
            "speaker_name",
            "page_owner",
            "channel_name",
            "tên tác giả",
            "ten tac gia",
            "người đăng",
            "nguoi dang",
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
            "người xem",
            "nguoi xem",
            "người bán hàng",
            "nguoi ban hang",
            "người mua hàng",
            "nguoi mua hang",
            "group_manager",
            "playlist_owner",
            "watchlater_owner",
            "viewer_name",
            "người đặt hàng",
            "nguoi dat hang",
            "người thanh toán",
            "nguoi thanh toan",
            "người tạo hiệu ứng",
            "nguoi tao hieu ung",
            "effect_creator",
            "sticker_sender",
            "order_buyer",
            "payer_name",
            "người sáng tạo",
            "nguoi sang tao",
            "người hợp tác",
            "nguoi hop tac",
            "người kiếm tiền",
            "nguoi kiem tien",
            "brand_partner",
            "voiceover_author",
            "draft_owner",
            "creator_name",
            "người quảng cáo",
            "nguoi quang cao",
            "người quản lý trang",
            "nguoi quan ly trang",
            "người gửi tin nhắn",
            "nguoi gui tin nhan",
            "page_promoter",
            "alt_text_editor",
            "inbox_agent",
            "advertiser_name",
        ):
            token = fold_key(key)
            if token not in lowered:
                continue
            child = lowered.get(token)
            found = person_name(child, depth + 1) or nested_text(child)
            if found:
                return found
        given = person_name(lowered.get("firstname"), depth + 1) or person_name(lowered.get("givenname"), depth + 1)
        middle = person_name(lowered.get("middlename"), depth + 1)
        family = person_name(lowered.get("lastname"), depth + 1) or person_name(lowered.get("familyname"), depth + 1)
        parts = [part for part in (given, middle, family) if part]
        if parts:
            return " ".join(parts)
    return None


def fold_key(key: object) -> str:
    """Compare headers ignoring case, spaces, and underscores."""
    return "".join(ch for ch in str(key).strip().lower() if ch.isalnum())


def first_person_name(row: dict[str, object], keys: tuple[str, ...]) -> str | None:
    """Try each author field. An object without a name must not hide the next field."""
    folded = {fold_key(key): value for key, value in row.items()}
    for key in keys:
        found = person_name(folded.get(fold_key(key)))
        if found:
            return found
    return None


def scalar_text(value: object) -> str | None:
    """Keep plain names and timestamps. Do not stringify objects."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def time_text(value: object, depth: int = 0) -> str | None:
    """A timestamp string or number, including one wrapped as text or timestamp."""
    if depth > 2:
        return None
    plain = scalar_text(value)
    if plain:
        return plain
    if isinstance(value, dict):
        folded = {fold_key(key): item for key, item in value.items()}
        for key in (
            "text",
            "message",
            "timestamp",
            "time",
            "date",
            "relative",
            "created_time",
            "creation_time",
            "created_at",
            "created",
        ):
            token = fold_key(key)
            if token not in folded:
                continue
            found = time_text(folded.get(token), depth + 1)
            if found:
                return found
    return None


def nested_text(value: object, depth: int = 0) -> str | None:
    """Plain comment text, or the text inside a nested message or body object.

    Call this on one known field (``text``, ``message``, ``body``). Do not pass
    a whole comment row: a wrapper ``content`` key can be the post, not the comment.
    """
    if depth > 3:
        return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, list):
                return None
            if isinstance(item, dict):
                if any(
                    key in item
                    for key in ("author", "from", "author_name", "id", "comment_id", "commentId", "legacy_fbid")
                ):
                    return None
            found = nested_text(item, depth + 1)
            if found:
                parts.append(found)
        return " ".join(parts) if parts else None
    if isinstance(value, dict):
        for key in (
            "text",
            "message",
            "comment",
            "body",
            "content",
            "commentBody",
            "comment_body",
            "bodyText",
            "body_text",
            "message_text",
            "plain_body",
            "plaintext",
            "comment_message",
            "body_html",
        ):
            if key not in value:
                continue
            found = nested_text(value.get(key), depth + 1)
            if found:
                return found
    return None


def timestamp_of(row: object) -> str | None:
    """A timestamp field on a row. The comment message is not a timestamp."""
    if not isinstance(row, dict):
        return None
    folded = {fold_key(key): value for key, value in row.items()}
    for key in (
        "created_time",
        "creation_time",
        "created_at",
        "created",
        "comment_time",
        "updated_time",
        "posted_at",
        "published_at",
        "date_created",
        "posted_time",
        "comment_create_time",
        "creation_timestamp",
        "created_time_seconds",
        "created_time_ms",
        "created_seconds",
        "publish_time",
        "published_time",
        "comment_created_at",
        "commented_at",
        "comment_timestamp",
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
        "datetime",
        "date",
        "time",
        "timestamp",
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
        "thời gian xem",
        "thoi gian xem",
        "ngày xem",
        "ngay xem",
        "lúc lưu video",
        "luc luu video",
        "thời gian danh sách phát",
        "thoi gian danh sach phat",
        "watched_at",
        "queued_at",
        "thời gian đặt hàng",
        "thoi gian dat hang",
        "ngày thanh toán",
        "ngay thanh toan",
        "ordered_at",
        "paid_at",
        "effect_created_at",
        "thời gian bản nháp",
        "thoi gian ban nhap",
        "ngày kiếm tiền",
        "ngay kiem tien",
        "drafted_at",
        "monetized_at",
        "collab_at",
        "thời gian quảng cáo",
        "thoi gian quang cao",
        "ngày boost",
        "ngay boost",
        "boosted_at",
        "promoted_at",
        "captioned_at",
    ):
        found = time_text(folded.get(fold_key(key)))
        if found:
            return found
    return None


def text_carrier(row: dict[str, object]) -> dict[str, object] | None:
    """A nested object that holds the comment text together with its author and id."""
    for key in ("comment", "commentText", "comment_text", "message", "body", "text"):
        value = row.get(key)
        if isinstance(value, dict) and nested_text(value):
            return value
    return None


def connection_rows(value: object) -> list[object]:
    """A list, ``{data: [...]}``, ``{edges: [{node: ...}]}``, or one ``{node: ...}`` edge."""
    if isinstance(value, list):
        return [_unwrap_node(item) for item in value]
    if not isinstance(value, dict):
        return []
    for key in ("data", "edges", "nodes"):
        child = value.get(key)
        if isinstance(child, list) and child:
            return [_unwrap_node(item) for item in child]
    node = value.get("node")
    if isinstance(node, dict):
        return [_unwrap_node(value)]
    return []


def _unwrap_node(item: object) -> object:
    """An edge is ``{node, cursor}``. A row that already has comment text stays itself."""
    if not isinstance(item, dict):
        return item
    node = item.get("node")
    if not isinstance(node, dict):
        return item
    for key in ("text", "message", "comment", "comment_text", "commentText", "commentBody", "body", "bodyText", "preferred_body"):
        if nested_text(item.get(key)):
            return item
    return node


def objects_with_comments(html: str, max_hits: int = 80) -> list[Any]:
    """Decode JSON objects that contain a comment typename, even late in a large script."""
    if max_hits < 1:
        return []
    chunks = _SCRIPT.findall(html) or [html]
    decoder = json.JSONDecoder()
    found: list[Any] = []
    seen: set[tuple[int, int]] = set()
    hits = 0
    for chunk_index, chunk in enumerate(chunks):
        for match in _COMMENT_TYPE.finditer(chunk):
            if hits >= max_hits:
                return found
            hits += 1
            start = max(0, match.start() - 20000)
            window = chunk[start:]
            relative = match.start() - start
            positions = [index for index, char in enumerate(window[:relative]) if char == "{"]
            # The opening brace can sit behind dozens of small objects (ranges, tracking).
            for pos in reversed(positions[-250:]):
                absolute = start + pos
                if (chunk_index, absolute) in seen:
                    break
                try:
                    obj, end = decoder.raw_decode(window, pos)
                except json.JSONDecodeError:
                    continue
                if end <= relative or not isinstance(obj, (dict, list)):
                    continue
                seen.add((chunk_index, absolute))
                found.append(obj)
                break
    return found
