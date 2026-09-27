from __future__ import annotations

from tiktok_osint.domain.normalize import normalize_phone, parse_profile_input
from tiktok_osint.errors import InvalidProfileInput, ReverseLookupForbidden, ValidationError


def normalize_displayed_username(raw: str) -> str:
    """Username taken from a result TikTok already showed the user. Not a phone lookup."""
    text = raw.strip()
    if normalize_phone(text) and not text.startswith("@") and "tiktok.com" not in text.lower():
        raise ReverseLookupForbidden()
    parsed = parse_profile_input(text)
    if parsed.short_link or not parsed.username:
        raise InvalidProfileInput(
            parsed.reject_reason or "Cần username TikTok đã hiển thị, không phải số điện thoại"
        )
    return parsed.username


def reconciliation_rows(
    matches: list[dict[str, object]],
    profiles_by_username: dict[str, dict[str, object]],
    contacts_by_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Join recorded official usernames to public profiles. Phone is never a key."""
    rows: list[dict[str, object]] = []
    for match in matches:
        username = str(match["tiktok_username"])
        profile = profiles_by_username.get(username)
        contact_id = match.get("user_contact_id")
        contact = contacts_by_id.get(str(contact_id)) if contact_id else None
        rows.append(
            {
                "match_id": match["id"],
                "tiktok_username": username,
                "display_name_shown": match.get("display_name_shown"),
                "note": match.get("note"),
                "contact": _public_contact_view(contact),
                "public_profile": _public_profile_view(profile),
                "matched_public_profile": profile is not None,
                "match_key": "tiktok_username",
            }
        )
    return rows


def assert_record_payload(username: str, display_name_shown: str | None) -> str:
    if display_name_shown is not None and len(display_name_shown) > 200:
        raise ValidationError("Tên hiển thị quá dài")
    return normalize_displayed_username(username)


def _public_contact_view(contact: dict[str, object] | None) -> dict[str, object] | None:
    if contact is None:
        return None
    return {
        "id": contact["id"],
        "display_name": contact["display_name"],
        "phone_e164": contact.get("phone_e164"),
        "email": contact.get("email"),
    }


def _public_profile_view(profile: dict[str, object] | None) -> dict[str, object] | None:
    if profile is None:
        return None
    return {
        "username": profile["username"],
        "nickname": profile.get("nickname"),
        "bio": profile.get("bio"),
        "followers": profile.get("followers"),
        "likes": profile.get("likes"),
        "verified": profile.get("verified"),
        "bio_link": profile.get("bio_link"),
        "private_account": profile.get("private_account"),
    }
