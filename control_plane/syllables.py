"""Bảng âm tiết tiếng Việt để sửa cách viết sau khi các hướng đọc đã cùng chữ gốc.

Chỉ làm hai việc an toàn. Dấu thanh đang nằm sai nguyên âm thì chuyển về đúng chỗ, khi chỗ đó là một âm tiết có thật.
Một từ không phải âm tiết, và đúng một âm tiết hợp lệ cùng chữ gốc, thì dùng âm tiết đó.
Nhiều âm tiết cùng chữ gốc (khác nhau ở dấu) thì giữ nguyên chữ OCR đã chọn, không đoán dấu.
"""

from __future__ import annotations

import unicodedata

from control_plane.people import clean_name, fold_name

# Thanh: ngang, huyền, sắc, hỏi, ngã, nặng. Ký tự đầu là nguyên âm không thanh.
_FORMS = {
    "a": "aàáảãạ",
    "ă": "ăằắẳẵặ",
    "â": "âầấẩẫậ",
    "e": "eèéẻẽẹ",
    "ê": "êềếểễệ",
    "i": "iìíỉĩị",
    "o": "oòóỏõọ",
    "ô": "ôồốổỗộ",
    "ơ": "ơờớởỡợ",
    "u": "uùúủũụ",
    "ư": "ưừứửữự",
    "y": "yỳýỷỹỵ",
}
_SPLIT: dict[str, tuple[str, int]] = {}
for _base, _forms in _FORMS.items():
    for _tone, _char in enumerate(_forms):
        _SPLIT[_char] = (_base, _tone)

# Vần có thật. Phụ âm đầu ghép theo quy tắc c/k, g/gh, ng/ngh.
_ONSETS = (
    "", "b", "c", "ch", "d", "đ", "g", "gh", "gi", "h", "k", "kh", "l", "m", "n",
    "ng", "ngh", "nh", "p", "ph", "qu", "r", "s", "t", "th", "tr", "v", "x",
)
_RIMES = (
    "a", "ac", "ach", "ai", "am", "an", "ang", "anh", "ao", "ap", "at", "ay",
    "ăc", "ăm", "ăn", "ăng", "ăp", "ăt",
    "âc", "âm", "ân", "âng", "âp", "ât",
    "e", "ec", "em", "en", "eng", "eo", "ep", "et",
    "ê", "êch", "êm", "ên", "ênh", "êp", "êt", "êu",
    "i", "ia", "ich", "iêm", "iên", "iêp", "iêt", "iêu", "im", "in", "inh", "ip", "it", "iu",
    "o", "oa", "oac", "oach", "oai", "oam", "oan", "oang", "oanh", "oap", "oat", "oay",
    "oc", "oe", "oeo", "oi", "om", "on", "ong", "op", "ot",
    "ô", "ôc", "ôi", "ôm", "ôn", "ông", "ôp", "ôt",
    "ơ", "ơi", "ơm", "ơn", "ơp", "ơt",
    "u", "ua", "uân", "uây", "uc", "uê", "uêch", "uêm", "uên", "uênh", "ui", "um", "un", "ung",
    "uô", "uôc", "uôi", "uôm", "uôn", "uông", "uôt", "up", "ut",
    "uy", "uya", "uyên", "uyêt", "uych", "uyn", "uynh", "uyt", "ưu",
    "ư", "ưa", "ưc", "ưi", "ưng", "ươc", "ươi", "ươm", "ươn", "ương", "ươp", "ươt", "ưt",
    "y", "yêm", "yên", "yêt", "ynh",
)
# Mũ và sừng được ưu tiên đặt dấu: ê ơ ô ă â ư.
_HAT = ("ê", "ơ", "ô", "ă", "â", "ư")


def _onset_ok(onset: str, rime: str) -> bool:
    first = rime[0]
    front = first in "eêi"
    yfront = first in "eêiy"
    if onset == "c":
        return not yfront
    if onset == "k":
        return yfront
    if onset == "g":
        return not front
    if onset == "gh":
        return front
    if onset == "ng":
        return not front
    if onset == "ngh":
        return front
    if onset == "qu":
        return first not in "uư"
    return True


def _is_vowel(char: str) -> bool:
    return char in _SPLIT


def _vowel_indexes(bare: str) -> list[int]:
    """Vị trí nguyên âm. u trong qu và i trong gi không phải nguyên âm khi phía sau còn nguyên âm."""
    skip: set[int] = set()
    if bare.startswith("qu") and len(bare) > 2 and _is_vowel(bare[2]):
        skip.add(1)
    if bare.startswith("gi") and len(bare) > 2 and _is_vowel(bare[2]):
        skip.add(1)
    return [index for index, char in enumerate(bare) if index not in skip and _is_vowel(char)]


