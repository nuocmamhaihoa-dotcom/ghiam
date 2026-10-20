"""Rank 3 — call one managed comments API. Provider is selected by env, not hardcoded secrets."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from fb_poller.engines.guard import (
    connection_rows,
    first_comment_id,
    first_person_name,
    invalid_limit,
    nested_text,
    public_error,
    timestamp_of,
    redact_text,
)
from fb_poller.engines.types import EngineResult, PublicComment

RANK = 3
NAME = "managed-api"
_DATASET_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _provider() -> str:
    return os.environ.get("MANAGED_PROVIDER", "generic").strip().lower() or "generic"


async def scan(url: str, *, limit: int = 50, timeout: float = 60.0, **_: object) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    provider = _provider()
    key = os.environ.get("MANAGED_API_KEY", "").strip()
    endpoint = os.environ.get("MANAGED_COMMENTS_URL", "").strip()
    if provider not in {"generic", "chocodata", "brightdata"}:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="unknown_provider",
            note="Set MANAGED_PROVIDER=generic|chocodata|brightdata and the matching key.",
        )
    if provider == "generic" and not endpoint:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_MANAGED_COMMENTS_URL",
            note="Set MANAGED_PROVIDER=generic|chocodata|brightdata and the matching key.",
        )
    if not key:
        return EngineResult(NAME, RANK, False, error="missing_MANAGED_API_KEY")

    if provider == "chocodata" and not endpoint:
        endpoint = "https://chocodata.com/api/v1/facebook/comments"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if provider == "brightdata":
                resp = await _bright_data(client, url, key, limit)
            else:
                resp = await client.get(
                    endpoint,
                    params={"url": url, "limit": limit, "api_key": key},
                    headers={"Authorization": f"Bearer {key}"},
                )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    if resp.status_code >= 400:
        return EngineResult(NAME, RANK, False, error=redact_text(f"http_{resp.status_code}: {resp.text}", 220))
    try:
        payload = resp.json()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    comments = _comments_from_json(payload, limit)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else "empty_provider_payload",
        note=f"Provider={provider}. This engine does not share clients with other ranks.",
    )


def bright_dataset_id() -> str | None:
    """Dataset id safe to send as one query parameter."""
    dataset = os.environ.get("BRIGHTDATA_DATASET_ID", "").strip()
    if _DATASET_ID.fullmatch(dataset):
        return dataset
    return None


async def _bright_data(
    client: httpx.AsyncClient,
    url: str,
    key: str,
    limit: int,
) -> httpx.Response:
    raw = os.environ.get("BRIGHTDATA_DATASET_ID", "").strip()
    dataset = bright_dataset_id()
    if not raw:
        raise RuntimeError("missing_BRIGHTDATA_DATASET_ID")
    if dataset is None:
        raise RuntimeError("invalid_BRIGHTDATA_DATASET_ID")
    return await client.post(
        "https://api.brightdata.com/datasets/v3/scrape",
        params={"dataset_id": dataset, "format": "json"},
        headers={"Authorization": f"Bearer {key}"},
        json=[{"url": url, "limit": limit}],
    )


def _comments_from_json(payload: Any, limit: int) -> list[PublicComment]:
    rows = _rows(payload)
    out: list[PublicComment] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        text, inner = _field_source(row)
        if not text or len(text) > 8000:
            continue
        author_keys = (
            "author",
            "author_name",
            "from",
            "profileName",
            "user_name",
            "commenter_name",
            "commenter",
            "name",
            "user",
            "profile",
            "owner",
            "owner_name",
            "display_name",
            "full_name",
            "screen_name",
            "short_name",
            "author_title",
            "user_full_name",
            "profile_url",
            "author_nickname",
            "commenter_full_name",
            "i18n_name",
            "alternate_name",
            "localized_name",
            "user_handle",
            "commenter_handle",
            "profile_handle",
            "author_sort_name",
            "page_name",
            "actor",
            "actor_name",
            "from_name",
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
            "speaker_name",
            "page_owner",
            "channel_name",
            "commenter_display",
            "participant_name",
            "member_display",
            "author_caption",
            "profile_owner",
            "account_owner",
            "presenter_name",
            "host_display",
            "voice_name",
            "display_author",
            "reply_author_name",
            "parent_author_name",
            "byline_name",
            "credit_name",
            "author_figure",
            "from_display",
            "comment_author_name",
            "user_display",
            "poster_display",
            "sender_display",
            "creator_display",
            "actor_display",
            "writer_display",
            "owner_display",
            "member_title",
            "author_string",
            "replier_name",
            "responder_name",
            "answerer_name",
            "guest_name",
            "visitor_name",
            "fan_name",
            "buyer_name",
            "customer_name",
            "shopper_name",
            "donor_name",
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
            "người ghi chú",
            "nguoi ghi chu",
            "người tạo mẫu",
            "nguoi tao mau",
            "người tham gia phòng",
            "nguoi tham gia phong",
            "note_author",
            "template_creator",
            "room_joiner",
            "note_rater",
            "người mua sắm",
            "nguoi mua sam",
            "người cổ vũ",
            "nguoi co vu",
            "người trong danh sách",
            "nguoi trong danh sach",
            "list_owner",
            "streamer_fan",
            "restricted_user",
            "shopper_name",
            "người giám sát",
            "nguoi giam sat",
            "người xác minh",
            "nguoi xac minh",
            "người song ca",
            "nguoi song ca",
            "publication_follower",
            "duet_partner",
            "family_supervisor",
            "verified_subscriber",
            "người đánh giá",
            "nguoi danh gia",
            "người trả hàng",
            "nguoi tra hang",
            "người làm quiz",
            "nguoi lam quiz",
            "quiz_author",
            "insights_viewer",
            "goal_setter",
            "reviewer_name",
            "người tạo avatar",
            "nguoi tao avatar",
            "người đề nghị",
            "nguoi de nghi",
            "người đặt trạng thái",
            "nguoi dat trang thai",
            "status_author",
            "offer_buyer",
            "membership_applicant",
            "avatar_owner",
            "người đăng Threads",
            "nguoi dang Threads",
            "người xem lại thẻ",
            "nguoi xem lai the",
            "người lên lịch live",
            "nguoi len lich live",
            "threads_author",
            "listing_reporter",
            "live_scheduler",
            "người quyên góp",
            "nguoi quyen gop",
            "người tạo quỹ",
            "nguoi tao quy",
            "người bình chọn",
            "nguoi binh chon",
            "người tạo thăm dò",
            "nguoi tao tham do",
            "người tưởng niệm",
            "nguoi tuong niem",
            "người gửi quà",
            "nguoi gui qua",
            "người chơi game",
            "nguoi choi game",
            "fundraiser_author",
            "poll_voter",
            "memorial_author",
            "gift_sender",
            "game_player",
            "người viết đánh giá",
            "nguoi viet danh gia",
            "người theo dõi đơn",
            "nguoi theo doi don",
            "người dùng bộ lọc",
            "nguoi dung bo loc",
            "người quét QR",
            "nguoi quet QR",
            "người khóa hồ sơ",
            "nguoi khoa ho so",
            "người đăng ký Meta",
            "nguoi dang ky Meta",
            "người hợp tác thương hiệu",
            "nguoi hop tac thuong hieu",
            "người lên lịch bài",
            "nguoi len lich bai",
            "wishlist_owner",
            "review_writer",
            "qr_scanner",
            "subscription_holder",
            "collab_applicant",
            "caption_editor",
            "filter_user",
            "profile_locker",
            "người tham gia sự kiện",
            "nguoi tham gia su kien",
            "người quan tâm sự kiện",
            "nguoi quan tam su kien",
            "người tổ chức sự kiện",
            "nguoi to chuc su kien",
            "người mua vé",
            "nguoi mua ve",
            "người xin vào nhóm",
            "nguoi xin vao nhom",
            "người hẹn hò",
            "nguoi hen ho",
            "người hiến máu",
            "nguoi hien mau",
            "người đánh dấu an toàn",
            "nguoi danh dau an toan",
            "event_goer",
            "ticket_buyer",
            "group_requester",
            "dating_matcher",
            "blood_donor",
            "safety_marker",
            "event_host",
            "climate_viewer",
            "người đưa đề nghị",
            "nguoi dua de nghi",
            "người bán marketplace",
            "nguoi ban marketplace",
            "người mua marketplace",
            "nguoi mua marketplace",
            "người ứng tuyển việc",
            "nguoi ung tuyen viec",
            "người lưu việc",
            "nguoi luu viec",
            "người thêm trường",
            "nguoi them truong",
            "người bạn chung",
            "nguoi ban chung",
            "offer_maker",
            "job_applicant",
            "mutual_friend",
            "listing_seller",
            "favorite_owner",
            "listing_buyer",
            "người tạo tin",
            "nguoi tao tin",
            "người tạo Reels",
            "nguoi tao Reels",
            "người dùng mẫu",
            "nguoi dung mau",
            "người gắn nhãn dán",
            "nguoi gan nhan dan",
            "người quản lý trang",
            "nguoi quan ly trang",
            "người trả lời hộp thư",
            "nguoi tra loi hop thu",
            "người bạn thân",
            "nguoi ban than",
            "story_creator",
            "reel_creator",
            "template_user",
            "sticker_user",
            "page_admin",
            "inbox_replier",
            "close_friend",
            "người tạo quảng cáo",
            "nguoi tao quang cao",
            "người đặt ngân sách",
            "nguoi dat ngan sach",
            "người tạo đối tượng",
            "nguoi tao doi tuong",
            "người xem thông báo",
            "nguoi xem thong bao",
            "người tìm kiếm",
            "nguoi tim kiem",
            "người tải dữ liệu",
            "nguoi tai du lieu",
            "người liên hệ di sản",
            "nguoi lien he di san",
            "ad_creator",
            "budget_setter",
            "audience_builder",
            "notif_viewer",
            "searcher",
            "data_downloader",
            "legacy_contact",
            "người xem sau",
            "nguoi xem sau",
            "người tạo playlist",
            "nguoi tao playlist",
            "người gửi sao",
            "nguoi gui sao",
            "người stream",
            "nguoi stream",
            "người tham gia giải",
            "nguoi tham gia giai",
            "người thách đấu",
            "nguoi thach dau",
            "người chơi instant",
            "nguoi choi instant",
            "watch_later_user",
            "playlist_owner",
            "star_buyer",
            "streamer",
            "tournament_player",
            "challenger",
            "instant_gamer",
            "người chỉnh trợ năng",
            "nguoi chinh tro nang",
            "người đổi ngôn ngữ",
            "nguoi doi ngon ngu",
            "người bật chế độ tối",
            "nguoi bat che do toi",
            "người thêm alt text",
            "nguoi them alt text",
            "người tham gia beta",
            "nguoi tham gia beta",
            "người tải lên lại",
            "nguoi tai len lai",
            "accessibility_user",
            "language_setter",
            "dark_mode_user",
            "alt_text_editor",
            "beta_tester",
            "upload_retrier",
            "offline_poster",
            "người hỏi Meta AI",
            "nguoi hoi Meta AI",
            "người tạo ảnh AI",
            "nguoi tao anh AI",
            "người viết lại AI",
            "nguoi viet lai AI",
            "người gắn nhãn AI",
            "nguoi gan nhan AI",
            "người thử đồ AI",
            "nguoi thu do AI",
            "meta_ai_user",
            "ai_image_creator",
            "ai_rewriter",
            "ai_labeler",
            "ai_try_on_user",
            "ai_video_creator",
        )
        author_name = first_person_name(row, author_keys) or (
            first_person_name(inner, author_keys) if inner else None
        )
        cid = first_comment_id(row) or (first_comment_id(inner) if inner else None)
        created = timestamp_of(row) or (timestamp_of(inner) if inner else None)
        key = cid or f"{author_name}|{text}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            PublicComment(
                text=text,
                author_name=author_name,
                comment_id=cid,
                created_time=created,
            )
        )
        if len(out) >= limit:
            break
    return out


def _field_text(row: dict[str, Any]) -> str | None:
    """Read one text field. A dict in ``text`` must not hide a later string ``message``."""
    text, _inner = _field_source(row)
    return text


def _field_source(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Text, plus the nested object that held it when the field itself is an object."""
    for key in (
        "text",
        "message",
        "comment",
        "comment_text",
        "commentBody",
        "body",
        "bodyText",
        "preferred_body",
        "content",
        "message_text",
        "plain_body",
        "plaintext",
        "comment_message",
        "body_html",
    ):
        value = row.get(key)
        found = nested_text(value)
        if found:
            inner = value if isinstance(value, dict) else None
            return found, inner
    return None, None


def _own_text(row: dict[str, Any]) -> bool:
    return bool(_field_text(row))


def _rows(payload: Any, depth: int = 0) -> list[Any]:
    """Keep a comment and the replies nested under it. An empty list does not hide a later list."""
    if depth > 6:
        return []
    if isinstance(payload, list):
        out: list[Any] = []
        for item in payload:
            unwrapped = connection_rows([item])
            out.extend(_rows(unwrapped[0] if unwrapped else item, depth + 1))
        return out
    if not isinstance(payload, dict):
        return []
    nested: list[Any] = []
    for key in (
        "comments",
        "replies",
        "comment_list",
        "commentList",
        "comment_thread",
        "comments_connection",
        "top_level_comments",
        "thread",
        "comment_edge",
        "feedback",
        "data",
        "results",
        "result",
        "items",
        "payload",
        "wrapper",
    ):
        value = payload.get(key)
        linked = connection_rows(value)
        if linked:
            nested.extend(_rows(linked, depth + 1))
        elif isinstance(value, (list, dict)):
            nested.extend(_rows(value, depth + 1))
    if _own_text(payload):
        return [payload, *nested]
    return nested
