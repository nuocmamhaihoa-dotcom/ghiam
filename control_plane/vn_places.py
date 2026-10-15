"""Tỉnh, huyện, xã Việt Nam — đơn vị cũ và mới, có dấu và không dấu."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

NAME_MAX_WORDS = 6
ADDRESS_MIN_WORDS = 9
ADDRESS_MIN_CHARS = 29
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_ADMIN_HEAD = re.compile(
    r"^(tinh|thanh pho|tp|huyen|quan|q|thi xa|thi tran|xa|phuong|p)\s+"
)
_NUMBERED_UNIT = re.compile(r"^(quan|q|phuong|p)\s*\d{1,2}$")
_DATA_PATH = Path(__file__).resolve().parent / "data" / "vn_places.txt"


def _fold_local(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    folded = _PUNCT.sub(" ", stripped.casefold())
    return _SPACES.sub(" ", folded).strip()


@lru_cache(maxsize=1)
def _tables() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    provinces: set[str] = set()
    districts: set[str] = set()
    communes: set[str] = set()
    raw = _DATA_PATH.read_text(encoding="utf-8")
    for line in raw.splitlines():
        if not line or "\t" not in line:
            continue
        kind, name = line.split("\t", 1)
        key = _fold_local(name)
        if not key:
            continue
        if kind == "P":
            provinces.add(key)
        elif kind == "D":
            districts.add(key)
        elif kind == "C":
            communes.add(key)
    for index in range(1, 13):
        districts.add(f"quan {index}")
        districts.add(f"q {index}")
    return frozenset(provinces), frozenset(districts), frozenset(communes)


def provinces() -> frozenset[str]:
    return _tables()[0]


def districts() -> frozenset[str]:
    return _tables()[1]


def communes() -> frozenset[str]:
    return _tables()[2]


def _strip_admin(folded: str) -> str:
    text = folded
    while True:
        nxt = _ADMIN_HEAD.sub("", text)
        if nxt == text:
            return text
        text = nxt


def _token_has(tokens: list[str], phrases: frozenset[str], max_span: int = 6) -> bool:
    count = len(tokens)
    for start in range(count):
        chunk: list[str] = []
        for width in range(max_span):
            index = start + width
            if index >= count:
                break
            chunk.append(tokens[index])
            if " ".join(chunk) in phrases:
                return True
    return False


def is_place_cell(folded: str) -> bool:
    """Cả ô là tên tỉnh/thành, huyện, hoặc xã/phường có tiền tố."""
    if not folded:
        return False
    if _NUMBERED_UNIT.match(folded):
        return True
    bare = _strip_admin(folded)
    if bare in provinces() or bare in districts():
        return True
    if folded != bare and bare in communes():
        return True
    if folded.startswith(("xa ", "phuong ", "thi tran ")) and bare:
        return True
    if folded.startswith(("huyen ", "quan ", "thi xa ", "tinh ", "thanh pho ", "tp ")):
        return True
    return False


def has_place_name(folded: str) -> bool:
    tokens = folded.split()
    if not tokens:
        return False
    if is_place_cell(folded):
        return True
    if _token_has(tokens, provinces()) or _token_has(tokens, districts()):
        return True
    if any(token in {"huyen", "xa", "phuong", "tinh"} or token.startswith("q") and token[1:].isdigit() for token in tokens):
        if _token_has(tokens, communes()) or _token_has(tokens, districts()) or _token_has(tokens, provinces()):
            return True
    return False
