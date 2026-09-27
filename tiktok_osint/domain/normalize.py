from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from tiktok_osint.domain.models import ParsedProfileInput
from tiktok_osint.errors import InvalidProfileInput

_USERNAME = re.compile(r"^[A-Za-z0-9._]{2,24}$")
PROFILE_HOSTS = frozenset({"www.tiktok.com", "tiktok.com", "m.tiktok.com"})
SHORT_HOSTS = frozenset({"vm.tiktok.com", "vt.tiktok.com"})
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "igshid",
        "si",
        "_r",
        "lang",
        "is_from_webapp",
        "sender_device",
    }
)


def valid_username(username: str) -> bool:
    return bool(_USERNAME.fullmatch(username))


def public_profile_url(username: str) -> str:
    if not valid_username(username):
        raise InvalidProfileInput(f"Username không hợp lệ: {username!r}")
    return f"https://www.tiktok.com/@{username}"


def parse_profile_input(raw: str) -> ParsedProfileInput:
    text = raw.strip()
    if not text or text.startswith("#"):
        return ParsedProfileInput(raw, None, None, False, "Dòng trống hoặc ghi chú")

    if text.startswith("@") or ("://" not in text and "/" not in text):
        username = text[1:] if text.startswith("@") else text
        if not valid_username(username):
            return ParsedProfileInput(text, None, None, False, "Username không hợp lệ")
        return ParsedProfileInput(text, username, public_profile_url(username), False, None)

    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host in SHORT_HOSTS:
        if parsed.scheme not in {"http", "https"}:
            return ParsedProfileInput(text, None, None, True, "Short link phải là http(s)")
        clean = urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
        return ParsedProfileInput(text, None, clean, True, None)

    if host in PROFILE_HOSTS:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 1 and parts[0].startswith("@"):
            username = parts[0][1:]
            if valid_username(username):
                return ParsedProfileInput(
                    text,
                    username,
                    public_profile_url(username),
                    False,
                    None,
                )
        return ParsedProfileInput(
            text,
            None,
            None,
            False,
            "Chỉ nhận URL hồ sơ công khai dạng https://www.tiktok.com/@username",
        )

    return ParsedProfileInput(text, None, None, False, "Không phải hồ sơ TikTok công khai")


def normalize_phone(raw: str) -> str | None:
    compact = re.sub(r"[^\d+]", "", raw.strip())
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+"):
        numeric = compact[1:]
        if numeric.isdigit() and 8 <= len(numeric) <= 15:
            return "+" + numeric
        return None
    if compact.isdigit() and compact.startswith("84") and len(compact) == 11:
        return f"+{compact}"
    if compact.isdigit() and compact.startswith("0") and len(compact) == 10:
        return "+84" + compact[1:]
    return None


def is_vn_mobile(e164: str) -> bool:
    return bool(re.fullmatch(r"\+84[35789]\d{8}", e164))


def normalize_email(raw: str) -> str | None:
    value = raw.strip().strip(".,;:)>\"'").lower()
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", value):
        return None
    return value


def canonicalize_url(raw: str) -> str | None:
    text = raw.strip().strip(".,;:)>\"'")
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in _TRACKING_PARAMS]
    )
    path = parsed.path.rstrip("/") or ""
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))
