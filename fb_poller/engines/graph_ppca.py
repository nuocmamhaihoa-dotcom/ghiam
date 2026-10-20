"""Rank 6 — official Graph API for public Page posts (Page Public Content Access)."""

from __future__ import annotations

import os
import re
from urllib.parse import unquote

import httpx

from fb_poller.engines.guard import (
    first_comment_id,
    invalid_limit,
    connection_rows,
    nested_text,
    first_person_name,
    person_name,
    text_carrier,
    public_error,
    redact_text,
    scalar_text,
    timestamp_of,
)
from fb_poller.engines.types import EngineResult, PublicComment

_VERSION = re.compile(r"^v\d+\.\d+$")
_OBJECT_ID = re.compile(r"^[A-Za-z0-9_]{1,200}$")

RANK = 6
NAME = "graph-ppca"


def graph_version() -> str:
    return os.environ.get("GRAPH_VERSION", "v21.0").strip() or "v21.0"


def access_token() -> str:
    return os.environ.get("GRAPH_ACCESS_TOKEN", "").strip()


def accepted_object_id(value: object) -> str | None:
    """Graph path segment. Only a plain id is inserted into the comments URL."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if _OBJECT_ID.match(text):
        return text
    return None


def comments_from_graph_payload(data: object, limit: int) -> tuple[list[PublicComment], str | None]:
    """Read a Graph comments payload. A bare list is accepted. Comments win over an error object."""
    if limit < 1:
        return [], "invalid_limit"
    if isinstance(data, list):
        rows: list[object] = data
    elif isinstance(data, dict):
        rows = _graph_rows(data)
        if not rows and "error" in data:
            err = data.get("error")
            message = err.get("message") if isinstance(err, dict) else err
            return [], redact_text(str(message), 300)
    else:
        return [], "unexpected_graph_payload"
    comments: list[PublicComment] = []
    _append_rows(rows, comments, set(), limit, 0)
    if comments:
        return comments, None
    return [], "empty_comment_edge"


def _append_rows(
    rows: list[object],
    comments: list[PublicComment],
    seen: set[str],
    limit: int,
    depth: int,
) -> None:
    """Keep a comment and replies wrapped as ``comments: {data: [...]}``."""
    if depth > 6 or len(comments) >= limit:
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        inner = text_carrier(row)
        text = (
            nested_text(row.get("message"))
            or nested_text(row.get("text"))
            or nested_text(row.get("body"))
            or nested_text(row.get("preferred_body"))
            or nested_text(row.get("content"))
            or nested_text(row.get("commentBody"))
            or nested_text(row.get("bodyText"))
            or nested_text(row.get("message_text"))
            or nested_text(row.get("plain_body"))
            or nested_text(row.get("plaintext"))
            or nested_text(row.get("comment_message"))
            or nested_text(row.get("body_html"))
        )
        from_inner = False
        if text is None and inner is not None:
            text = nested_text(inner)
            from_inner = text is not None
        if text:
            author_name = (
                person_name(row.get("from"))
                or person_name(row.get("author"))
                or first_person_name(
                    row,
                    (
                        "owner_name",
                        "commenter_name",
                        "profileName",
                        "userName",
                        "display_name",
                        "full_name",
                        "screen_name",
                        "author_title",
                        "user_full_name",
                        "from_name",
                        "actor_name",
                        "poster_name",
                        "owner_full_name",
                        "user_display_name",
                        "writer_name",
                        "display_title",
                        "profile_title",
                        "sender_name",
                        "entity_name",
                        "commenter_title",
                        "creator_name",
                        "owner_title",
                        "user_label",
                        "publisher_name",
                        "account_name",
                        "profile_display",
                        "byline",
                        "signed_by",
                        "handle_name",
                        "contributor_name",
                        "member_name",
                        "poster_title",
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
                        "người dùng âm thanh",
                        "nguoi dung am thanh",
                        "người cắt video",
                        "nguoi cat video",
                        "người thêm hiệu ứng",
                        "nguoi them hieu ung",
                        "người dùng phông xanh",
                        "nguoi dung phong xanh",
                        "người làm đẹp",
                        "nguoi lam dep",
                        "audio_user",
                        "trim_editor",
                        "effect_downloader",
                        "green_screen_user",
                        "beauty_user",
                        "layout_editor",
                        "người viết ghi chú",
                        "nguoi viet ghi chu",
                        "người nghe podcast",
                        "nguoi nghe podcast",
                        "người tạo album",
                        "nguoi tao album",
                        "người tạo bộ sưu tập",
                        "nguoi tao bo suu tap",
                        "người lưu bài",
                        "nguoi luu bai",
                        "note_author",
                        "podcast_listener",
                        "album_creator",
                        "collection_saver",
                        "người gửi thẻ quà",
                        "nguoi gui the qua",
                        "người tham gia chat cộng đồng",
                        "nguoi tham gia chat cong dong",
                        "người bật chế độ yên tĩnh",
                        "nguoi bat che do yen tinh",
                        "người giám sát",
                        "nguoi giam sat",
                        "gift_sender",
                        "chat_member",
                        "quiet_mode_user",
                        "supervisor",
                        "parent_dashboard_user",
                        "người chia sẻ WhatsApp",
                        "nguoi chia se WhatsApp",
                        "người mở Instagram",
                        "nguoi mo Instagram",
                        "người collab",
                        "nguoi collab",
                        "người chỉnh phụ đề",
                        "nguoi chinh phu de",
                        "người dùng mẫu",
                        "nguoi dung mau",
                        "whatsapp_sharer",
                        "instagram_opener",
                        "collaborator",
                        "caption_editor",
                        "template_user",
                        "người phê duyệt đăng nhập",
                        "nguoi phe duyet dang nhap",
                        "người đăng ký bản tin",
                        "nguoi dang ky ban tin",
                        "người gắn thẻ",
                        "nguoi gan the",
                        "người xem tình bạn",
                        "nguoi xem tinh ban",
                        "login_approver",
                        "newsletter_subscriber",
                        "tag_reviewer",
                        "friendship_viewer",
                        "security_key_user",
                        "người quản lý Meta Verified",
                        "nguoi quan ly Meta Verified",
                        "người ghim thông báo",
                        "nguoi ghim thong bao",
                        "người mời vào nhóm",
                        "nguoi moi vao nhom",
                        "người gửi hoa hồng",
                        "nguoi gui hoa hong",
                        "meta_verified_manager",
                        "announcement_pinner",
                        "group_inviter",
                        "rose_sender",
                        "anonymous_poster",
                        "người thêm vào túi",
                        "nguoi them vao tui",
                        "người bắt đầu cuộc gọi",
                        "nguoi bat dau cuoc goi",
                        "người tìm điểm bỏ phiếu",
                        "nguoi tim diem bo phieu",
                        "người xem giải đấu",
                        "nguoi xem giai dau",
                        "bag_adder",
                        "call_starter",
                        "polling_finder",
                        "spectate_user",
                        "monetization_manager",
                        "người chọn nhạc",
                        "nguoi chon nhac",
                        "người chỉnh chữ",
                        "nguoi chinh chu",
                        "người chọn nhãn dán",
                        "nguoi chon nhan dan",
                        "người xem thời tiết",
                        "nguoi xem thoi tiet",
                        "music_picker",
                        "text_editor",
                        "sticker_shopper",
                        "weather_viewer",
                        "work_account_switcher",
                        "người thêm sự kiện cuộc sống",
                        "nguoi them su kien cuoc song",
                        "người nhắn người bán",
                        "nguoi nhan nguoi ban",
                        "người xem bộ sưu tập kỹ thuật số",
                        "nguoi xem bo suu tap ky thuat so",
                        "người thêm lựa chọn khảo sát",
                        "nguoi them lua chon khao sat",
                        "life_event_adder",
                        "seller_messenger",
                        "collectible_viewer",
                        "poll_option_adder",
                        "guest_inviter",
                        "người đặt quản trị viên",
                        "nguoi dat quan tri vien",
                        "người gửi tin nhắn thoại",
                        "nguoi gui tin nhan thoai",
                        "người xem tất cả cảm xúc",
                        "nguoi xem tat ca cam xuc",
                        "người dịch bài viết",
                        "nguoi dich bai viet",
                        "group_admin_maker",
                        "voice_sender",
                        "reactions_viewer",
                        "post_translator",
                        "group_follower",
                        "người ghim trang cá nhân",
                        "nguoi ghim trang ca nhan",
                        "người gắn thẻ sản phẩm",
                        "nguoi gan the san pham",
                        "người tiếp tục xem",
                        "nguoi tiep tuc xem",
                        "người mở hộp thư hỗ trợ",
                        "nguoi mo hop thu ho tro",
                        "profile_pinner",
                        "product_tagger",
                        "continue_watcher",
                        "support_inbox_user",
                        "brand_content_creator",
                        "người tạm ẩn bài",
                        "nguoi tam an bai",
                        "người xem thư viện quảng cáo",
                        "nguoi xem thu vien quang cao",
                        "người tổ chức xem chung",
                        "nguoi to chuc xem chung",
                        "người quản lý hoạt động",
                        "nguoi quan ly hoat dong",
                        "feed_snoozer",
                        "ad_library_viewer",
                        "watch_party_host",
                        "activity_manager",
                        "friend_request_viewer",
                        "người xem kỷ niệm",
                        "nguoi xem ky niem",
                        "người quản lý thùng rác",
                        "nguoi quan ly thung rac",
                        "người tạo nội dung thương hiệu",
                        "nguoi tao noi dung thuong hieu",
                        "người tải danh bạ",
                        "nguoi tai danh ba",
                        "memory_viewer",
                        "trash_manager",
                        "branded_content_partner",
                        "contact_uploader",
                        "người gây quỹ",
                        "nguoi gay quy",
                        "người ứng phó khủng hoảng",
                        "nguoi ung pho khung hoang",
                        "người khu phố",
                        "nguoi khu pho",
                        "người giám sát phụ huynh",
                        "nguoi giam sat phu huynh",
                        "fundraiser_donor",
                        "crisis_responder",
                        "neighborhood_member",
                        "parental_supervisor",
                    ),
                )
            )
            if author_name is None and from_inner and inner is not None:
                author_name = (
                    person_name(inner.get("from"))
                    or person_name(inner.get("author"))
                    or first_person_name(
                        inner,
                        (
                            "owner_name",
                            "commenter_name",
                            "profileName",
                            "userName",
                            "display_name",
                            "full_name",
                            "screen_name",
                            "from_name",
                            "actor_name",
                            "poster_name",
                            "owner_full_name",
                            "writer_name",
                            "display_title",
                            "sender_name",
                            "entity_name",
                            "creator_name",
                            "owner_title",
                            "publisher_name",
                            "account_name",
                            "byline",
                            "signed_by",
                            "contributor_name",
                            "member_name",
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
                            "người dùng âm thanh",
                            "nguoi dung am thanh",
                            "người cắt video",
                            "nguoi cat video",
                            "người thêm hiệu ứng",
                            "nguoi them hieu ung",
                            "người dùng phông xanh",
                            "nguoi dung phong xanh",
                            "người làm đẹp",
                            "nguoi lam dep",
                            "audio_user",
                            "trim_editor",
                            "effect_downloader",
                            "green_screen_user",
                            "beauty_user",
                            "layout_editor",
                            "người viết ghi chú",
                            "nguoi viet ghi chu",
                            "người nghe podcast",
                            "nguoi nghe podcast",
                            "người tạo album",
                            "nguoi tao album",
                            "người tạo bộ sưu tập",
                            "nguoi tao bo suu tap",
                            "người lưu bài",
                            "nguoi luu bai",
                            "note_author",
                            "podcast_listener",
                            "album_creator",
                            "collection_saver",
                            "người gửi thẻ quà",
                            "nguoi gui the qua",
                            "người tham gia chat cộng đồng",
                            "nguoi tham gia chat cong dong",
                            "người bật chế độ yên tĩnh",
                            "nguoi bat che do yen tinh",
                            "người giám sát",
                            "nguoi giam sat",
                            "gift_sender",
                            "chat_member",
                            "quiet_mode_user",
                            "supervisor",
                            "parent_dashboard_user",
                            "người chia sẻ WhatsApp",
                            "nguoi chia se WhatsApp",
                            "người mở Instagram",
                            "nguoi mo Instagram",
                            "người collab",
                            "nguoi collab",
                            "người chỉnh phụ đề",
                            "nguoi chinh phu de",
                            "người dùng mẫu",
                            "nguoi dung mau",
                            "whatsapp_sharer",
                            "instagram_opener",
                            "collaborator",
                            "caption_editor",
                            "template_user",
                            "người phê duyệt đăng nhập",
                            "nguoi phe duyet dang nhap",
                            "người đăng ký bản tin",
                            "nguoi dang ky ban tin",
                            "người gắn thẻ",
                            "nguoi gan the",
                            "người xem tình bạn",
                            "nguoi xem tinh ban",
                            "login_approver",
                            "newsletter_subscriber",
                            "tag_reviewer",
                            "friendship_viewer",
                            "security_key_user",
                            "người quản lý Meta Verified",
                            "nguoi quan ly Meta Verified",
                            "người ghim thông báo",
                            "nguoi ghim thong bao",
                            "người mời vào nhóm",
                            "nguoi moi vao nhom",
                            "người gửi hoa hồng",
                            "nguoi gui hoa hong",
                            "meta_verified_manager",
                            "announcement_pinner",
                            "group_inviter",
                            "rose_sender",
                            "anonymous_poster",
                            "người thêm vào túi",
                            "nguoi them vao tui",
                            "người bắt đầu cuộc gọi",
                            "nguoi bat dau cuoc goi",
                            "người tìm điểm bỏ phiếu",
                            "nguoi tim diem bo phieu",
                            "người xem giải đấu",
                            "nguoi xem giai dau",
                            "bag_adder",
                            "call_starter",
                            "polling_finder",
                            "spectate_user",
                            "monetization_manager",
                            "người chọn nhạc",
                            "nguoi chon nhac",
                            "người chỉnh chữ",
                            "nguoi chinh chu",
                            "người chọn nhãn dán",
                            "nguoi chon nhan dan",
                            "người xem thời tiết",
                            "nguoi xem thoi tiet",
                            "music_picker",
                            "text_editor",
                            "sticker_shopper",
                            "weather_viewer",
                            "work_account_switcher",
                            "người thêm sự kiện cuộc sống",
                            "nguoi them su kien cuoc song",
                            "người nhắn người bán",
                            "nguoi nhan nguoi ban",
                            "người xem bộ sưu tập kỹ thuật số",
                            "nguoi xem bo suu tap ky thuat so",
                            "người thêm lựa chọn khảo sát",
                            "nguoi them lua chon khao sat",
                            "life_event_adder",
                            "seller_messenger",
                            "collectible_viewer",
                            "poll_option_adder",
                            "guest_inviter",
                            "người đặt quản trị viên",
                            "nguoi dat quan tri vien",
                            "người gửi tin nhắn thoại",
                            "nguoi gui tin nhan thoai",
                            "người xem tất cả cảm xúc",
                            "nguoi xem tat ca cam xuc",
                            "người dịch bài viết",
                            "nguoi dich bai viet",
                            "group_admin_maker",
                            "voice_sender",
                            "reactions_viewer",
                            "post_translator",
                            "group_follower",
                            "người ghim trang cá nhân",
                            "nguoi ghim trang ca nhan",
                            "người gắn thẻ sản phẩm",
                            "nguoi gan the san pham",
                            "người tiếp tục xem",
                            "nguoi tiep tuc xem",
                            "người mở hộp thư hỗ trợ",
                            "nguoi mo hop thu ho tro",
                            "profile_pinner",
                            "product_tagger",
                            "continue_watcher",
                            "support_inbox_user",
                            "brand_content_creator",
                            "người tạm ẩn bài",
                            "nguoi tam an bai",
                            "người xem thư viện quảng cáo",
                            "nguoi xem thu vien quang cao",
                            "người tổ chức xem chung",
                            "nguoi to chuc xem chung",
                            "người quản lý hoạt động",
                            "nguoi quan ly hoat dong",
                            "feed_snoozer",
                            "ad_library_viewer",
                            "watch_party_host",
                            "activity_manager",
                            "friend_request_viewer",
                            "người xem kỷ niệm",
                            "nguoi xem ky niem",
                            "người quản lý thùng rác",
                            "nguoi quan ly thung rac",
                            "người tạo nội dung thương hiệu",
                            "nguoi tao noi dung thuong hieu",
                            "người tải danh bạ",
                            "nguoi tai danh ba",
                            "memory_viewer",
                            "trash_manager",
                            "branded_content_partner",
                            "contact_uploader",
                            "người gây quỹ",
                            "nguoi gay quy",
                            "người ứng phó khủng hoảng",
                            "nguoi ung pho khung hoang",
                            "người khu phố",
                            "nguoi khu pho",
                            "người giám sát phụ huynh",
                            "nguoi giam sat phu huynh",
                            "fundraiser_donor",
                            "crisis_responder",
                            "neighborhood_member",
                            "parental_supervisor",
                        ),
                    )
                )
            created = timestamp_of(row)
            if created is None and from_inner and inner is not None:
                created = timestamp_of(inner)
            item = PublicComment(
                text=text,
                author_name=author_name,
                comment_id=first_comment_id(row) or (first_comment_id(inner) if from_inner and inner else None),
                created_time=created,
            )
            key = item.comment_id or f"{item.author_name}|{item.text}"
            if key not in seen:
                seen.add(key)
                comments.append(item)
        if len(comments) >= limit:
            return
        for name in ("comments", "replies"):
            nested = _connection_rows(row.get(name))
            if nested:
                _append_rows(nested, comments, seen, limit, depth + 1)
            if len(comments) >= limit:
                return


def _connection_rows(value: object) -> list[object]:
    """A Graph connection is ``{data: [...]}`` or ``{edges: [{node: ...}]}``."""
    return connection_rows(value)


def _graph_rows(data: dict[str, object]) -> list[object]:
    value = data.get("data")
    if isinstance(value, list) and value:
        return connection_rows(value)
    for name in (
        "comments",
        "replies",
        "comment_list",
        "commentList",
        "comment_thread",
        "comments_connection",
        "top_level_comments",
        "thread",
        "feedback",
    ):
        nested = _connection_rows(data.get(name))
        if nested:
            return nested
        child = data.get(name)
        if isinstance(child, dict):
            for inner in (
                "comments",
                "comment_list",
                "commentList",
                "comment_thread",
                "comments_connection",
                "top_level_comments",
                "data",
                "edges",
                "nodes",
            ):
                deeper = _connection_rows(child.get(inner))
                if deeper:
                    return deeper
    edge = data.get("comment_edge")
    if isinstance(edge, dict):
        nested = _connection_rows([edge])
        if nested:
            return nested
    message = data.get("message")
    if isinstance(message, dict) or scalar_text(message):
        return [data]
    return []


def post_id_from_url(url: str) -> str | None:
    raw = url.strip()
    if raw.isdigit() or ("_" in raw and raw.replace("_", "").isdigit()):
        return raw
    # Unwrap Facebook click-tracking and share wrappers before matching post ids.
    if re.search(r"/l\.php\?|/dialog/share\?|/sharer/sharer\.php\?", raw, re.IGNORECASE):
        wrapped = re.search(r"[?&](?:u|href)=([^&\s#]+)", raw, re.IGNORECASE)
        if wrapped:
            inner = unquote(wrapped.group(1)).strip()
            if inner and inner != raw:
                found = post_id_from_url(inner)
                if found:
                    return found
    photo = re.search(r"/(?:photos|videos)/(?:a|pcb)\.(\d+)/(\d+)", raw, re.IGNORECASE)
    if photo:
        return accepted_object_id(photo.group(2))
    market = re.search(r"/marketplace/item/(\d+)", raw, re.IGNORECASE)
    if market:
        return accepted_object_id(market.group(1))
    commerce = re.search(r"/commerce/products/(\d+)", raw, re.IGNORECASE)
    if commerce:
        return accepted_object_id(commerce.group(1))
    shop = re.search(r"/shop/product/(\d+)", raw, re.IGNORECASE)
    if shop:
        return accepted_object_id(shop.group(1))
    fundraiser = re.search(r"/fundraisers?/(\d+)", raw, re.IGNORECASE)
    if fundraiser:
        return accepted_object_id(fundraiser.group(1))
    donate = re.search(r"/donate/(\d+)", raw, re.IGNORECASE)
    if donate:
        return accepted_object_id(donate.group(1))
    job = re.search(r"[?&]job_opening_id=(\d+)", raw, re.IGNORECASE)
    if job:
        return accepted_object_id(job.group(1))
    gaming = re.search(r"/gaming/(?:play/)?(\d+)", raw, re.IGNORECASE)
    if gaming:
        return accepted_object_id(gaming.group(1))
    games_app = re.search(r"/games/app/(\d+)", raw, re.IGNORECASE)
    if games_app:
        return accepted_object_id(games_app.group(1))
    clips = re.search(r"/clips/(?:play/)?(\d+)", raw, re.IGNORECASE)
    if clips:
        return accepted_object_id(clips.group(1))
    place = re.search(r"/(?:places/thing/\?id=|place/)(\d+)", raw, re.IGNORECASE)
    if place:
        return accepted_object_id(place.group(1))
    local = re.search(r"/local/lists/(\d+)", raw, re.IGNORECASE)
    if local:
        return accepted_object_id(local.group(1))
    share = re.search(r"/share/[pvr]/(pfbid[A-Za-z0-9]+|\d+)", raw, re.IGNORECASE)
    if share:
        return accepted_object_id(share.group(1))
    notes = re.search(r"/notes/(?:[^/?#]+/)?(\d+)", raw, re.IGNORECASE)
    if notes:
        return accepted_object_id(notes.group(1))
    offers = re.search(r"/offers/(\d+)", raw, re.IGNORECASE)
    if offers:
        return accepted_object_id(offers.group(1))
    catalog = re.search(r"/catalog/products/(\d+)", raw, re.IGNORECASE)
    if catalog:
        return accepted_object_id(catalog.group(1))
    menu = re.search(r"/menu/\?id=(\d+)", raw, re.IGNORECASE)
    if menu:
        return accepted_object_id(menu.group(1))
    appointments = re.search(r"/appointments/\?id=(\d+)", raw, re.IGNORECASE)
    if appointments:
        return accepted_object_id(appointments.group(1))
    booking = re.search(r"/booking/\?id=(\d+)", raw, re.IGNORECASE)
    if booking:
        return accepted_object_id(booking.group(1))
    rooms = re.search(r"/rooms/(\d+)", raw, re.IGNORECASE)
    if rooms:
        return accepted_object_id(rooms.group(1))
    playlist = re.search(r"/watch/playlist/\?id=(\d+)", raw, re.IGNORECASE)
    if playlist:
        return accepted_object_id(playlist.group(1))
    ads_library = re.search(r"/ads/library/\?id=(\d+)", raw, re.IGNORECASE)
    if ads_library:
        return accepted_object_id(ads_library.group(1))
    live_producer = re.search(r"/live/producer/(\d+)", raw, re.IGNORECASE)
    if live_producer:
        return accepted_object_id(live_producer.group(1))
    effect = re.search(r"/effects/(\d+)", raw, re.IGNORECASE)
    if effect:
        return accepted_object_id(effect.group(1))
    orders = re.search(r"/commerce/orders/(\d+)", raw, re.IGNORECASE)
    if orders:
        return accepted_object_id(orders.group(1))
    payments = re.search(r"/payments/(\d+)", raw, re.IGNORECASE)
    if payments:
        return accepted_object_id(payments.group(1))
    coupon = re.search(r"/coupon/(\d+)", raw, re.IGNORECASE)
    if coupon:
        return accepted_object_id(coupon.group(1))
    branded = re.search(r"/brandedcontent/(\d+)", raw, re.IGNORECASE)
    if branded:
        return accepted_object_id(branded.group(1))
    stars = re.search(r"/stars/\?id=(\d+)", raw, re.IGNORECASE)
    if stars:
        return accepted_object_id(stars.group(1))
    drafts = re.search(r"/reels/drafts/\?id=(\d+)", raw, re.IGNORECASE)
    if drafts:
        return accepted_object_id(drafts.group(1))
    boost = re.search(r"/boost/\?id=(\d+)", raw, re.IGNORECASE)
    if boost:
        return accepted_object_id(boost.group(1))
    year_review = re.search(r"/yearinreview/\?id=(\d+)", raw, re.IGNORECASE)
    if year_review:
        return accepted_object_id(year_review.group(1))
    horizon = re.search(r"/horizon/world/(\d+)", raw, re.IGNORECASE)
    if horizon:
        return accepted_object_id(horizon.group(1))
    community_notes = re.search(r"/communitynotes/(\d+)", raw, re.IGNORECASE)
    if community_notes:
        return accepted_object_id(community_notes.group(1))
    templates = re.search(r"/reels/templates/\?id=(\d+)", raw, re.IGNORECASE)
    if templates:
        return accepted_object_id(templates.group(1))
    room_join = re.search(r"/rooms/join/\?id=(\d+)", raw, re.IGNORECASE)
    if room_join:
        return accepted_object_id(room_join.group(1))
    cart = re.search(r"/commerce/cart/\?id=(\d+)", raw, re.IGNORECASE)
    if cart:
        return accepted_object_id(cart.group(1))
    gifts = re.search(r"/gaming/gifts/\?id=(\d+)", raw, re.IGNORECASE)
    if gifts:
        return accepted_object_id(gifts.group(1))
    friend_list = re.search(r"[?&]list_id=(\d+)", raw, re.IGNORECASE)
    if friend_list and re.search(r"/friends/lists", raw, re.IGNORECASE):
        return accepted_object_id(friend_list.group(1))
    capcut = re.search(r"/reels/capcut/\?id=(\d+)", raw, re.IGNORECASE)
    if capcut:
        return accepted_object_id(capcut.group(1))
    bulletin = re.search(r"/bulletin/(\d+)", raw, re.IGNORECASE)
    if bulletin:
        return accepted_object_id(bulletin.group(1))
    family = re.search(r"/familycenter/\?id=(\d+)", raw, re.IGNORECASE)
    if family:
        return accepted_object_id(family.group(1))
    meta_verified = re.search(r"/meta_verified/\?id=(\d+)", raw, re.IGNORECASE)
    if meta_verified:
        return accepted_object_id(meta_verified.group(1))
    review = re.search(r"[?&]review_id=(\d+)", raw, re.IGNORECASE)
    if review:
        return accepted_object_id(review.group(1))
    shipment = re.search(r"/commerce/shipments/\?id=(\d+)", raw, re.IGNORECASE)
    if shipment:
        return accepted_object_id(shipment.group(1))
    quiz = re.search(r"/quiz/\?id=(\d+)", raw, re.IGNORECASE)
    if quiz:
        return accepted_object_id(quiz.group(1))
    insights = re.search(r"/reels/insights/\?id=(\d+)", raw, re.IGNORECASE)
    if insights:
        return accepted_object_id(insights.group(1))
    avatar = re.search(r"/avatareditor/\?id=(\d+)", raw, re.IGNORECASE)
    if avatar:
        return accepted_object_id(avatar.group(1))
    status = re.search(r"/status/\?id=(\d+)", raw, re.IGNORECASE)
    if status:
        return accepted_object_id(status.group(1))
    market_offer = re.search(r"/marketplace/offer/\?id=(\d+)", raw, re.IGNORECASE)
    if market_offer:
        return accepted_object_id(market_offer.group(1))
    share_threads = re.search(r"/share/threads/\?id=(\d+)", raw, re.IGNORECASE)
    if share_threads:
        return accepted_object_id(share_threads.group(1))
    instagram_crosspost = re.search(r"/instagram/crosspost/\?id=(\d+)", raw, re.IGNORECASE)
    if instagram_crosspost:
        return accepted_object_id(instagram_crosspost.group(1))
    effects_saved = re.search(r"/effects/saved/\?id=(\d+)", raw, re.IGNORECASE)
    if effects_saved:
        return accepted_object_id(effects_saved.group(1))
    live_schedule = re.search(r"/live/schedule/\?id=(\d+)", raw, re.IGNORECASE)
    if live_schedule:
        return accepted_object_id(live_schedule.group(1))
    privacy_tags = re.search(r"/privacy/tags/\?id=(\d+)", raw, re.IGNORECASE)
    if privacy_tags:
        return accepted_object_id(privacy_tags.group(1))
    gaming_tournament = re.search(r"/gaming/tournament/\?id=(\d+)", raw, re.IGNORECASE)
    if gaming_tournament:
        return accepted_object_id(gaming_tournament.group(1))
    jobs_job = re.search(r"/jobs/job/\?id=(\d+)", raw, re.IGNORECASE)
    if jobs_job:
        return accepted_object_id(jobs_job.group(1))
    fundraisers_q = re.search(r"/fundraisers/\?id=(\d+)", raw, re.IGNORECASE)
    if fundraisers_q:
        return accepted_object_id(fundraisers_q.group(1))
    donate_fundraiser = re.search(r"/donate/\?fundraiser_id=(\d+)", raw, re.IGNORECASE)
    if donate_fundraiser:
        return accepted_object_id(donate_fundraiser.group(1))
    polls = re.search(r"/polls/\?id=(\d+)", raw, re.IGNORECASE)
    if polls:
        return accepted_object_id(polls.group(1))
    memorial = re.search(r"/memorial/\?id=(\d+)", raw, re.IGNORECASE)
    if memorial:
        return accepted_object_id(memorial.group(1))
    share_messenger = re.search(r"/share/messenger/\?id=(\d+)", raw, re.IGNORECASE)
    if share_messenger:
        return accepted_object_id(share_messenger.group(1))
    share_whatsapp = re.search(r"/share/whatsapp/\?id=(\d+)", raw, re.IGNORECASE)
    if share_whatsapp:
        return accepted_object_id(share_whatsapp.group(1))
    soundbites = re.search(r"/soundbites/\?id=(\d+)", raw, re.IGNORECASE)
    if soundbites:
        return accepted_object_id(soundbites.group(1))
    podcasts = re.search(r"/podcasts/\?id=(\d+)", raw, re.IGNORECASE)
    if podcasts:
        return accepted_object_id(podcasts.group(1))
    wishlist = re.search(r"/(?:commerce/)?wishlist/\?id=(\d+)", raw, re.IGNORECASE)
    if wishlist:
        return accepted_object_id(wishlist.group(1))
    shipping = re.search(r"/(?:commerce/)?shipping/\?id=(\d+)", raw, re.IGNORECASE)
    if shipping:
        return accepted_object_id(shipping.group(1))
    feedback = re.search(r"/feedback/\?id=(\d+)", raw, re.IGNORECASE)
    if feedback:
        return accepted_object_id(feedback.group(1))
    reviews_write = re.search(r"/reviews/write/\?id=(\d+)", raw, re.IGNORECASE)
    if reviews_write:
        return accepted_object_id(reviews_write.group(1))
    ar_effect = re.search(r"/ar/effect/\?id=(\d+)", raw, re.IGNORECASE)
    if ar_effect:
        return accepted_object_id(ar_effect.group(1))
    camera_effects = re.search(r"/camera_effects/\?id=(\d+)", raw, re.IGNORECASE)
    if camera_effects:
        return accepted_object_id(camera_effects.group(1))
    qrcode = re.search(r"/qr(?:code)?/\?id=(\d+)", raw, re.IGNORECASE)
    if qrcode:
        return accepted_object_id(qrcode.group(1))
    accounts_center = re.search(r"/accounts_center/\?id=(\d+)", raw, re.IGNORECASE)
    if accounts_center:
        return accepted_object_id(accounts_center.group(1))
    creator_marketplace = re.search(r"/creator_marketplace/\?id=(\d+)", raw, re.IGNORECASE)
    if creator_marketplace:
        return accepted_object_id(creator_marketplace.group(1))
    brandcollabs = re.search(r"/brandcollabs/\?id=(\d+)", raw, re.IGNORECASE)
    if brandcollabs:
        return accepted_object_id(brandcollabs.group(1))
    professional_mode = re.search(r"/professional_mode/\?id=(\d+)", raw, re.IGNORECASE)
    if professional_mode:
        return accepted_object_id(professional_mode.group(1))
    lock_profile = re.search(r"/lock_profile/\?id=(\d+)", raw, re.IGNORECASE)
    if lock_profile:
        return accepted_object_id(lock_profile.group(1))
    subscriptions = re.search(r"/subscriptions/\?id=(\d+)", raw, re.IGNORECASE)
    if subscriptions:
        return accepted_object_id(subscriptions.group(1))
    spark_ar = re.search(r"/spark_?ar/\?id=(\d+)", raw, re.IGNORECASE)
    if spark_ar:
        return accepted_object_id(spark_ar.group(1))

    events_going = re.search(r"/events/going/\?id=(\d+)", raw, re.IGNORECASE)
    if events_going:
        return accepted_object_id(events_going.group(1))
    events_interested = re.search(r"/events/interested/\?id=(\d+)", raw, re.IGNORECASE)
    if events_interested:
        return accepted_object_id(events_interested.group(1))
    events_create = re.search(r"/events/create/\?id=(\d+)", raw, re.IGNORECASE)
    if events_create:
        return accepted_object_id(events_create.group(1))
    events_calendar = re.search(r"/events/calendar/\?id=(\d+)", raw, re.IGNORECASE)
    if events_calendar:
        return accepted_object_id(events_calendar.group(1))
    tickets = re.search(r"/tickets/\?id=(\d+)", raw, re.IGNORECASE)
    if tickets:
        return accepted_object_id(tickets.group(1))
    groups_join = re.search(r"/groups/join/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_join:
        return accepted_object_id(groups_join.group(1))
    groups_rules = re.search(r"/groups/rules/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_rules:
        return accepted_object_id(groups_rules.group(1))
    groups_announcements = re.search(r"/groups/announcements/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_announcements:
        return accepted_object_id(groups_announcements.group(1))
    dating = re.search(r"/dating/(?:matches/)?\?id=(\d+)", raw, re.IGNORECASE)
    if dating:
        return accepted_object_id(dating.group(1))
    climate = re.search(r"/climate/\?id=(\d+)", raw, re.IGNORECASE)
    if climate:
        return accepted_object_id(climate.group(1))
    communityhelp = re.search(r"/communityhelp/\?id=(\d+)", raw, re.IGNORECASE)
    if communityhelp:
        return accepted_object_id(communityhelp.group(1))
    blooddonations = re.search(r"/blooddonations/\?id=(\d+)", raw, re.IGNORECASE)
    if blooddonations:
        return accepted_object_id(blooddonations.group(1))
    votinginformation = re.search(r"/votinginformation/\?id=(\d+)", raw, re.IGNORECASE)
    if votinginformation:
        return accepted_object_id(votinginformation.group(1))
    townhall = re.search(r"/townhall/\?id=(\d+)", raw, re.IGNORECASE)
    if townhall:
        return accepted_object_id(townhall.group(1))
    safetycheck = re.search(r"/safetycheck/\?id=(\d+)", raw, re.IGNORECASE)
    if safetycheck:
        return accepted_object_id(safetycheck.group(1))
    crisisresponse = re.search(r"/crisisresponse/\?id=(\d+)", raw, re.IGNORECASE)
    if crisisresponse:
        return accepted_object_id(crisisresponse.group(1))
    workrooms = re.search(r"/workrooms/\?id=(\d+)", raw, re.IGNORECASE)
    if workrooms:
        return accepted_object_id(workrooms.group(1))
    horizon_home = re.search(r"/horizon/home/\?id=(\d+)", raw, re.IGNORECASE)
    if horizon_home:
        return accepted_object_id(horizon_home.group(1))
    immersive = re.search(r"/immersive/\?id=(\d+)", raw, re.IGNORECASE)
    if immersive:
        return accepted_object_id(immersive.group(1))

    marketplace_create = re.search(r"/marketplace/create/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_create:
        return accepted_object_id(marketplace_create.group(1))
    marketplace_selling = re.search(r"/marketplace/you/selling/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_selling:
        return accepted_object_id(marketplace_selling.group(1))
    marketplace_buying = re.search(r"/marketplace/you/buying/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_buying:
        return accepted_object_id(marketplace_buying.group(1))
    marketplace_item = re.search(r"/marketplace/item/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_item:
        return accepted_object_id(marketplace_item.group(1))
    marketplace_category = re.search(r"/marketplace/category/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_category:
        return accepted_object_id(marketplace_category.group(1))
    marketplace_vehicles = re.search(r"/marketplace/vehicles/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_vehicles:
        return accepted_object_id(marketplace_vehicles.group(1))
    marketplace_property = re.search(r"/marketplace/property/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_property:
        return accepted_object_id(marketplace_property.group(1))
    marketplace_offers = re.search(r"/marketplace/offers/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_offers:
        return accepted_object_id(marketplace_offers.group(1))
    jobs = re.search(r"/jobs/(?:application/|saved/)?\?id=(\d+)", raw, re.IGNORECASE)
    if jobs:
        return accepted_object_id(jobs.group(1))
    profile_education = re.search(r"/profile/education/\?id=(\d+)", raw, re.IGNORECASE)
    if profile_education:
        return accepted_object_id(profile_education.group(1))
    profile_work = re.search(r"/profile/work/\?id=(\d+)", raw, re.IGNORECASE)
    if profile_work:
        return accepted_object_id(profile_work.group(1))
    profile_relationship = re.search(r"/profile/relationship/\?id=(\d+)", raw, re.IGNORECASE)
    if profile_relationship:
        return accepted_object_id(profile_relationship.group(1))
    friends_mutual = re.search(r"/friends/mutual/\?id=(\d+)", raw, re.IGNORECASE)
    if friends_mutual:
        return accepted_object_id(friends_mutual.group(1))
    favorites = re.search(r"/favorites/\?id=(\d+)", raw, re.IGNORECASE)
    if favorites:
        return accepted_object_id(favorites.group(1))
    shortcuts = re.search(r"/shortcuts/\?id=(\d+)", raw, re.IGNORECASE)
    if shortcuts:
        return accepted_object_id(shortcuts.group(1))
    feed_preferences = re.search(r"/feed/preferences/\?id=(\d+)", raw, re.IGNORECASE)
    if feed_preferences:
        return accepted_object_id(feed_preferences.group(1))

    stories_create = re.search(r"/stories/create/\?id=(\d+)", raw, re.IGNORECASE)
    if stories_create:
        return accepted_object_id(stories_create.group(1))
    stories_archive = re.search(r"/stories/archive/\?id=(\d+)", raw, re.IGNORECASE)
    if stories_archive:
        return accepted_object_id(stories_archive.group(1))
    stories_highlight = re.search(r"/stories/highlight/\?id=(\d+)", raw, re.IGNORECASE)
    if stories_highlight:
        return accepted_object_id(stories_highlight.group(1))
    reels_create = re.search(r"/reels/create/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_create:
        return accepted_object_id(reels_create.group(1))
    reels_audio = re.search(r"/reels/audio/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_audio:
        return accepted_object_id(reels_audio.group(1))
    reels_sequence = re.search(r"/reels/sequence/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_sequence:
        return accepted_object_id(reels_sequence.group(1))
    pages_transparency = re.search(r"/pages/transparency/\?id=(\d+)", raw, re.IGNORECASE)
    if pages_transparency:
        return accepted_object_id(pages_transparency.group(1))
    pages_roles = re.search(r"/pages/roles/\?id=(\d+)", raw, re.IGNORECASE)
    if pages_roles:
        return accepted_object_id(pages_roles.group(1))
    pages_quality = re.search(r"/pages/quality/\?id=(\d+)", raw, re.IGNORECASE)
    if pages_quality:
        return accepted_object_id(pages_quality.group(1))
    pages_cta = re.search(r"/pages/cta/\?id=(\d+)", raw, re.IGNORECASE)
    if pages_cta:
        return accepted_object_id(pages_cta.group(1))
    pages_inbox = re.search(r"/pages/inbox/\?id=(\d+)", raw, re.IGNORECASE)
    if pages_inbox:
        return accepted_object_id(pages_inbox.group(1))
    close_friends = re.search(r"/close_friends/\?id=(\d+)", raw, re.IGNORECASE)
    if close_friends:
        return accepted_object_id(close_friends.group(1))
    ai_stickers = re.search(r"/ai/stickers/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_stickers:
        return accepted_object_id(ai_stickers.group(1))
    stickers = re.search(r"/stickers/\?id=(\d+)", raw, re.IGNORECASE)
    if stickers:
        return accepted_object_id(stickers.group(1))
    magic_editor = re.search(r"/magic_editor/\?id=(\d+)", raw, re.IGNORECASE)
    if magic_editor:
        return accepted_object_id(magic_editor.group(1))
    saved_replies = re.search(r"/saved_replies/\?id=(\d+)", raw, re.IGNORECASE)
    if saved_replies:
        return accepted_object_id(saved_replies.group(1))
    automated_responses = re.search(r"/automated_responses/\?id=(\d+)", raw, re.IGNORECASE)
    if automated_responses:
        return accepted_object_id(automated_responses.group(1))
    icebreakers = re.search(r"/icebreakers/\?id=(\d+)", raw, re.IGNORECASE)
    if icebreakers:
        return accepted_object_id(icebreakers.group(1))

    ad_center = re.search(r"/ad_center/\?id=(\d+)", raw, re.IGNORECASE)
    if ad_center:
        return accepted_object_id(ad_center.group(1))
    adsmanager = re.search(r"/adsmanager/\?id=(\d+)", raw, re.IGNORECASE)
    if adsmanager:
        return accepted_object_id(adsmanager.group(1))
    ads_audience = re.search(r"/ads/audience/\?id=(\d+)", raw, re.IGNORECASE)
    if ads_audience:
        return accepted_object_id(ads_audience.group(1))
    ads_billing = re.search(r"/ads/billing/\?id=(\d+)", raw, re.IGNORECASE)
    if ads_billing:
        return accepted_object_id(ads_billing.group(1))
    notifications = re.search(r"/notifications/(?:settings/)?\?id=(\d+)", raw, re.IGNORECASE)
    if notifications:
        return accepted_object_id(notifications.group(1))
    search_recent = re.search(r"/search/(?:recent|posts)/\?id=(\d+)", raw, re.IGNORECASE)
    if search_recent:
        return accepted_object_id(search_recent.group(1))
    activity_log = re.search(r"/activity_log/\?id=(\d+)", raw, re.IGNORECASE)
    if activity_log:
        return accepted_object_id(activity_log.group(1))
    dyi = re.search(r"/dyi/\?id=(\d+)", raw, re.IGNORECASE)
    if dyi:
        return accepted_object_id(dyi.group(1))
    your_information = re.search(r"/your_information/\?id=(\d+)", raw, re.IGNORECASE)
    if your_information:
        return accepted_object_id(your_information.group(1))
    settings_apps = re.search(r"/settings/apps/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_apps:
        return accepted_object_id(settings_apps.group(1))
    settings_security = re.search(r"/settings/security/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_security:
        return accepted_object_id(settings_security.group(1))
    settings_sessions = re.search(r"/settings/sessions/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_sessions:
        return accepted_object_id(settings_sessions.group(1))
    legacy = re.search(r"/legacy/\?id=(\d+)", raw, re.IGNORECASE)
    if legacy:
        return accepted_object_id(legacy.group(1))
    trusted_contacts = re.search(r"/trusted_contacts/\?id=(\d+)", raw, re.IGNORECASE)
    if trusted_contacts:
        return accepted_object_id(trusted_contacts.group(1))
    security_checkup = re.search(r"/security_checkup/\?id=(\d+)", raw, re.IGNORECASE)
    if security_checkup:
        return accepted_object_id(security_checkup.group(1))

    watch_later = re.search(r"/watch/later/\?id=(\d+)", raw, re.IGNORECASE)
    if watch_later:
        return accepted_object_id(watch_later.group(1))
    video_settings = re.search(r"/video/settings/\?id=(\d+)", raw, re.IGNORECASE)
    if video_settings:
        return accepted_object_id(video_settings.group(1))
    captions_settings = re.search(r"/captions/settings/\?id=(\d+)", raw, re.IGNORECASE)
    if captions_settings:
        return accepted_object_id(captions_settings.group(1))
    gaming_stream = re.search(r"/gaming/stream/\?id=(\d+)", raw, re.IGNORECASE)
    if gaming_stream:
        return accepted_object_id(gaming_stream.group(1))
    gaming_clips = re.search(r"/gaming/clips/\?id=(\d+)", raw, re.IGNORECASE)
    if gaming_clips:
        return accepted_object_id(gaming_clips.group(1))
    gaming_leaderboard = re.search(r"/gaming/leaderboard/\?id=(\d+)", raw, re.IGNORECASE)
    if gaming_leaderboard:
        return accepted_object_id(gaming_leaderboard.group(1))
    gaming_challenge = re.search(r"/gaming/challenge/\?id=(\d+)", raw, re.IGNORECASE)
    if gaming_challenge:
        return accepted_object_id(gaming_challenge.group(1))
    instantgames = re.search(r"/instantgames/\?id=(\d+)", raw, re.IGNORECASE)
    if instantgames:
        return accepted_object_id(instantgames.group(1))
    games_browse = re.search(r"/games/browse/\?id=(\d+)", raw, re.IGNORECASE)
    if games_browse:
        return accepted_object_id(games_browse.group(1))
    stars_buy = re.search(r"/stars/buy/\?id=(\d+)", raw, re.IGNORECASE)
    if stars_buy:
        return accepted_object_id(stars_buy.group(1))
    stars_gift = re.search(r"/stars/gift/\?id=(\d+)", raw, re.IGNORECASE)
    if stars_gift:
        return accepted_object_id(stars_gift.group(1))
    live_badges = re.search(r"/live/badges/\?id=(\d+)", raw, re.IGNORECASE)
    if live_badges:
        return accepted_object_id(live_badges.group(1))
    picture_in_picture = re.search(r"/picture_in_picture/\?id=(\d+)", raw, re.IGNORECASE)
    if picture_in_picture:
        return accepted_object_id(picture_in_picture.group(1))
    autoplay = re.search(r"/autoplay/\?id=(\d+)", raw, re.IGNORECASE)
    if autoplay:
        return accepted_object_id(autoplay.group(1))

    settings_accessibility = re.search(r"/settings/accessibility/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_accessibility:
        return accepted_object_id(settings_accessibility.group(1))
    settings_language = re.search(r"/settings/language/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_language:
        return accepted_object_id(settings_language.group(1))
    settings_dark_mode = re.search(r"/settings/dark_mode/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_dark_mode:
        return accepted_object_id(settings_dark_mode.group(1))
    settings_text_size = re.search(r"/settings/text_size/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_text_size:
        return accepted_object_id(settings_text_size.group(1))
    settings_media = re.search(r"/settings/media/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_media:
        return accepted_object_id(settings_media.group(1))
    settings_data = re.search(r"/settings/data/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_data:
        return accepted_object_id(settings_data.group(1))
    settings_beta = re.search(r"/settings/beta/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_beta:
        return accepted_object_id(settings_beta.group(1))
    settings_region = re.search(r"/settings/region/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_region:
        return accepted_object_id(settings_region.group(1))
    settings_experiments = re.search(r"/settings/experiments/\?id=(\d+)", raw, re.IGNORECASE)
    if settings_experiments:
        return accepted_object_id(settings_experiments.group(1))
    help_keyboard = re.search(r"/help/keyboard/\?id=(\d+)", raw, re.IGNORECASE)
    if help_keyboard:
        return accepted_object_id(help_keyboard.group(1))
    alt_text = re.search(r"/alt_text/\?id=(\d+)", raw, re.IGNORECASE)
    if alt_text:
        return accepted_object_id(alt_text.group(1))
    upload_queue = re.search(r"/upload/queue/\?id=(\d+)", raw, re.IGNORECASE)
    if upload_queue:
        return accepted_object_id(upload_queue.group(1))
    offline = re.search(r"/offline/\?id=(\d+)", raw, re.IGNORECASE)
    if offline:
        return accepted_object_id(offline.group(1))
    composer_retry = re.search(r"/composer/retry/\?id=(\d+)", raw, re.IGNORECASE)
    if composer_retry:
        return accepted_object_id(composer_retry.group(1))

    meta_ai = re.search(r"/meta_ai/(?:chat/|imagine/)?\?id=(\d+)", raw, re.IGNORECASE)
    if meta_ai:
        return accepted_object_id(meta_ai.group(1))
    ai_rewrite = re.search(r"/ai/rewrite/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_rewrite:
        return accepted_object_id(ai_rewrite.group(1))
    ai_edit = re.search(r"/ai/edit/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_edit:
        return accepted_object_id(ai_edit.group(1))
    ai_video = re.search(r"/ai/video/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_video:
        return accepted_object_id(ai_video.group(1))
    ai_voice = re.search(r"/ai/voice/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_voice:
        return accepted_object_id(ai_voice.group(1))
    ai_try_on = re.search(r"/ai/try_on/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_try_on:
        return accepted_object_id(ai_try_on.group(1))
    ai_search = re.search(r"/ai/search/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_search:
        return accepted_object_id(ai_search.group(1))
    ai_label = re.search(r"/ai/label/\?id=(\d+)", raw, re.IGNORECASE)
    if ai_label:
        return accepted_object_id(ai_label.group(1))
    edits_ai = re.search(r"/edits/ai/\?id=(\d+)", raw, re.IGNORECASE)
    if edits_ai:
        return accepted_object_id(edits_ai.group(1))
    imagine_me = re.search(r"/imagine_me/\?id=(\d+)", raw, re.IGNORECASE)
    if imagine_me:
        return accepted_object_id(imagine_me.group(1))

    reels_audio_library = re.search(r"/reels/audio/(?:library|trending|saved)/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_audio_library:
        return accepted_object_id(reels_audio_library.group(1))
    reels_trim = re.search(r"/reels/trim/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_trim:
        return accepted_object_id(reels_trim.group(1))
    reels_speed = re.search(r"/reels/speed/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_speed:
        return accepted_object_id(reels_speed.group(1))
    reels_transitions = re.search(r"/reels/transitions/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_transitions:
        return accepted_object_id(reels_transitions.group(1))
    reels_layout = re.search(r"/reels/layout/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_layout:
        return accepted_object_id(reels_layout.group(1))
    reels_timer = re.search(r"/reels/timer/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_timer:
        return accepted_object_id(reels_timer.group(1))
    reels_voice_effects = re.search(r"/reels/voice_effects/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_voice_effects:
        return accepted_object_id(reels_voice_effects.group(1))
    reels_green_screen = re.search(r"/reels/green_screen/\?id=(\d+)", raw, re.IGNORECASE)
    if reels_green_screen:
        return accepted_object_id(reels_green_screen.group(1))
    effects_gallery = re.search(r"/effects/gallery/\?id=(\d+)", raw, re.IGNORECASE)
    if effects_gallery:
        return accepted_object_id(effects_gallery.group(1))
    effects_download = re.search(r"/effects/download/\?id=(\d+)", raw, re.IGNORECASE)
    if effects_download:
        return accepted_object_id(effects_download.group(1))
    camera_beauty = re.search(r"/camera/beauty/\?id=(\d+)", raw, re.IGNORECASE)
    if camera_beauty:
        return accepted_object_id(camera_beauty.group(1))
    camera_flash = re.search(r"/camera/flash/\?id=(\d+)", raw, re.IGNORECASE)
    if camera_flash:
        return accepted_object_id(camera_flash.group(1))
    notes_library = re.search(r"/notes/(?:library|drafts)/\?id=(\d+)", raw, re.IGNORECASE)
    if notes_library:
        return accepted_object_id(notes_library.group(1))
    podcasts_episodes = re.search(r"/podcasts/episodes/\?id=(\d+)", raw, re.IGNORECASE)
    if podcasts_episodes:
        return accepted_object_id(podcasts_episodes.group(1))
    episodes = re.search(r"/episodes/\?id=(\d+)", raw, re.IGNORECASE)
    if episodes:
        return accepted_object_id(episodes.group(1))
    albums_create = re.search(r"/albums/(?:create|edit)/\?id=(\d+)", raw, re.IGNORECASE)
    if albums_create:
        return accepted_object_id(albums_create.group(1))
    collections = re.search(r"/collections/(?:view/)?\?id=(\d+)", raw, re.IGNORECASE)
    if collections:
        return accepted_object_id(collections.group(1))
    saved_collections = re.search(r"/saved/collections/\?id=(\d+)", raw, re.IGNORECASE)
    if saved_collections:
        return accepted_object_id(saved_collections.group(1))
    giftcards = re.search(r"/giftcards/(?:send/)?\?id=(\d+)", raw, re.IGNORECASE)
    if giftcards:
        return accepted_object_id(giftcards.group(1))
    communitychats = re.search(r"/communitychats/\?id=(\d+)", raw, re.IGNORECASE)
    if communitychats:
        return accepted_object_id(communitychats.group(1))
    chats = re.search(r"/chats/\?id=(\d+)", raw, re.IGNORECASE)
    if chats:
        return accepted_object_id(chats.group(1))
    quiet_mode = re.search(r"/quiet_mode/\?id=(\d+)", raw, re.IGNORECASE)
    if quiet_mode:
        return accepted_object_id(quiet_mode.group(1))
    teen_accounts = re.search(r"/teen_accounts/\?id=(\d+)", raw, re.IGNORECASE)
    if teen_accounts:
        return accepted_object_id(teen_accounts.group(1))
    supervision = re.search(r"/supervision/\?id=(\d+)", raw, re.IGNORECASE)
    if supervision:
        return accepted_object_id(supervision.group(1))
    take_a_break = re.search(r"/take_a_break/\?id=(\d+)", raw, re.IGNORECASE)
    if take_a_break:
        return accepted_object_id(take_a_break.group(1))
    whatsapp = re.search(r"/whatsapp/(?:share/)?\?id=(\d+)", raw, re.IGNORECASE)
    if whatsapp:
        return accepted_object_id(whatsapp.group(1))
    instagram_crosspost = re.search(r"/instagram/crosspost/\?id=(\d+)", raw, re.IGNORECASE)
    if instagram_crosspost:
        return accepted_object_id(instagram_crosspost.group(1))
    remix = re.search(r"/remix/\?id=(\d+)", raw, re.IGNORECASE)
    if remix:
        return accepted_object_id(remix.group(1))
    collab = re.search(r"/collab/\?id=(\d+)", raw, re.IGNORECASE)
    if collab:
        return accepted_object_id(collab.group(1))
    captions = re.search(r"/captions/\?id=(\d+)", raw, re.IGNORECASE)
    if captions:
        return accepted_object_id(captions.group(1))
    templates_browse = re.search(r"/templates/(?:browse/)?\?id=(\d+)", raw, re.IGNORECASE)
    if templates_browse:
        return accepted_object_id(templates_browse.group(1))
    security_login = re.search(r"/security/login/\?id=(\d+)", raw, re.IGNORECASE)
    if security_login:
        return accepted_object_id(security_login.group(1))
    login_alerts = re.search(r"/login_alerts/\?id=(\d+)", raw, re.IGNORECASE)
    if login_alerts:
        return accepted_object_id(login_alerts.group(1))
    newsletters = re.search(r"/newsletters/(?:subscribe/)?\?id=(\d+)", raw, re.IGNORECASE)
    if newsletters:
        return accepted_object_id(newsletters.group(1))
    tags_review = re.search(r"/tags/review/\?id=(\d+)", raw, re.IGNORECASE)
    if tags_review:
        return accepted_object_id(tags_review.group(1))
    friendship = re.search(r"/friendship/\?id=(\d+)", raw, re.IGNORECASE)
    if friendship:
        return accepted_object_id(friendship.group(1))
    privacy_checkup = re.search(r"/privacy_checkup/\?id=(\d+)", raw, re.IGNORECASE)
    if privacy_checkup:
        return accepted_object_id(privacy_checkup.group(1))
    avatars = re.search(r"/avatars/\?id=(\d+)", raw, re.IGNORECASE)
    if avatars:
        return accepted_object_id(avatars.group(1))
    meta_verified_manage = re.search(
        r"/meta_verified/(?:manage|benefits)/\?id=(\d+)", raw, re.IGNORECASE
    )
    if meta_verified_manage:
        return accepted_object_id(meta_verified_manage.group(1))
    groups_announcements = re.search(r"/groups/announcements/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_announcements:
        return accepted_object_id(groups_announcements.group(1))
    groups_pending = re.search(r"/groups/pending/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_pending:
        return accepted_object_id(groups_pending.group(1))
    groups_invite = re.search(r"/groups/invite/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_invite:
        return accepted_object_id(groups_invite.group(1))
    dating_roses = re.search(r"/dating/(?:roses|pass)/\?id=(\d+)", raw, re.IGNORECASE)
    if dating_roses:
        return accepted_object_id(dating_roses.group(1))
    roses = re.search(r"/roses/\?id=(\d+)", raw, re.IGNORECASE)
    if roses:
        return accepted_object_id(roses.group(1))
    bag = re.search(r"/bag/\?id=(\d+)", raw, re.IGNORECASE)
    if bag:
        return accepted_object_id(bag.group(1))
    checkout = re.search(r"/checkout/\?id=(\d+)", raw, re.IGNORECASE)
    if checkout:
        return accepted_object_id(checkout.group(1))
    orders_track = re.search(r"/orders/track/\?id=(\d+)", raw, re.IGNORECASE)
    if orders_track:
        return accepted_object_id(orders_track.group(1))
    rooms_call = re.search(r"/rooms/call/\?id=(\d+)", raw, re.IGNORECASE)
    if rooms_call:
        return accepted_object_id(rooms_call.group(1))
    pollingplace = re.search(r"/pollingplace/\?id=(\d+)", raw, re.IGNORECASE)
    if pollingplace:
        return accepted_object_id(pollingplace.group(1))
    elections_reminders = re.search(r"/elections/reminders/\?id=(\d+)", raw, re.IGNORECASE)
    if elections_reminders:
        return accepted_object_id(elections_reminders.group(1))
    gaming_spectate = re.search(r"/gaming/spectate/\?id=(\d+)", raw, re.IGNORECASE)
    if gaming_spectate:
        return accepted_object_id(gaming_spectate.group(1))
    tournament_bracket = re.search(r"/tournament/bracket/\?id=(\d+)", raw, re.IGNORECASE)
    if tournament_bracket:
        return accepted_object_id(tournament_bracket.group(1))
    music_library = re.search(r"/music/library/\?id=(\d+)", raw, re.IGNORECASE)
    if music_library:
        return accepted_object_id(music_library.group(1))
    sound_original = re.search(r"/sound/original/\?id=(\d+)", raw, re.IGNORECASE)
    if sound_original:
        return accepted_object_id(sound_original.group(1))
    text_style = re.search(r"/text/style/\?id=(\d+)", raw, re.IGNORECASE)
    if text_style:
        return accepted_object_id(text_style.group(1))
    stickers_store = re.search(r"/stickers/store/\?id=(\d+)", raw, re.IGNORECASE)
    if stickers_store:
        return accepted_object_id(stickers_store.group(1))
    gifs_store = re.search(r"/gifs/store/\?id=(\d+)", raw, re.IGNORECASE)
    if gifs_store:
        return accepted_object_id(gifs_store.group(1))
    weather = re.search(r"/weather/(?:forecast/)?\?id=(\d+)", raw, re.IGNORECASE)
    if weather:
        return accepted_object_id(weather.group(1))
    work_accounts = re.search(r"/work/accounts/\?id=(\d+)", raw, re.IGNORECASE)
    if work_accounts:
        return accepted_object_id(work_accounts.group(1))
    life_events = re.search(r"/life_events/\?id=(\d+)", raw, re.IGNORECASE)
    if life_events:
        return accepted_object_id(life_events.group(1))
    about_contact = re.search(r"/about/contact/\?id=(\d+)", raw, re.IGNORECASE)
    if about_contact:
        return accepted_object_id(about_contact.group(1))
    digital_collectibles = re.search(r"/digital_collectibles/\?id=(\d+)", raw, re.IGNORECASE)
    if digital_collectibles:
        return accepted_object_id(digital_collectibles.group(1))
    marketplace_seller = re.search(r"/marketplace/seller/\?id=(\d+)", raw, re.IGNORECASE)
    if marketplace_seller:
        return accepted_object_id(marketplace_seller.group(1))
    polls_create = re.search(r"/polls/create/\?id=(\d+)", raw, re.IGNORECASE)
    if polls_create:
        return accepted_object_id(polls_create.group(1))
    events_invite = re.search(r"/events/invite/\?id=(\d+)", raw, re.IGNORECASE)
    if events_invite:
        return accepted_object_id(events_invite.group(1))
    greenscreen_remix = re.search(r"/greenscreen/remix/\?id=(\d+)", raw, re.IGNORECASE)
    if greenscreen_remix:
        return accepted_object_id(greenscreen_remix.group(1))
    profile_view_as = re.search(r"/profile/view_as/\?id=(\d+)", raw, re.IGNORECASE)
    if profile_view_as:
        return accepted_object_id(profile_view_as.group(1))
    groups_insights = re.search(r"/groups/insights/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_insights:
        return accepted_object_id(groups_insights.group(1))
    groups_snooze = re.search(r"/groups/snooze/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_snooze:
        return accepted_object_id(groups_snooze.group(1))
    groups_admins = re.search(r"/groups/admins/\?id=(\d+)", raw, re.IGNORECASE)
    if groups_admins:
        return accepted_object_id(groups_admins.group(1))
    messenger_voice = re.search(r"/messenger/(?:voice|video)/\?id=(\d+)", raw, re.IGNORECASE)
    if messenger_voice:
        return accepted_object_id(messenger_voice.group(1))
    reactions_all = re.search(r"/reactions/all/\?id=(\d+)", raw, re.IGNORECASE)
    if reactions_all:
        return accepted_object_id(reactions_all.group(1))
    translate = re.search(r"/translate/\?id=(\d+)", raw, re.IGNORECASE)
    if translate:
        return accepted_object_id(translate.group(1))
    save_link = re.search(r"/save/link/\?id=(\d+)", raw, re.IGNORECASE)
    if save_link:
        return accepted_object_id(save_link.group(1))
    profile_pin = re.search(r"/profile/pin/\?id=(\d+)", raw, re.IGNORECASE)
    if profile_pin:
        return accepted_object_id(profile_pin.group(1))
    highlights = re.search(r"/highlights/\?id=(\d+)", raw, re.IGNORECASE)
    if highlights:
        return accepted_object_id(highlights.group(1))
    shops_collection = re.search(r"/shops/collection/\?id=(\d+)", raw, re.IGNORECASE)
    if shops_collection:
        return accepted_object_id(shops_collection.group(1))
    branded_content = re.search(r"/branded_content/\?id=(\d+)", raw, re.IGNORECASE)
    if branded_content:
        return accepted_object_id(branded_content.group(1))
    collaboration = re.search(r"/collaboration/\?id=(\d+)", raw, re.IGNORECASE)
    if collaboration:
        return accepted_object_id(collaboration.group(1))
    watch_continue = re.search(r"/watch/continue/\?id=(\d+)", raw, re.IGNORECASE)
    if watch_continue:
        return accepted_object_id(watch_continue.group(1))
    support_inbox = re.search(r"/support_inbox/\?id=(\d+)", raw, re.IGNORECASE)
    if support_inbox:
        return accepted_object_id(support_inbox.group(1))
    adpreferences_topics = re.search(r"/adpreferences/topics/\?id=(\d+)", raw, re.IGNORECASE)
    if adpreferences_topics:
        return accepted_object_id(adpreferences_topics.group(1))
    watch_party = re.search(r"/watch_party/\?id=(\d+)", raw, re.IGNORECASE)
    if watch_party:
        return accepted_object_id(watch_party.group(1))
    activity_manage = re.search(r"/activity/manage/\?id=(\d+)", raw, re.IGNORECASE)
    if activity_manage:
        return accepted_object_id(activity_manage.group(1))
    search_history = re.search(r"/search/history/\?id=(\d+)", raw, re.IGNORECASE)
    if search_history:
        return accepted_object_id(search_history.group(1))
    friends_requests = re.search(r"/friends/requests/\?id=(\d+)", raw, re.IGNORECASE)
    if friends_requests:
        return accepted_object_id(friends_requests.group(1))
    off_facebook_activity = re.search(r"/off_facebook_activity/\?id=(\d+)", raw, re.IGNORECASE)
    if off_facebook_activity:
        return accepted_object_id(off_facebook_activity.group(1))
    ad_library = re.search(r"/ad_library/\?id=(\d+)", raw, re.IGNORECASE)
    if ad_library:
        return accepted_object_id(ad_library.group(1))
    stories_settings = re.search(r"/stories/settings/\?id=(\d+)", raw, re.IGNORECASE)
    if stories_settings:
        return accepted_object_id(stories_settings.group(1))
    pages_promote = re.search(r"/pages/promote/\?id=(\d+)", raw, re.IGNORECASE)
    if pages_promote:
        return accepted_object_id(pages_promote.group(1))
    memories = re.search(r"/memories/\?id=(\d+)", raw, re.IGNORECASE)
    if memories:
        return accepted_object_id(memories.group(1))
    recyclebin = re.search(r"/recyclebin/\?id=(\d+)", raw, re.IGNORECASE)
    if recyclebin:
        return accepted_object_id(recyclebin.group(1))
    rights_manager = re.search(r"/rights_manager/\?id=(\d+)", raw, re.IGNORECASE)
    if rights_manager:
        return accepted_object_id(rights_manager.group(1))
    contacts_upload = re.search(r"/contacts_upload/\?id=(\d+)", raw, re.IGNORECASE)
    if contacts_upload:
        return accepted_object_id(contacts_upload.group(1))
    onthisday = re.search(r"/onthisday/\?id=(\d+)", raw, re.IGNORECASE)
    if onthisday:
        return accepted_object_id(onthisday.group(1))
    audience_insights = re.search(r"/audience_insights/\?id=(\d+)", raw, re.IGNORECASE)
    if audience_insights:
        return accepted_object_id(audience_insights.group(1))
    partnership_ads = re.search(r"/partnership_ads/\?id=(\d+)", raw, re.IGNORECASE)
    if partnership_ads:
        return accepted_object_id(partnership_ads.group(1))
    crisis_response = re.search(r"/crisis_response/\?id=(\d+)", raw, re.IGNORECASE)
    if crisis_response:
        return accepted_object_id(crisis_response.group(1))
    family_center = re.search(r"/family_center/\?id=(\d+)", raw, re.IGNORECASE)
    if family_center:
        return accepted_object_id(family_center.group(1))
    accessibility = re.search(r"/accessibility/\?id=(\d+)", raw, re.IGNORECASE)
    if accessibility:
        return accepted_object_id(accessibility.group(1))
    neighborhoods = re.search(r"/neighborhoods/\?id=(\d+)", raw, re.IGNORECASE)
    if neighborhoods:
        return accepted_object_id(neighborhoods.group(1))
    parental_supervision = re.search(r"/parental_supervision/\?id=(\d+)", raw, re.IGNORECASE)
    if parental_supervision:
        return accepted_object_id(parental_supervision.group(1))
    time_management = re.search(r"/time_management/\?id=(\d+)", raw, re.IGNORECASE)
    if time_management:
        return accepted_object_id(time_management.group(1))
    notification_settings = re.search(r"/notification_settings/\?id=(\d+)", raw, re.IGNORECASE)
    if notification_settings:
        return accepted_object_id(notification_settings.group(1))
    messenger_media = re.search(r"[?&]asset_id=(\d+)", raw, re.IGNORECASE)
    if messenger_media and re.search(r"messenger_media", raw, re.IGNORECASE):
        return accepted_object_id(messenger_media.group(1))
    # Prefer the photo/post fbid over the album set id when both appear.
    query = re.search(r"(?:story_fbid|fbid|post_id)=([^&\s]+)", raw, re.IGNORECASE)
    if query:
        return accepted_object_id(unquote(query.group(1)).rstrip("="))
    multi = re.search(r"[?&]multi_permalinks=(\d+)", raw, re.IGNORECASE)
    if multi:
        return accepted_object_id(multi.group(1))
    stories = re.search(r"/stories/(\d+)", raw, re.IGNORECASE)
    if stories:
        return accepted_object_id(stories.group(1))
    media = re.search(
        r"[?&]set=(?:a|pcb|o|gm|oa|pb|ms|t|go|ft|s|r|h|c|e|p|vb|n|q|v|y|i|z|u|w|x|k|d|m|l|b|j|f)\.(\d+)",
        raw,
        re.IGNORECASE,
    )
    if media:
        return accepted_object_id(media.group(1))
    path = re.search(
        r"(?:posts|permalink|videos|reels?|photos|events)/(pfbid[A-Za-z0-9]+|\d+(?:_\d+)*)",
        raw,
        re.IGNORECASE,
    )
    if path:
        return accepted_object_id(path.group(1))
    permalink = re.search(r"[?&]view=permalink(?:[^#]*?)[?&]id=(\d+)", raw, re.IGNORECASE)
    if permalink:
        return accepted_object_id(permalink.group(1))
    watch = re.search(r"[?&]v=(\d+)", raw)
    if watch:
        return accepted_object_id(watch.group(1))
    return None


async def scan(url: str, *, limit: int = 50, timeout: float = 30.0, **_: object) -> EngineResult:
    if not url.strip():
        return EngineResult(NAME, RANK, False, error="missing_url")
    if invalid_limit(limit):
        return EngineResult(NAME, RANK, False, error="invalid_limit")
    token = access_token()
    if not token:
        return EngineResult(
            NAME,
            RANK,
            False,
            error="missing_GRAPH_ACCESS_TOKEN",
            note="Needs a Page or system-user token with Page Public Content Access.",
        )
    version = graph_version()
    if not _VERSION.match(version):
        return EngineResult(NAME, RANK, False, error="invalid_GRAPH_VERSION")
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            object_id = accepted_object_id(await _resolve_id(client, version, token, url))
            if not object_id:
                return EngineResult(NAME, RANK, False, error="unresolved_post_id")
            resp = await client.get(
                f"https://graph.facebook.com/{version}/{object_id}/comments",
                params={
                    "fields": "message,from,created_time",
                    "limit": str(min(limit, 100)),
                    "access_token": token,
                },
            )
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))

    try:
        data = resp.json()
    except Exception as exc:
        return EngineResult(NAME, RANK, False, error=public_error(exc))
    comments, error = comments_from_graph_payload(data, limit)
    if resp.status_code >= 400 and not comments:
        if error in {None, "empty_comment_edge", "unexpected_graph_payload"}:
            error = redact_text(f"http_{resp.status_code}: {resp.text}", 220)
        return EngineResult(NAME, RANK, False, error=error)
    return EngineResult(
        NAME,
        RANK,
        bool(comments),
        comments=comments,
        error=None if comments else error,
        note="PPCA may omit commenter ids unless the token can moderate the Page.",
    )


async def _resolve_id(client: httpx.AsyncClient, version: str, token: str, url: str) -> str | None:
    direct = post_id_from_url(url)
    resp = await client.get(
        f"https://graph.facebook.com/{version}/",
        params={"id": url.strip(), "access_token": token},
    )
    if resp.status_code < 400:
        try:
            body = resp.json()
        except Exception:
            body = None
        og = body.get("og_object") if isinstance(body, dict) else None
        if isinstance(og, dict):
            found = accepted_object_id(og.get("id"))
            if found:
                return found
        if isinstance(body, dict) and "error" not in body:
            found = accepted_object_id(body.get("id"))
            if found:
                return found
    return accepted_object_id(direct)
