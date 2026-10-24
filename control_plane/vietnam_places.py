"""Địa danh Việt Nam (có dấu và không dấu) để nhận cột địa chỉ."""

from __future__ import annotations

import re
import unicodedata

_MARKS = {
    "à": "a",
    "á": "a",
    "ả": "a",
    "ã": "a",
    "ạ": "a",
    "ă": "a",
    "ằ": "a",
    "ắ": "a",
    "ẳ": "a",
    "ẵ": "a",
    "ặ": "a",
    "â": "a",
    "ầ": "a",
    "ấ": "a",
    "ẩ": "a",
    "ẫ": "a",
    "ậ": "a",
    "è": "e",
    "é": "e",
    "ẻ": "e",
    "ẽ": "e",
    "ẹ": "e",
    "ê": "e",
    "ề": "e",
    "ế": "e",
    "ể": "e",
    "ễ": "e",
    "ệ": "e",
    "ì": "i",
    "í": "i",
    "ỉ": "i",
    "ĩ": "i",
    "ị": "i",
    "ò": "o",
    "ó": "o",
    "ỏ": "o",
    "õ": "o",
    "ọ": "o",
    "ô": "o",
    "ồ": "o",
    "ố": "o",
    "ổ": "o",
    "ỗ": "o",
    "ộ": "o",
    "ơ": "o",
    "ờ": "o",
    "ớ": "o",
    "ở": "o",
    "ỡ": "o",
    "ợ": "o",
    "ù": "u",
    "ú": "u",
    "ủ": "u",
    "ũ": "u",
    "ụ": "u",
    "ư": "u",
    "ừ": "u",
    "ứ": "u",
    "ử": "u",
    "ữ": "u",
    "ự": "u",
    "ỳ": "y",
    "ý": "y",
    "ỷ": "y",
    "ỹ": "y",
    "ỵ": "y",
    "đ": "d",
}

_FOLD = str.maketrans({**_MARKS, **{key.upper(): value.upper() for key, value in _MARKS.items()}})
_NON_WORD = re.compile(r"[^a-z0-9]+")
_UNIT_CODE = re.compile(r"(?:^| )(?:q|p|h|x|tt|tp)[.]?(?: |\d)")
_ADMIN_PREFIX = re.compile(
    r"(?:^| )(?:tinh|thanh pho|tp|quan|huyen|xa|phuong|thi xa|thi tran|duong|pho) [a-z0-9]"
)

# Từ đơn ngắn (An, Mỹ, Sơn...) không đưa vào đây — dễ trùng tên người.
_PROVINCES = (
    "An Giang",
    "Bà Rịa Vũng Tàu",
    "Bà Rịa - Vũng Tàu",
    "Vũng Tàu",
    "Bắc Giang",
    "Bắc Kạn",
    "Bạc Liêu",
    "Bắc Ninh",
    "Bến Tre",
    "Bình Định",
    "Bình Dương",
    "Bình Phước",
    "Bình Thuận",
    "Cà Mau",
    "Cần Thơ",
    "Cao Bằng",
    "Đà Nẵng",
    "Đắk Lắk",
    "Đắk Nông",
    "Điện Biên",
    "Đồng Nai",
    "Đồng Tháp",
    "Gia Lai",
    "Hà Giang",
    "Hà Nam",
    "Hà Nội",
    "Hà Tĩnh",
    "Hải Dương",
    "Hải Phòng",
    "Hậu Giang",
    "Hòa Bình",
    "Hưng Yên",
    "Khánh Hòa",
    "Kiên Giang",
    "Kon Tum",
    "Lai Châu",
    "Lâm Đồng",
    "Lạng Sơn",
    "Lào Cai",
    "Long An",
    "Nam Định",
    "Nghệ An",
    "Ninh Bình",
    "Ninh Thuận",
    "Phú Thọ",
    "Phú Yên",
    "Quảng Bình",
    "Quảng Nam",
    "Quảng Ngãi",
    "Quảng Ninh",
    "Quảng Trị",
    "Sóc Trăng",
    "Sơn La",
    "Tây Ninh",
    "Thái Bình",
    "Thái Nguyên",
    "Thanh Hóa",
    "Thừa Thiên Huế",
    "Thừa Thiên - Huế",
    "Tiền Giang",
    "Hồ Chí Minh",
    "Thành phố Hồ Chí Minh",
    "TP Hồ Chí Minh",
    "Trà Vinh",
    "Tuyên Quang",
    "Vĩnh Long",
    "Vĩnh Phúc",
    "Yên Bái",
    "Huế",
)

