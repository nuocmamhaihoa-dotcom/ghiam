
"""Vietnamese transcript normalization."""
from __future__ import annotations
import re

ABBREV = {"ko": "không", "k": "không", "kg": "không", "dc": "được", "đc": "được", "ntn": "như thế nào", "nx": "nữa", "vs": "với", "sp": "sản phẩm", "bh": "bảo hành", "ck": "chuyển khoản"}
NORTH = ("chứ", "nhỉ", "ơ", "thế à")
CENTRAL = ("mi", "răng", "chi", "nớ")
SOUTH = ("hông", "hen", "vậy đó", "nè", "zậy")


def detect_dialect(text: str) -> str:
    t = (text or "").lower()
    scores = {"north": sum(1 for x in NORTH if x in t), "central": sum(1 for x in CENTRAL if x in t), "south": sum(1 for x in SOUTH if x in t)}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


def normalize_vietnamese(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    tokens = re.split(r"(\s+)", raw)
    out = [ABBREV.get(tok.lower(), tok) for tok in tokens]
    return re.sub(r"\s{2,}", " ", "".join(out)).strip().replace("..", "…")
