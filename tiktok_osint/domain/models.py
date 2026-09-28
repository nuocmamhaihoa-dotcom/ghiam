from __future__ import annotations

from dataclasses import dataclass

PHONE = "phone"
EMAIL = "email"
ZALO = "zalo"
WEBSITE = "website"
FACEBOOK = "facebook"
INSTAGRAM = "instagram"
YOUTUBE = "youtube"

CONTACT_KINDS = frozenset({PHONE, EMAIL, ZALO, WEBSITE, FACEBOOK, INSTAGRAM, YOUTUBE})

SOURCE_BIO = "bio"
SOURCE_BIO_LINK = "bio_link"
SOURCE_OCR = "ocr_avatar"
CONTACT_SOURCES = frozenset({SOURCE_BIO, SOURCE_BIO_LINK, SOURCE_OCR})


@dataclass(frozen=True)
class ParsedProfileInput:
    raw: str
    username: str | None
    profile_url: str | None
    short_link: bool
    reject_reason: str | None


@dataclass(frozen=True)
class PublicSnapshot:
    username: str
    nickname: str | None
    bio: str | None
    followers: int | None
    following: int | None
    likes: int | None
    verified: bool
    private_account: bool
    avatar_url: str | None
    bio_link: str | None
    source_url: str
    partial: bool = False


@dataclass(frozen=True)
class ContactCandidate:
    kind: str
    raw_value: str
    normalized_value: str
    source: str
    confidence: float
    evidence: str
