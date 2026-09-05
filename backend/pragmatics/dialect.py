"""Dialect detection for Vietnamese telesales speech."""

from __future__ import annotations

from collections import Counter

DIALECT_CUES: dict[str, tuple[str, ...]] = {
    "south": (
        "coi",
        "ráng",
        "hen",
        "nha",
        "rồi đó",
        "alpha",
        "trời ơi",
        "mắc",
        "hong",
        "đâu có",
        "vậy đó",
        "okela",
        "dzậy",
    ),
    "north": (
        "nhé",
        "ạ",
        "cháu",
        "bác",
        "đắt",
        "thế",
        "nhỉ",
        "ừ",
        "vâng",
        "em ạ",
        "thế à",
        "có vẻ",
    ),
    "central": (
        "răng",
        "chi",
        "mô",
        "tê",
        "ni",
        "nớ",
        "mi",
        "bôn",
        "chừ",
        "đi mô",
    ),
}


def detect_dialect(text: str, hint: str | None = None) -> str:
    if hint in {"north", "central", "south"}:
        return hint
    lowered = (text or "").lower()
    scores: Counter[str] = Counter()
    for dialect, cues in DIALECT_CUES.items():
        for cue in cues:
            if cue in lowered:
                scores[dialect] += 1
    if not scores:
        return "unknown"
    return scores.most_common(1)[0][0]
