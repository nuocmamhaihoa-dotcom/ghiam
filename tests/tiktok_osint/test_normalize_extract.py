from __future__ import annotations

from tiktok_osint.domain.dedupe import annotate_cross_profile, dedupe_contacts
from tiktok_osint.domain.extract import extract_contacts
from tiktok_osint.domain.normalize import is_vn_mobile, normalize_phone, parse_profile_input, public_profile_url
from tiktok_osint.errors import InvalidProfileInput


def test_parse_profile_inputs() -> None:
    parsed = parse_profile_input("  @publicshop  ")
    assert parsed.username == "publicshop"
    assert parsed.profile_url == "https://www.tiktok.com/@publicshop"
    assert parsed.reject_reason is None

    url = parse_profile_input("https://www.tiktok.com/@publicshop?lang=en")
    assert url.username == "publicshop"
    assert url.profile_url == "https://www.tiktok.com/@publicshop"

    short = parse_profile_input("https://vm.tiktok.com/ZMabc/")
    assert short.short_link is True
    assert short.username is None

    rejected = parse_profile_input("https://www.tiktok.com/foryou")
    assert rejected.reject_reason
    video = parse_profile_input("https://www.tiktok.com/@publicshop/video/1")
    assert video.reject_reason


def test_public_profile_url_rejects_path_tricks() -> None:
    try:
        public_profile_url("../admin")
    except InvalidProfileInput:
        return
    raise AssertionError("expected invalid username")


def test_phone_normalization() -> None:
    assert normalize_phone("0901.234.567") == "+84901234567"
    assert normalize_phone("+84 901 234 567") == "+84901234567"
    assert is_vn_mobile("+84901234567")
    assert normalize_phone("123") is None


def test_extract_public_bio_contacts() -> None:
    bio = (
        "Liên hệ Zalo 0901.234.567 | email: shop@example.com\n"
        "FB: facebook.com/myshop\n"
        "IG instagram.com/myshop\n"
        "YouTube: youtube.com/@myshop\n"
        "Web: https://myshop.vn?utm_source=tiktok"
    )
    contacts = { (item.kind, item.normalized_value): item for item in extract_contacts(bio, source="bio") }
    assert contacts[("phone", "+84901234567")].confidence == 0.9
    assert contacts[("zalo", "+84901234567")].confidence == 0.91
    assert contacts[("email", "shop@example.com")].confidence == 0.94
    assert ("facebook", "https://facebook.com/myshop") in contacts
    assert ("instagram", "https://instagram.com/myshop") in contacts
    assert ("youtube", "https://youtube.com/@myshop") in contacts
    assert contacts[("website", "https://myshop.vn")].normalized_value == "https://myshop.vn"


def test_ocr_source_lowers_confidence_and_dedupes() -> None:
    bio = extract_contacts("email shop@example.com", source="bio")
    ocr = extract_contacts("shop@example.com", source="ocr_avatar")
    merged = dedupe_contacts(bio + ocr)
    assert len(merged) == 1
    assert merged[0].confidence == 0.94
    assert "shop@example.com" in merged[0].evidence


def test_contact_book_csv_and_vcard() -> None:
    from tiktok_osint.sync.book import parse_contact_csv, parse_vcard

    rows = parse_contact_csv("name,phone,email\nAn,0901234567,a@b.co\n")
    assert rows[0].phone_e164 == "+84901234567"
    assert rows[0].email == "a@b.co"
    cards = parse_vcard("BEGIN:VCARD\nFN:Binh\nTEL:0987000111\nEMAIL:b@b.co\nEND:VCARD\n")
    assert cards[0].display_name == "Binh"
    assert cards[0].phone_e164 == "+84987000111"


def test_cross_profile_annotation() -> None:
    rows = annotate_cross_profile(
        [
            {"username": "a", "contact_kind": "phone", "contact_value": "+84901234567"},
            {"username": "b", "contact_kind": "phone", "contact_value": "+84901234567"},
        ]
    )
    assert rows[0]["also_seen_on"] == "b"
    assert rows[1]["also_seen_on"] == "a"
