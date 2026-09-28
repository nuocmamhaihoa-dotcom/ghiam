from __future__ import annotations

import re

from tiktok_osint.domain.models import (
    EMAIL,
    FACEBOOK,
    INSTAGRAM,
    PHONE,
    WEBSITE,
    YOUTUBE,
    ZALO,
    ContactCandidate,
)
from tiktok_osint.domain.normalize import canonicalize_url, is_vn_mobile, normalize_email, normalize_phone
from tiktok_osint.domain.score import finalize_confidence

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d[\d\s.\-]{7,18}\d|00\d[\d\s.\-]{7,18}\d|0\d(?:[\s.\-]?\d){8,9})(?!\d)"
)
_BARE_SOCIAL = [
    (FACEBOOK, re.compile(r"(?:https?://)?(?:www\.)?(?:facebook|fb)\.com/[A-Za-z0-9.\-_]+", re.IGNORECASE)),
    (INSTAGRAM, re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/[A-Za-z0-9._]+", re.IGNORECASE)),
    (
        YOUTUBE,
        re.compile(
            r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:@[A-Za-z0-9_\-]+|(?:channel|c)/[A-Za-z0-9_\-]+)|youtu\.be/[A-Za-z0-9_\-]+)",
            re.IGNORECASE,
        ),
    ),
    (ZALO, re.compile(r"(?:https?://)?(?:www\.)?zalo\.me/[A-Za-z0-9.\-_]+", re.IGNORECASE)),
]
_KEYWORD_RE = re.compile(
    r"zalo|sđt|sdt|phone|tel|hotline|whatsapp|liên\s*hệ|lien\s*he|contact|\blh\b|call",
    re.IGNORECASE,
)
_SOCIAL_HOSTS = {
    "facebook.com": FACEBOOK,
    "fb.com": FACEBOOK,
    "instagram.com": INSTAGRAM,
    "youtube.com": YOUTUBE,
    "youtu.be": YOUTUBE,
    "zalo.me": ZALO,
}
_SKIP_HOSTS = frozenset({"tiktok.com", "vm.tiktok.com", "vt.tiktok.com"})


def extract_contacts(text: str | None, *, source: str) -> list[ContactCandidate]:
    if not text or not text.strip():
        return []
    found: list[ContactCandidate] = []
    found.extend(_emails(text, source))
    found.extend(_phones(text, source))
    found.extend(_links(text, source))
    return found


def _emails(text: str, source: str) -> list[ContactCandidate]:
    rows: list[ContactCandidate] = []
    for match in _EMAIL_RE.finditer(text):
        normalized = normalize_email(match.group(0))
        if not normalized:
            continue
        rows.append(
            _candidate(
                kind=EMAIL,
                raw_value=match.group(0),
                normalized=normalized,
                source=source,
                base=0.94,
                text=text,
                start=match.start(),
                end=match.end(),
            )
        )
    return rows


def _phones(text: str, source: str) -> list[ContactCandidate]:
    rows: list[ContactCandidate] = []
    for match in _PHONE_RE.finditer(text):
        normalized = normalize_phone(match.group(0))
        if not normalized:
            continue
        keyword = _keyword_near(text, match.start(), match.end())
        window = _window(text, match.start(), match.end()).lower()
        vn = is_vn_mobile(normalized)
        if keyword is None and not vn:
            continue
        if not vn and not normalized.startswith("+"):
            continue
        base = 0.9 if keyword and vn else 0.84 if keyword else 0.62
        rows.append(
            _candidate(
                kind=PHONE,
                raw_value=match.group(0).strip(),
                normalized=normalized,
                source=source,
                base=base,
                text=text,
                start=match.start(),
                end=match.end(),
            )
        )
        if "zalo" in window:
            rows.append(
                _candidate(
                    kind=ZALO,
                    raw_value=match.group(0).strip(),
                    normalized=normalized,
                    source=source,
                    base=0.91 if vn else 0.8,
                    text=text,
                    start=match.start(),
                    end=match.end(),
                )
            )
    return rows


def _links(text: str, source: str) -> list[ContactCandidate]:
    rows: list[ContactCandidate] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in _BARE_SOCIAL:
        for match in pattern.finditer(text):
            _push_url(rows, seen, match.group(0), kind, source, text, match.start(), match.end(), 0.93)
    for match in _URL_RE.finditer(text):
        canonical = canonicalize_url(match.group(0))
        if not canonical:
            continue
        host = _host(canonical)
        if _is_tiktok(host):
            continue
        kind = _kind_for_host(host)
        base = 0.92 if kind != WEBSITE else 0.8
        _push_url(rows, seen, match.group(0), kind, source, text, match.start(), match.end(), base)
    return rows


def _push_url(
    rows: list[ContactCandidate],
    seen: set[tuple[str, str]],
    raw: str,
    kind: str,
    source: str,
    text: str,
    start: int,
    end: int,
    base: float,
) -> None:
    canonical = canonicalize_url(raw)
    if not canonical:
        return
    key = (kind, canonical)
    if key in seen:
        return
    seen.add(key)
    rows.append(
        _candidate(
            kind=kind,
            raw_value=raw.strip(),
            normalized=canonical,
            source=source,
            base=base,
            text=text,
            start=start,
            end=end,
        )
    )


def _candidate(
    *,
    kind: str,
    raw_value: str,
    normalized: str,
    source: str,
    base: float,
    text: str,
    start: int,
    end: int,
) -> ContactCandidate:
    left = max(0, start - 36)
    right = min(len(text), end + 36)
    evidence = " ".join(text[left:right].split())
    return ContactCandidate(
        kind=kind,
        raw_value=raw_value,
        normalized_value=normalized,
        source=source,
        confidence=finalize_confidence(base, source),
        evidence=evidence[:180],
    )


def _window(text: str, start: int, end: int, window: int = 40) -> str:
    return text[max(0, start - window) : min(len(text), end + window)]


def _keyword_near(text: str, start: int, end: int, window: int = 40) -> str | None:
    match = _KEYWORD_RE.search(_window(text, start, end, window))
    return match.group(0) if match else None


def _host(canonical: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(canonical).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_tiktok(host: str) -> bool:
    return host in _SKIP_HOSTS or host.endswith(".tiktok.com")


def _kind_for_host(host: str) -> str:
    for suffix, kind in _SOCIAL_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return kind
    return WEBSITE