_ALIASES = (
    "ha noi",
    "hn",
    "sai gon",
    "saigon",
    "tphcm",
    "tp hcm",
    "hcm",
    "tp.hcm",
    "ho chi minh city",
    "brvt",
    "ba ria vung tau",
    "da nang",
    "dnang",
    "hai phong",
    "can tho",
    "da lat",
    "dalat",
    "nha trang",
    "nhatrang",
    "vung tau",
    "bien hoa",
    "thu duc",
    "ha long",
    "ha long bay",
    "phan thiet",
    "quy nhon",
    "buon ma thuot",
    "pleiku",
    "my tho",
    "rach gia",
    "ca mau",
    "viet nam",
    "vietnam",
)

_DISTRICTS = (
    "Ba Đình",
    "Hoàn Kiếm",
    "Hai Bà Trưng",
    "Đống Đa",
    "Tây Hồ",
    "Cầu Giấy",
    "Thanh Xuân",
    "Hoàng Mai",
    "Long Biên",
    "Hà Đông",
    "Nam Từ Liêm",
    "Bắc Từ Liêm",
    "Thanh Trì",
    "Gia Lâm",
    "Đông Anh",
    "Sóc Sơn",
    "Hoài Đức",
    "Thanh Oai",
    "Thường Tín",
    "Phúc Thọ",
    "Sơn Tây",
    "Quận 1",
    "Quận 2",
    "Quận 3",
    "Quận 4",
    "Quận 5",
    "Quận 6",
    "Quận 7",
    "Quận 8",
    "Quận 9",
    "Quận 10",
    "Quận 11",
    "Quận 12",
    "Bình Thạnh",
    "Tân Bình",
    "Tân Phú",
    "Phú Nhuận",
    "Gò Vấp",
    "Thủ Đức",
    "Bình Tân",
    "Nhà Bè",
    "Hóc Môn",
    "Củ Chi",
    "Bình Chánh",
    "Cần Giờ",
    "Hải Châu",
    "Thanh Khê",
    "Sơn Trà",
    "Ngũ Hành Sơn",
    "Liên Chiểu",
    "Cẩm Lệ",
    "Hòa Vang",
    "Ngô Quyền",
    "Lê Chân",
    "Kiến An",
    "Hồng Bàng",
    "Đồ Sơn",
    "Dương Kinh",
    "Ninh Kiều",
    "Bình Thủy",
    "Cái Răng",
    "Ô Môn",
    "Thốt Nốt",
    "Phú Nhuận",
    "Tân An",
    "Thủ Dầu Một",
    "Dĩ An",
    "Thuận An",
    "Biên Hòa",
    "Long Khánh",
    "Nha Trang",
    "Cam Ranh",
    "Phan Thiết",
    "Phan Rang",
    "Quy Nhơn",
    "Pleiku",
    "Buôn Ma Thuột",
    "Đà Lạt",
    "Bảo Lộc",
    "Hạ Long",
    "Cẩm Phả",
    "Uông Bí",
    "Móng Cái",
    "Vinh",
    "Hà Tĩnh",
    "Đồng Hới",
    "Tam Kỳ",
    "Hội An",
    "Quảng Ngãi",
    "Tuy Hòa",
    "Rạch Giá",
    "Hà Tiên",
    "Mỹ Tho",
    "Gò Công",
    "Bến Tre",
    "Trà Vinh",
    "Vĩnh Long",
    "Sa Đéc",
    "Cao Lãnh",
    "Long Xuyên",
    "Châu Đốc",
    "Sóc Trăng",
    "Bạc Liêu",
    "Cà Mau",
    "Vị Thanh",
    "Tây Hồ",
)

_KEYWORDS = (
    "thanh pho",
    "thi xa",
    "thi tran",
    "khu pho",
    "to dan pho",
    "khu dan cu",
    "khu do thi",
    "chung cu",
    "so nha",
    "ngach",
    "hem",
    "thon",
    "xom",
)


def fold_vietnamese(value: str) -> str:
    text = unicodedata.normalize("NFC", value or "").translate(_FOLD)
    return _NON_WORD.sub(" ", text.casefold()).strip()


def _phrase_set(names: tuple[str, ...]) -> frozenset[str]:
    folded = {fold_vietnamese(name) for name in names}
    return frozenset(item for item in folded if len(item) >= 3)


