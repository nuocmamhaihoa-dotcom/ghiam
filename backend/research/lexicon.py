"""Lexicon helpers for novelty / clustering."""
from __future__ import annotations

import re
import unicodedata

KNOWN_OBJECTIONS = {
    "de em suy nghi", "de em coi", "de em xem", "goi lai sau", "dat qua", "gia cao",
    "dang dung ben khac", "khong co nhu cau", "de anh chi ban voi nha",
}
KNOWN_BUYING_SIGNALS = {
    "chuyen khoan", "gui stk", "lay bao nhieu", "ship ve", "chot don", "thanh toan",
    "gui hop dong", "dat coc",
}
KNOWN_CLOSINGS = {"chot don", "xac nhan don", "gui hang", "thanh toan xong"}
KNOWN_EMOTIONS = {"tuc gian", "buc minh", "vui qua", "ngai qua", "so lan", "met qua", "do du"}


def normalize(text: str) -> str:
    text = (text or "").replace("đ", "d").replace("Đ", "d").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if len(t) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def novelty_score(text: str, known: set[str]) -> float:
    norm = normalize(text)
    if not norm:
        return 0.0
    if norm in known:
        return 0.05
    best = max((jaccard(norm, k) for k in known), default=0.0)
    return round(max(0.0, min(1.0, 1.0 - best)), 4)

# aliases
jaccard = jaccard
KNOWN_OBJECTIONS = KNOWN_OBJECTIONS
normalize = normalize
