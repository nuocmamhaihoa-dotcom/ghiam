from __future__ import annotations

from tiktok_osint.domain.models import CONTACT_SOURCES

SOURCE_FACTOR = {
    "bio": 1.0,
    "bio_link": 0.9,
    "ocr_avatar": 0.75,
}


def finalize_confidence(base: float, source: str) -> float:
    if source not in CONTACT_SOURCES:
        raise ValueError(f"Nguồn liên hệ không hỗ trợ: {source}")
    value = max(0.0, min(1.0, base * SOURCE_FACTOR[source]))
    return round(value, 3)