PLACE_PHRASES = frozenset(sorted(_phrase_set(_PROVINCES) | _phrase_set(_DISTRICTS) | {fold_vietnamese(item) for item in _ALIASES if fold_vietnamese(item)}))
_SORTED_PLACES = tuple(sorted(PLACE_PHRASES, key=len, reverse=True))
ADDRESS_KEYWORDS = frozenset(_KEYWORDS)


def has_vietnam_place(value: str) -> bool:
    folded = fold_vietnamese(value)
    if not folded:
        return False
    padded = f" {folded} "
    if _UNIT_CODE.search(padded):
        return True
    has_digit = any(character.isdigit() for character in folded)
    keyword = has_address_keyword(value)
    for place in _SORTED_PLACES:
        if f" {place} " not in padded:
            continue
        if " " in place or len(place) >= 5:
            return True
        if folded == place or has_digit or keyword:
            return True
    return False


def has_address_keyword(value: str) -> bool:
    folded = fold_vietnamese(value)
    if not folded:
        return False
    padded = f" {folded} "
    if _ADMIN_PREFIX.search(padded):
        return True
    return any(f" {key} " in padded for key in ADDRESS_KEYWORDS)


_PROVINCE_ALIASES = frozenset(
    {
        "ha noi",
        "hn",
        "ho chi minh",
        "hcm",
        "tphcm",
        "tp hcm",
        "sai gon",
        "saigon",
        "da nang",
        "hai phong",
        "can tho",
        "hue",
        "thanh pho ha noi",
        "thanh pho hue",
        "thanh pho ho chi minh",
        "tp ho chi minh",
    }
)
PROVINCE_NAMES = frozenset(fold_vietnamese(name) for name in _PROVINCES if fold_vietnamese(name)) | _PROVINCE_ALIASES
DISTRICT_NAMES = frozenset(
    name for name in (fold_vietnamese(item) for item in _DISTRICTS) if name and name not in PROVINCE_NAMES
)
# Mã viết tắt, không phải tên đầy đủ. «Huế», «Phú Thọ» không nằm ở đây vì hay là tên đường.
_CODE_PROVINCE = {
    "hn": "ha noi",
    "hcm": "ho chi minh",
    "tphcm": "ho chi minh",
    "brvt": "ba ria vung tau",
}
_PROVINCE_CANON = {
    "ha noi": "ha noi",
    "hn": "ha noi",
    "tp ha noi": "ha noi",
    "thanh pho ha noi": "ha noi",
    "ho chi minh": "ho chi minh",
    "hcm": "ho chi minh",
    "tphcm": "ho chi minh",
    "tp hcm": "ho chi minh",
    "thanh pho ho chi minh": "ho chi minh",
    "tp ho chi minh": "ho chi minh",
    "sai gon": "ho chi minh",
    "saigon": "ho chi minh",
    "hue": "hue",
    "thua thien hue": "hue",
    "thanh pho hue": "hue",
    "tp hue": "hue",
    "brvt": "ba ria vung tau",
    "ba ria vung tau": "ba ria vung tau",
    "vung tau": "ba ria vung tau",
}
_COUNTRY_NAMES = frozenset({"viet nam", "vietnam", "vn"})
_GLUE_TOKENS = frozenset({"duong", "pho", "ngo", "hem", "ngach", "kiet", "so", "lo", "toa", "tang", "can", "chung", "cu"})
# (cụm từ, loại, có nuốt số đứng ngay sau không). Cụm dài đứng trước.
_ADMIN_TOKEN_PREFIXES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("to", "dan", "pho"), "to", True),
    (("khu", "dan", "cu"), "kdc", False),
    (("khu", "pho"), "kp", True),
    (("thanh", "pho"), "tp", False),
    (("thi", "tran"), "tt", False),
    (("thi", "xa"), "tx", False),
    (("phuong",), "p", True),
    (("huyen",), "h", True),
    (("quan",), "q", True),
    (("thon",), "thon", False),
    (("xom",), "xom", False),
    (("buon",), "buon", False),
    (("soc",), "soc", False),
    (("khom",), "khom", False),
    (("tinh",), "tinh", False),
    (("tdp",), "to", True),
    (("kp",), "kp", True),
    (("tx",), "tx", False),
    (("tt",), "tt", False),
    (("tp",), "tp", False),
    (("q",), "q", True),
    (("p",), "p", True),
    (("x",), "x", True),
)
_PREFIX_STRIP = re.compile(
    r"^(?:to dan pho|khu dan cu|khu pho|thanh pho|thi tran|thi xa|phuong|huyen|quan|"
    r"thon|xom|buon|soc|khom|tinh|tdp|kp|tx|tt|tp|to|doi|ban|[pqhx])\s+"
)
_LEAD_LABEL = ("dia chi ", "d c ", "dc ")


