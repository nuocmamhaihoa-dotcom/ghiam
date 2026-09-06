"""Vietnamese transcript normalization and dialect detection (AIE V2)."""
from __future__ import annotations

import re

# Keep original meaning — expand abbreviations / chat slang only.
ABBREV = {
    "ko": "không",
    "k": "không",
    "kg": "không",
    "hok": "không",
    "hông": "không",
    "hong": "không",
    "dc": "được",
    "đc": "được",
    "đk": "được",
    "ntn": "như thế nào",
    "nx": "nữa",
    "vs": "với",
    "sp": "sản phẩm",
    "bh": "bảo hành",
    "ck": "chuyển khoản",
    "tt": "thanh toán",
    "đt": "điện thoại",
    "kh": "khách hàng",
    "nv": "nhân viên",
    "okela": "ok",
    "oke": "ok",
    "r": "rồi",
    "j": "gì",
    "wa": "quá",
    "wá": "quá",
    "hnay": "hôm nay",
    "nma": "nhưng mà",
    "nm": "nhưng mà",
    "tks": "cảm ơn",
}

FILLERS = ("à", "ờ", "ừm", "ờm", "hmm", "á", "ơ")

NORTH = ("chứ", "nhỉ", "ơ", "thế à", "thế nhỉ", "ấy", "cơ")
CENTRAL = ("mi", "răng", "chi", "nớ", "rứa", "mô")
SOUTH = ("hông", "hen", "vậy đó", "nè", "zậy", "hem")


def detect_dialect(text: str) -> str:
    t = (text or "").lower()
    scores = {
        "north": sum(1 for x in NORTH if x in t),
        "central": sum(1 for x in CENTRAL if x in t),
        "south": sum(1 for x in SOUTH if x in t),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


def normalize_vietnamese(text: str) -> str:
    """Normalize spelling/abbreviations without dropping meaning. Keep original separately."""
    raw = (text or "").strip()
    if not raw:
        return ""
    tokens = re.split(r"(\s+)", raw)
    out: list[str] = []
    for tok in tokens:
        if not tok or tok.isspace():
            out.append(tok)
            continue
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok)
        if not m:
            out.append(ABBREV.get(tok.lower(), tok))
            continue
        lead, core, trail = m.groups()
        repl = ABBREV.get(core.lower(), core)
        out.append(f"{lead}{repl}{trail}")
    normalized = "".join(out)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip()
    normalized = normalized.replace("..", "…")
    for f in FILLERS:
        normalized = re.sub(
            rf"\b{re.escape(f)}(\s+{re.escape(f)})+\b",
            f,
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized
