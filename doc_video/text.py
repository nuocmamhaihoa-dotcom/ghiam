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
# Cả dòng phụ dưới số điện thoại, không có khoảng trắng: đúng dạng @id TikTok.
_HANDLE_TOKEN = re.compile(r"^[A-Za-z0-9._]{2,24}$")
SCREEN_WORDS = {"follow", "follower", "thich", "tin nhan", "da follow", "danh ba", "thong bao he thong"}
# Phần chữ của tên và @id mặc định TikTok đặt sẵn. Giống nhau ở phần này không phải bằng chứng cùng người.
GENERIC_NAMES = frozenset({"user", "tiktok", "tiktoker"})
# Ký tự đặc biệt giữ lại trong tên. "|" bị bỏ vì bộ đọc hay thấy nó ở mép ảnh đại diện.
NAME_SYMBOLS = "._-&()'+#!/"


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
    kept = "".join(ch if ch.isalnum() or ch in NAME_SYMBOLS else " " for ch in text)
    return " ".join(kept.split())


def is_name(text: str) -> bool:
    stripped = str(text or "").strip()
    if len(stripped) == 1 and stripped.isalpha() and ord(stripped) > 127:
        return True
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
    if letters in GENERIC_NAMES or "".join(ch for ch in compact if ch.isalpha()) in GENERIC_NAMES:
        # Tên mặc định của TikTok (user + dãy số): chỉ phần chữ "user" giống nhau thì chưa nói lên gì.
        return False
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


def _handle_token(text: str) -> str:
    """Phần chữ của một @id in dưới số. Bỏ dấu và ký tự rác.

    ó thành 6 chỉ khi dòng đã có chữ số hoặc dấu @. Tên thuần như Binh không
    bị đổi thành số rồi nhận nhầm là @id.
    """
    raw = str(text or "")
    if "@" in raw or any(ch.isdigit() for ch in raw):
        raw = raw.translate(_HANDLE_LOOKALIKE)
    folded = fold(raw)
    return "".join(ch for ch in folded if ch.isalnum() or ch in "._").rstrip(".").lower()


def subtitle_handle(name: str) -> str:
    """@id đã in sẵn ở dòng dưới số, khi dòng đó không thể là tên người.

    Nhận khi cả dòng là @id (kể cả @660), dạng user kèm số, hoặc một token
    đúng bộ ký tự @id và có chữ số (camtruong1954). Chữ thuần (Kem, Hong,
    teehairsalon) vẫn là tên. Dòng nhiều từ cũng là tên (Lan 2).
    """
    text = " ".join(str(name or "").split())
    if not text or looks_like_phone(text) or " " in text:
        return ""
    if text.startswith("@"):
        body = text[1:].rstrip(".").lower()
        if _HANDLE_TOKEN.match(body) and not body.startswith("."):
            return "@" + body
        folded = _handle_token(text[1:])
        if _HANDLE_TOKEN.match(folded) and not folded.startswith("."):
            return "@" + folded
        return ""
    cleaned = _handle_token(text)
    if not _HANDLE_TOKEN.match(cleaned):
        return ""
    if not any(ch.isdigit() for ch in cleaned) or not any(ch.isalpha() for ch in cleaned):
        return ""
    return find_handle("@" + cleaned)


def find_handle(text: str) -> str:
    """@id TikTok chỉ có chữ thường, số, dấu chấm, gạch dưới, không kết thúc bằng dấu chấm."""
    match = HANDLE_RE.search("".join(str(text or "").split()))
    if not match:
        return ""
    handle = match.group(1).rstrip(".").lower()
    if len(handle) < 2 or handle.startswith("."):
        return ""
    # @id toàn chữ số có thật (@31776814785). Chuỗi số ngắn sau @ thường là giờ hoặc rác (@0749).
    if not any(ch.isalpha() for ch in handle) and len(handle) < 6:
        return ""
    return "@" + handle


# Bộ đọc tiếng Việt đọc số 6 thành ó trong @id (@thanhson190ó).
# Dòng @id trên màn hình luôn là chữ thường; khi bộ đọc trả chữ hoa I, S, B
# (cao hết cỡ chữ) thì trên ảnh đó là l, 5, 8 chứ không phải chữ thường i, s, b.
_HANDLE_LOOKALIKE = str.maketrans({"ó": "6", "ò": "6", "Ó": "6", "Ò": "6", "I": "l", "O": "0", "S": "5", "B": "8"})


def handle_from_read(text: str) -> str:
    """@id từ một lần đọc riêng dòng @id. Bộ đọc hay bỏ mất dấu @ ở đầu."""
    text = str(text or "").strip().translate(_HANDLE_LOOKALIKE)
    return find_handle(text if "@" in text else "@" + text)


def handle_from_viet(text: str) -> str:
    """VietOCR đọc dấu @ thành @ cộng thêm một chữ O, Q hoặc 0. Bỏ chữ thừa đó."""
    body = "".join(str(text or "").split()).lstrip("@")
    if len(body) > 1 and body[0] in "OQ0o":
        body = body[1:]
    return find_handle("@" + body.translate(_HANDLE_LOOKALIKE))


# Các ký tự bộ đọc hay nhầm với nhau trong @id.
_LOOKALIKE = [set("l1i"), set("o0"), set("qg9"), set("s5"), set("b6"), set("d4"), set("z2"), set("b8")]


def lookalike_handles(left: str, right: str) -> bool:
    """Hai @id chỉ khác nhau ở một chỗ, và chỗ đó là cặp ký tự hay đọc nhầm (l với i, q với g, _ với __)."""
    if left == right:
        return False
    if left.replace("__", "_") == right.replace("__", "_"):
        return True
    if len(left) != len(right):
        return False
    diffs = [(a, b) for a, b in zip(left, right) if a != b]
    return len(diffs) == 1 and any(diffs[0][0] in group and diffs[0][1] in group for group in _LOOKALIKE)


def fix_underscores(handle: str, strokes: list[tuple[float, float]]) -> str:
    """Sửa số gạch dưới của @id theo các nét ngang đo trên ảnh (xem frame._underscore_strokes).

    strokes: (vị trí bắt đầu, chiều dài) tính bằng bề rộng một ký tự, ký tự 0 là @.
    Một gạch dưới dài khoảng 0,7 đến 1 ký tự, hai gạch liền nhau khoảng 1,5.
    Chỉ sửa hai lỗi bộ đọc hay mắc: hai gạch đọc thành một, và mất gạch ngay sau @.
    """
    if not handle.startswith("@") or not strokes:
        return handle
    out = handle
    groups = [(match.start(), match.end() - match.start()) for match in re.finditer("_+", handle)]
    for start, size in reversed(groups):
        near = [length for begin, length in strokes if start - 1.0 <= begin <= start + 1.2]
        if size == 1 and sum(near) >= 1.3:
            out = out[:start] + "__" + out[start + 1 :]
    first = out[1:2]
    if first != "_" and first not in "gjpqy" and len(out) > 2:
        if any(1.0 <= begin <= 2.2 and length >= 0.75 for begin, length in strokes):
            out = "@_" + out[1:]
    return out


def vote_chars(reads: list[tuple[str, float]] | list[tuple[str, float, float]], loose: bool = True) -> str:
    """Bỏ phiếu theo từng ký tự giữa nhiều lần đọc cùng một dòng chữ, mỗi lần đọc có trọng số.

    Lấy lần đọc giống các lần khác nhất làm gốc, xếp thẳng hàng từng lần đọc
    với gốc, rồi ở mỗi vị trí chọn ký tự (hoặc việc bỏ ký tự) được nhiều trọng
    số nhất. Đoạn chữ chèn thêm chỉ được giữ khi quá nửa tổng trọng số có nó.
    loose: xếp hàng bỏ qua dấu và hoa thường, để "Hải" và "Hài" vẫn thẳng hàng
    và chỉ bỏ phiếu cho dấu.
    Phần tử thứ ba, nếu có, là trọng số dùng ở vị trí mà mọi lần đọc đều thấy
    một chữ số: bộ tiếng Việt hay đọc 3 thành 5, nên ở chỗ chọn giữa hai chữ số
    nó được tin ít hơn. Chỗ chọn giữa 1 và l vẫn dùng trọng số thường.
    """
    present = [
        (str(item[0]), float(item[1]), float(item[2]) if len(item) > 2 else float(item[1]))
        for item in reads
        if item[0] and item[1] > 0
    ]
    if not present:
        return ""
    if len(present) == 1:
        return present[0][0]

    def key(text: str) -> str:
        return fold(text).casefold() if loose else text

    keys = [key(text) for text, _, _ in present]
    total = sum(weight for _, weight, _ in present)
    len_support = Counter()
    for (text, weight, _), text_key in zip(present, keys):
        len_support[len(text_key)] += weight
    pivot_index = max(
        range(len(present)),
        key=lambda i: (
            len_support[len(keys[i])] >= total / 2,
            sum(
                weight * SequenceMatcher(None, keys[i], keys[j], autojunk=False).ratio()
                for j, (_, weight, _) in enumerate(present)
                if j != i
            ),
            present[i][1],
        ),
    )
    pivot = present[pivot_index][0]
    slots: list[Counter] = [Counter() for _ in pivot]
    digit_slots: list[Counter] = [Counter() for _ in pivot]
    gaps: list[Counter] = [Counter() for _ in range(len(pivot) + 1)]
    for (text, weight, digit_weight), text_key in zip(present, keys):
        inserted: dict[int, str] = {}
        matcher = SequenceMatcher(None, keys[pivot_index], text_key, autojunk=False)
        for op, a0, a1, b0, b1 in matcher.get_opcodes():
            if op in ("equal", "replace"):
                shared = min(a1 - a0, b1 - b0)
                for offset in range(shared):
                    char = text[b0 + offset]
                    slots[a0 + offset][char] += weight
                    digit_slots[a0 + offset][char] += digit_weight
                for index in range(a0 + shared, a1):
                    slots[index][""] += weight
                if b1 - b0 > shared:
                    inserted[a0 + shared] = inserted.get(a0 + shared, "") + text[b0 + shared : b1]
            elif op == "delete":
                for index in range(a0, a1):
                    slots[index][""] += weight
            else:
                inserted[a0] = inserted.get(a0, "") + text[b0:b1]
        for position, piece in inserted.items():
            gaps[position][piece] += weight
    out: list[str] = []
    for index in range(len(pivot) + 1):
        if gaps[index]:
            piece, support = max(gaps[index].items(), key=lambda item: item[1])
            if support > total / 2 or (index == 0 and sum(gaps[index].values()) > total / 2):
                out.append(piece)
        if index < len(pivot):
            counts = slots[index]
            if counts.get("", 0.0) > total / 2:
                continue
            present_counts = {ch: w for ch, w in counts.items() if ch}
            if present_counts and all(char.isdigit() for char in present_counts):
                present_counts = {ch: w for ch, w in digit_slots[index].items() if ch}
            elif loose and len(present_counts) > 1:
                for mark_char, grave_char in (("ả", "à"), ("Ả", "À")):
                    if present_counts.get(mark_char, 0.0) >= 1.0 and grave_char in present_counts:
                        present_counts[mark_char] += 1.6
                for hat_char, tilde_char in (("ô", "õ"), ("Ô", "Õ")):
                    if present_counts.get(hat_char, 0.0) >= 1.5 and tilde_char in present_counts:
                        present_counts[hat_char] += 0.6
                if len(present_counts) >= 4 and max(present_counts.values()) < total / 2:
                    for ch in list(present_counts):
                        if fold(ch) == ch:
                            present_counts[ch] += total
            if present_counts:
                choice, _ = max(present_counts.items(), key=lambda item: (item[1], item[0] == pivot[index]))
                out.append(choice)
    return "".join(out)


def prefer_plain_digits_and_case(voted: str, plain: str) -> str:
    """Bộ chỉ chữ không dấu đọc chữ số và hoa thường đúng hơn bộ tiếng Việt.

    Chỗ cả hai cùng là chữ số mà khác nhau thì lấy chữ số của bộ không dấu
    (Thoa 6756… thành Thoa 6736…). Chữ hoa lạc giữa từ mà bộ không dấu đọc là
    chữ thường thì hạ xuống (TrUc thành Truc). Không nâng chữ thường lên hoa,
    để tên viết liền như YếnMến giữ nguyên.
    """
    if not voted or not plain:
        return voted
    out = list(voted)
    matcher = SequenceMatcher(None, fold(voted).casefold(), plain.casefold(), autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "replace" and a1 - a0 == b1 - b0:
            for offset in range(a1 - a0):
                if voted[a0 + offset].isdigit() and plain[b0 + offset].isdigit():
                    out[a0 + offset] = plain[b0 + offset]
        elif op == "equal":
            for offset in range(a1 - a0):
                index = a0 + offset
                if (
                    index > 0
                    and out[index].isupper()
                    and out[index - 1].islower()
                    and plain[b0 + offset].islower()
                ):
                    out[index] = out[index].lower()
    return "".join(out)


def vote(reads: list[str]) -> str:
    """Giá trị được nhiều lần đọc nhất; hoà thì lấy lần đọc đứng trước (đáng tin hơn)."""
    present = [item for item in reads if item]
    if not present:
        return ""
    counts = Counter(present)
    top = max(counts.values())
    return next(item for item in present if counts[item] == top)
