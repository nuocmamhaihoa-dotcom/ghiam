"""Chuẩn hoá nội dung, thời điểm và số lượt thích của comment công khai."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime, timedelta

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_SUFFIXES: tuple[tuple[str, int], ...] = (
    ("triệu", 1_000_000),
    ("trieu", 1_000_000),
    ("nghìn", 1_000),
    ("nghin", 1_000),
    ("tỷ", 1_000_000_000),
    ("ty", 1_000_000_000),
    ("tr", 1_000_000),
    ("k", 1_000),
    ("n", 1_000),
    ("m", 1_000_000),
    ("b", 1_000_000_000),
    ("t", 1_000_000_000),
)
_UNITS: tuple[tuple[str, int], ...] = (
    (r"phút|phut|mins|min|minutes|minute", 60),
    (r"giờ|gio|hours|hour|hrs|hr", 3600),
    (r"ngày|ngay|days|day", 86400),
    (r"tuần|tuan|weeks|week", 7 * 86400),
    (r"tháng|thang|months|month|mo", 30 * 86400),
    (r"năm|nam|years|year", 365 * 86400),
    (r"h", 3600),
    (r"d", 86400),
    (r"w", 7 * 86400),
    (r"m", 60),
    (r"y", 365 * 86400),
)
_RELATIVE = re.compile(
    r"^(?:khoảng\s+|about\s+)?(\d+)\s*(" + "|".join(pattern for pattern, _ in _UNITS) + r")(?:\s+trước|\s+ago)?$",
    re.IGNORECASE,
)
_BARE_UNIT = dict(_UNITS)


def clean_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value).translate(_INVISIBLE)
    return re.sub(r"\s+", " ", text).strip()


def stable_id(author: str, text: str, time_raw: str | None, parent_id: str | None) -> str:
    raw = "\n".join((author, text, time_raw or "", parent_id or ""))
    return "h:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def parse_likes(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip().lower().replace("\xa0", "").replace(" ", "")
    if not text or text in {"thích", "like", "likes"}:
        return None
    multiplier = 1
    for suffix, factor in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            multiplier = factor
            text = text[: -len(suffix)]
            break
    if multiplier != 1:
        number = text.replace(",", ".")
        if number.count(".") > 1:
            number = number.replace(".", "")
        try:
            return int(float(number) * multiplier)
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):
        return int(re.sub(r"[.,]", "", text))
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def parse_time(raw: str | None, now: datetime) -> datetime | None:
    if raw is None:
        return None
    text = clean_text(raw)
    if not text:
        return None
    if text.isdigit() and len(text) >= 10:
        return datetime.fromtimestamp(int(text), UTC)
    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    folded = text.casefold()
    if folded in {"vừa xong", "vừa mới", "vừa mới đây", "just now"}:
        return now
    if folded in {"hôm qua", "yesterday"}:
        return now - timedelta(days=1)
    if folded in {"hôm kia"}:
        return now - timedelta(days=2)
    match = _RELATIVE.match(text)
    if match is None:
        return None
    amount = int(match.group(1))
    unit = match.group(2).casefold()
    seconds = _BARE_UNIT.get(unit)
    if seconds is None:
        for pattern, value in _UNITS:
            if re.fullmatch(pattern, unit, re.IGNORECASE):
                seconds = value
                break
    if seconds is None:
        return None
    return now - timedelta(seconds=amount * seconds)