def _nucleus(bare: str) -> int | None:
    """Chỗ đặt dấu thanh. None khi không có nguyên âm.

    Nguyên âm có mũ hoặc sừng nhận dấu. Vần uy mở thì dấu trên u (thủy).
    Vần uy còn phụ âm phía sau thì dấu trên y (Huỳnh). Vần khép thì dấu trên nguyên âm cuối.
    Ba nguyên âm không mũ thì dấu ở giữa. Vần mở còn lại thì dấu trên nguyên âm đầu.
    """
    indexes = _vowel_indexes(bare)
    if not indexes:
        return None
    if len(indexes) == 1:
        return indexes[0]
    for hat in _HAT:
        for index in indexes:
            if bare[index] == hat:
                return index
    pair = "".join(bare[index] for index in indexes)
    if pair == "uy" and indexes[-1] == len(bare) - 1:
        return indexes[0]
    if indexes[-1] < len(bare) - 1:
        return indexes[-1]
    if len(indexes) >= 3:
        return indexes[1]
    return indexes[0]


def _with_tone(bare: str, tone: int) -> str:
    if tone <= 0:
        return bare
    place = _nucleus(bare)
    if place is None:
        return bare
    chars = list(bare)
    forms = _FORMS.get(chars[place])
    if forms is None:
        return bare
    chars[place] = forms[tone]
    return "".join(chars)


def _build_valid() -> frozenset[str]:
    found: set[str] = set()
    for onset in _ONSETS:
        for rime in _RIMES:
            if not _onset_ok(onset, rime):
                continue
            bare = onset + rime
            for tone in range(6):
                found.add(_with_tone(bare, tone))
    return frozenset(found)


_VALID = _build_valid()
_BY_FOLD: dict[str, set[str]] | None = None


def valid_syllable(word: str) -> bool:
    """Âm tiết đã đặt dấu đúng chỗ."""
    return unicodedata.normalize("NFC", word).casefold() in _VALID


def _fold_index() -> dict[str, set[str]]:
    global _BY_FOLD
    if _BY_FOLD is None:
        found: dict[str, set[str]] = {}
        for word in _VALID:
            found.setdefault(fold_name(word), set()).add(word)
        _BY_FOLD = found
    return _BY_FOLD


def _bare_and_tones(word: str) -> tuple[str, list[tuple[int, int]]]:
    """Chuỗi không thanh, và danh sách (vị trí, mã thanh) của các dấu thanh. Mũ không phải thanh."""
    tones: list[tuple[int, int]] = []
    chars: list[str] = []
    for index, char in enumerate(word):
        base, tone = _SPLIT.get(char, (char, 0))
        chars.append(base)
        if tone:
            tones.append((index, tone))
    return "".join(chars), tones


def _match_case(fixed: str, original: str) -> str:
    if original[:1].isupper() and fixed:
        return fixed[:1].upper() + fixed[1:]
    return fixed


def _move_tone(word: str) -> str:
    """Chuyển một dấu thanh về đúng nguyên âm. Giữ nguyên khi chỗ mới không phải âm tiết."""
    bare, tones = _bare_and_tones(word)
    if len(tones) != 1:
        return word
    place = _nucleus(bare)
    index, tone = tones[0]
    if place is None or place == index:
        return word
    moved = _with_tone(bare, tone)
    if moved not in _VALID:
        return word
    return _match_case(moved, word)


def _unique(word: str) -> str:
    """Đúng một âm tiết hợp lệ cùng chữ gốc thì dùng âm đó. Nhiều âm thì giữ chữ đang có."""
    if valid_syllable(word):
        return word
    choices = _fold_index().get(fold_name(word), set())
    if len(choices) != 1:
        return word
    only = next(iter(choices))
    return _match_case(only, word)


def restore_word(word: str, *, allow_unique: bool) -> str:
    """Sửa một từ. allow_unique bật khi cả hai hướng đều không có dấu."""
    moved = _move_tone(unicodedata.normalize("NFC", word).casefold())
    restored = _match_case(moved, word) if moved != word.casefold() else word
    if not allow_unique:
        return restored
    unique = _unique(restored.casefold())
    if unique.casefold() == restored.casefold():
        return restored
    return _match_case(unique, restored)


def restore_name(name: str, *, allow_unique: bool) -> str:
    """Sửa từng từ trong tên. Từ không phải âm tiết thì giữ, trừ khi đúng một cách viết."""
    words = clean_name(name).split()
    if not words:
        return ""
    return " ".join(restore_word(word, allow_unique=allow_unique) for word in words)
