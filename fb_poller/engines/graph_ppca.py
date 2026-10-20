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
        r"[?&]set=(?:a|pcb|o|gm|oa|pb|ms|t|go|ft|s|r|h|c|e|p|vb|n)\.(\d+)",
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
