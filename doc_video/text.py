"""Chuẩn hoá số điện thoại, @id, tên; ghép các lần đọc của cùng một dòng chữ."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

# Đầu số di động Việt Nam đang cấp.
MOBILE_PREFIXES = frozenset(
    "032 033 034 035 036 037 038 039 052 055 056 058 059 070 076 077 078 079 "
    "081 082 083 084 085 086 087 088 089 090 091 092 093 094 096 097 098 099".split()
)
# Chữ cái mà bộ đọc hay nhầm với chữ số khi đứng trong một dãy số.
DIGIT_LOOKALIKE = str.maketrans({"ó": "6", "ò": "6", "Ó": "6", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5"})
HANDLE_RE = re.compile(r"@([A-Za-z0-9._]{2,24})")
SCREEN_WORDS = {"follow", "follower", "thich", "tin nhan", "da follow", "danh ba", "thong bao he thong"}


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt, giữ nguyên số ký tự."""
    decomposed = unicodedata.normalize("NFD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return plain.replace("đ", "d").replace("Đ", "D")


def name_key(name: str) -> str:
    """Khoá so tên: không phân biệt hoa thường, dấu, khoảng trắng, ký tự lạ."""
    return "".join(ch for ch in fold(name).casefold() if ch.isalnum())


def clean_name(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text or ""))
    kept = "".join(ch if ch.isalnum() or ch in "._-" else " " for ch in text)
    return " ".join(kept.split())


def is_name(text: str) -> bool:
    folded = " ".join(fold(text).casefold().split())
    if folded in SCREEN_WORDS:
        return False
    return sum(ch.isalpha() for ch in folded) >= 2


def digits_of(text: str) -> str:
    return "".join(ch for ch in str(text or "").translate(DIGIT_LOOKALIKE) if ch.isdigit())


def looks_like_phone(text: str) -> bool:
    """Dòng gần như toàn chữ số, 9 đến 11 số: dòng số điện thoại trên màn hình danh bạ."""
    compact = "".join(str(text or "").split())
    digits = digits_of(compact)
    return 9 <= len(digits) <= 11 and len(digits) >= 0.8 * len(compact)


def normalize_phone(text: str) -> str:
    digits = digits_of(text)
    if len(digits) == 11 and digits.startswith("84"):
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits[:3] in MOBILE_PREFIXES:
        return digits
    return ""


def handle_matches_name(handle: str, name: str) -> bool:
    """@id sinh từ tên danh bạ thì là cùng một người, dù tên hiển thị trên hồ sơ khác.

    Khớp khi phần chữ của @id trùng tên, hoặc hai chuỗi gần như chứa nhau.
    Tên ngắn nằm trong tên dài hơn (Linh trong Kim Linh) không tính.
    """
    folded = name_key(name)
    compact = "".join(ch for ch in str(handle or "").lstrip("@").casefold() if ch.isalnum())
    if len(folded) < 3 or len(compact) < 3:
        return False
    if _same_text(folded, compact):
        return True
    letters = "".join(ch for ch in folded if ch.isalpha())
    prefix: list[str] = []
    for ch in compact:
        if not ch.isalpha():
            break
        prefix.append(ch)
    prefix_text = "".join(prefix)
    if len(prefix_text) >= 3 and prefix_text == letters:
        return True
    handle_letters = "".join(ch for ch in compact if ch.isalpha())
    if len(letters) >= 3 and letters == handle_letters:
        return True
    if len(letters) >= 8 and len(handle_letters) >= 8:
        return SequenceMatcher(None, letters, handle_letters).ratio() >= 0.9
    return False


def _same_text(left: str, right: str) -> bool:
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if short not in long:
        return False
    return len(short) >= 6 or len(short) >= 0.72 * len(long)


def find_handle(text: str) -> str:
    match = HANDLE_RE.search("".join(str(text or "").split()))
    if not match:
        return ""
    handle = match.group(1).rstrip(".")
    if len(handle) < 2 or handle.startswith(".") or not any(ch.isalpha() for ch in handle):
        return ""
    return "@" + handle


def vote(reads: list[str]) -> str:
    """Giá trị được nhiều lần đọc nhất; hoà thì lấy lần đọc đứng trước (đáng tin hơn)."""
    present = [item for item in reads if item]
    if not present:
        return ""
    counts = Counter(present)
    top = max(counts.values())
    return next(item for item in present if counts[item] == top)


def _has_marks(token: str) -> bool:
    return fold(token) != token


def merge_name(vie_reads: list[str], ascii_read: str) -> str:
    """Ghép tên từ lần đọc tiếng Việt và lần đọc chỉ chữ không dấu.

    Bộ tiếng Việt giữ đúng dấu nhưng hay sai hoa thường và chữ số (Truc thành
    TrUC, 6 thành ó). Bộ không dấu đọc đúng hoa thường và số nhưng làm hỏng
    chữ có dấu (Phước thành Phudc).

    So hai bản theo từng ký tự sau khi bỏ dấu. Chỗ khớp: lấy ký tự tiếng Việt
    (giữ dấu) với hoa thường của bản không dấu. Chỗ lệch: bản không dấu là chữ
    số, hoặc bản tiếng Việt không có dấu ở đó, thì lấy bản không dấu; còn lại
    giữ bản tiếng Việt.
    """
    vie = next((clean_name(item) for item in vie_reads if clean_name(item)), "")
    plain = clean_name(ascii_read)
    if not vie or not plain:
        return vie or plain
    folded = fold(vie)
    out: list[str] = []
    matcher = SequenceMatcher(None, folded.casefold(), plain.casefold(), autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        vie_part = vie[a0:a1]
        plain_part = plain[b0:b1]
        if op == "equal":
            for vie_ch, plain_ch in zip(vie_part, plain_part):
                out.append(vie_ch if _has_marks(vie_ch) else plain_ch)
            continue
        plain_is_digits = bool(plain_part.strip()) and all(ch.isdigit() or ch.isspace() for ch in plain_part)
        if plain_is_digits or not _has_marks(vie_part):
            out.append(plain_part)
        else:
            out.append(vie_part)
    return " ".join("".join(out).split())