def _fold_char(ch: str) -> str:
    mapped = unicodedata.normalize("NFC", ch).translate(_FOLD).casefold()
    if len(mapped) == 1 and mapped.isalnum():
        return mapped
    return " "


def _aligned(value: str) -> tuple[str, str]:
    text = " ".join(unicodedata.normalize("NFC", value).split())
    return text, "".join(_fold_char(ch) for ch in text)


def _is_province_name(folded: str) -> bool:
    return folded in PROVINCE_NAMES or folded in _PROVINCE_CANON or folded in _CODE_PROVINCE


def _province_canon(folded: str) -> str:
    if folded in _PROVINCE_CANON:
        return _PROVINCE_CANON[folded]
    if folded in _CODE_PROVINCE:
        return _CODE_PROVINCE[folded]
    rest = _PREFIX_STRIP.sub("", folded, count=1).strip()
    if rest in _PROVINCE_CANON:
        return _PROVINCE_CANON[rest]
    if rest in _CODE_PROVINCE:
        return _CODE_PROVINCE[rest]
    if rest in PROVINCE_NAMES:
        return rest
    if folded in PROVINCE_NAMES:
        return folded
    return folded


def _prefix_kind(folded: str) -> str:
    if folded.startswith("to dan pho") or folded.startswith("tdp") or re.match(r"^to\s+\d", folded):
        return "to"
    if folded.startswith("khu dan cu"):
        return "kdc"
    if folded.startswith("khu pho") or folded.startswith("kp ") or (folded.startswith("kp") and len(folded) <= 3):
        return "kp"
    if folded.startswith("thanh pho ") or folded.startswith("tp ") or folded in _CODE_PROVINCE:
        return "tp"
    if folded.startswith("thi xa") or folded.startswith("tx "):
        return "tx"
    if folded.startswith("huyen ") or re.match(r"^h\s+[a-z]", folded):
        return "h"
    if folded.startswith("quan ") or re.match(r"^q(?:\s|\d)", folded):
        return "q"
    if folded.startswith("thi tran") or folded.startswith("tt "):
        return "tt"
    if folded.startswith("phuong ") or re.match(r"^p(?:\s|\d)", folded):
        return "p"
    if folded.startswith("xa ") or re.match(r"^x\s+\S", folded):
        return "x"
    if folded.startswith("thon "):
        return "thon"
    if folded.startswith("xom "):
        return "xom"
    if folded.startswith("ap "):
        return "ap"
    if folded.startswith("lang "):
        return "lang"
    if folded.startswith("buon "):
        return "buon"
    if folded.startswith("soc "):
        return "soc"
    if folded.startswith("khom "):
        return "khom"
    if folded.startswith("doi ") or folded.startswith("ban "):
        return "to"
    if folded.startswith("tinh "):
        return "tinh"
    return ""


def _kind_from_header(header: str) -> str:
    folded = fold_vietnamese(header)
    if not folded:
        return ""
    if "to dan pho" in folded or folded in {"tdp", "to"}:
        return "to"
    if "khu pho" in folded or folded == "kp":
        return "kp"
    if "thi xa" in folded:
        return "tx"
    if "huyen" in folded:
        return "h"
    if "quan" in folded:
        return "q"
    if "thi tran" in folded:
        return "tt"
    if "phuong" in folded:
        return "p"
    if re.search(r"(?:^| )xa(?: |$)", folded):
        return "x"
    if "thon" in folded:
        return "thon"
    if "xom" in folded:
        return "xom"
    if re.search(r"(?:^| )ap(?: |$)", folded):
        return "ap"
    if "lang" in folded:
        return "lang"
    if "tinh" in folded:
        return "tinh"
    if "thanh pho" in folded:
        return "tp"
    return ""


def address_header_rank(header: str) -> int | None:
    """Tiêu đề Xã / Huyện / Tỉnh thì khóa bậc. «Địa chỉ» chung thì để nội dung ô quyết định."""
    folded = fold_vietnamese(header)
    if not folded:
        return None
    padded = f" {folded} "
    if " tinh " in padded:
        return 4
    if any(f" {word} " in padded for word in ("thi xa", "huyen", "quan")):
        return 3
    if " thanh pho " in padded or folded in {"tp", "thanh pho"}:
        return None
    if any(f" {word} " in padded for word in ("thi tran", "phuong", "xa")):
        return 2
    if any(f" {word} " in padded for word in ("to dan pho", "khu pho", "thon", "xom", "ap", "lang", "buon", "soc", "khom", "ban", "doi")):
        return 1
    if any(f" {word} " in padded for word in ("so nha", "ten duong", "duong", "ngo", "hem", "ngach", "kiet", "pho")):
        return 0
    return None


def address_content_rank(value: str) -> int:
    """0 chi tiết, 1 thôn/xóm, 2 xã/phường/thị trấn, 3 quận/huyện/thị xã, 4 tỉnh/thành phố trung ương."""
    folded = fold_vietnamese(value)
    if not folded:
        return 0
    if _is_province_name(folded) or folded.startswith("tinh ") or folded.startswith("tinh\t"):
        return 4
    if folded.startswith("thanh pho ") or folded.startswith("tp "):
        rest = re.sub(r"^(?:thanh pho|tp)\s+", "", folded, count=1).strip()
        if _is_province_name(rest):
            return 4
        return 3
    if folded in DISTRICT_NAMES:
        return 3
    if re.fullmatch(r"q\s*\d{1,2}", folded):
        return 3
    if re.match(r"^(?:thi xa|tx|huyen|quan)\s+\S", folded) or re.match(r"^q\s+\S", folded):
        return 3
    if re.match(r"^h\s+[a-z]{2}", folded):
        return 3
    if re.fullmatch(r"p\s*\d{1,2}", folded) or re.fullmatch(r"(?:kp|tdp)\s*\d{1,2}", folded):
        return 2 if folded.startswith("p") else 1
    if re.match(r"^(?:thi tran|tt|phuong|xa)\s+\S", folded) or re.match(r"^[px]\s+\S", folded):
        return 2
    if re.match(r"^(?:to|doi|ban)\s+\d{1,2}$", folded):
        return 1
    if re.match(
        r"^(?:to dan pho|tdp|khu pho|kp|khu dan cu|thon|xom|ap|lang|buon|soc|khom|ban|doi)\s+\S",
        folded,
    ):
        return 1
    return 0


def address_piece_key(text: str, rank: int, header: str = "") -> str | None:
    """Khóa để gom TP.HCM với Hồ Chí Minh, Q.1 với Quận 1. Việt Nam thì bỏ."""
    folded = fold_vietnamese(text)
    if not folded or folded in _COUNTRY_NAMES:
        return None
    if rank == 4:
        return "4:" + _province_canon(folded)
    kind = _prefix_kind(folded) or _kind_from_header(header)
    rest = _PREFIX_STRIP.sub("", folded, count=1).strip()
    short = re.sub(r"^(?:kp|tdp|[qp])(?=\d)", "", folded, count=1).strip()
    if short != folded and (not rest or rest == folded):
        rest = short
    body = rest or folded
    if re.fullmatch(r"\d{1,2}", body) and kind:
        return f"{rank}:{kind}#{int(body)}"
    if rank in (1, 2) and kind:
        return f"{rank}:{kind}:{body}"
    if body in _COUNTRY_NAMES:
        return None
    return f"{rank}:{body}"


def _token_spans(folded: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    index = 0
    size = len(folded)
    while index < size:
        if folded[index] == " ":
            index += 1
            continue
        end = index + 1
        while end < size and folded[end] != " ":
            end += 1
        spans.append((index, end, folded[index:end]))
        index = end
    return spans


def _match_admin_prefix(tokens: list[str], index: int, *, codes: bool = True) -> tuple[int, str, bool] | None:
    token = tokens[index]
    if re.fullmatch(r"q\d{1,2}", token):
        return 1, "q", True
    if re.fullmatch(r"p\d{1,2}", token):
        return 1, "p", True
    if re.fullmatch(r"(?:kp|tdp)\d{1,2}", token):
        return 1, "kp" if token.startswith("kp") else "to", True
    if codes and token in _CODE_PROVINCE:
        return 1, "tp", False
    for words, kind, takes_number in _ADMIN_TOKEN_PREFIXES:
        width = len(words)
        if tokens[index : index + width] != list(words):
            continue
        if words == ("h",):
            nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
            if not re.fullmatch(r"[a-z]{2,}", nxt):
                continue
        if words in {("to",), ("doi",), ("ban",)}:
            nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
            if not re.fullmatch(r"\d{1,2}", nxt):
                continue
        if words == ("p",) or words == ("x",) or words == ("q",):
            nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
            if not nxt:
                continue
        return width, kind, takes_number
    return None


def _admin_segment_end(tokens: list[str], start: int, width: int, takes_number: bool) -> int:
    cursor = start + width
    if takes_number and cursor < len(tokens) and re.fullmatch(r"\d{1,2}", tokens[cursor]):
        return cursor + 1
    if cursor < len(tokens) and re.match(r"^\d", tokens[cursor]):
        return cursor
    names = 0
    while cursor < len(tokens) and names < 4:
        if _match_admin_prefix(tokens, cursor, codes=False):
            break
        if names > 0 and re.match(r"^\d", tokens[cursor]):
            break
        if names > 0 and tokens[cursor] in _GLUE_TOKENS:
            break
        cursor += 1
        names += 1
    return cursor


def _split_admin_pieces(text: str) -> list[str]:
    folded_name = fold_vietnamese(text)
    if not folded_name:
        return []
    if folded_name in PROVINCE_NAMES or folded_name in DISTRICT_NAMES or folded_name in _PROVINCE_CANON:
        return [text]
    display, folded = _aligned(text)
    spans = _token_spans(folded)
    if not spans:
        return [text]
    tokens = [item[2] for item in spans]
    pieces: list[str] = []
    index = 0
    while index < len(tokens):
        matched = _match_admin_prefix(tokens, index)
        glued_code = index > 0 and tokens[index] in _CODE_PROVINCE and tokens[index - 1] in _GLUE_TOKENS
        if matched and not glued_code:
            width, _kind, takes_number = matched
            end = _admin_segment_end(tokens, index, width, takes_number)
        else:
            end = index + 1
            while end < len(tokens) and not _match_admin_prefix(tokens, end):
                end += 1
            if end < len(tokens) and tokens[end] in _CODE_PROVINCE and tokens[end - 1] in _GLUE_TOKENS:
                end += 1
                while end < len(tokens) and not _match_admin_prefix(tokens, end):
                    end += 1
        chunk = display[spans[index][0] : spans[end - 1][1]].strip(" ,;-")
        if chunk:
            pieces.append(chunk)
        index = max(end, index + 1)
    return pieces or [text]


def _strip_address_label(text: str) -> str:
    display, folded = _aligned(text)
    for lead in _LEAD_LABEL:
        if folded.startswith(lead):
            cut = display[len(lead) :].strip(" :,-")
            if cut:
                return cut
    return display or text


def iter_address_pieces(value: str) -> list[str]:
    """Tách một ô thành từng bậc. Tên đường giữ nguyên, kể cả khi trong tên có chữ tỉnh."""
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", ", ")
    raw = " ".join(raw.split())
    if not raw:
        return []
    whole = fold_vietnamese(raw)
    if whole in PROVINCE_NAMES or whole in DISTRICT_NAMES or whole in _PROVINCE_CANON:
        return [raw]
    protected = raw
    held = (
        ("Bà Rịa - Vũng Tàu", "\ue000"),
        ("Bà Rịa – Vũng Tàu", "\ue000"),
        ("Thừa Thiên - Huế", "\ue001"),
        ("Thừa Thiên – Huế", "\ue001"),
    )
    restore = {"\ue000": "Bà Rịa - Vũng Tàu", "\ue001": "Thừa Thiên - Huế"}
    for name, token in held:
        protected = protected.replace(name, token)
    normalized = re.sub(r"\s+[-–—]\s+", ", ", protected)
    rough = []
    for part in re.split(r"\s*[,;|]\s*", normalized):
        part = part.strip(" ,;")
        if not part:
            continue
        for token, name in restore.items():
            part = part.replace(token, name)
        rough.append(part)
    pieces: list[str] = []
    for part in rough:
        pieces.extend(_split_admin_pieces(_strip_address_label(part)))
    return pieces


def address_levels_present(value: str) -> set[int]:
    """Bậc có mặt sau khi tách ô. Tên tỉnh nằm trong tên đường không tính là đã có tỉnh."""
    levels: set[int] = set()
    for piece in iter_address_pieces(value):
        levels.add(address_content_rank(piece))
    return levels


def is_complete_address(value: str) -> bool:
    levels = address_levels_present(value)
    return 0 in levels and 4 in levels
