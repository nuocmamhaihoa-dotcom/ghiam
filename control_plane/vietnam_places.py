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
_PROVINCE_PHRASES = tuple(sorted((name for name in PROVINCE_NAMES if " " in name or len(name) >= 6), key=len, reverse=True))
_HAMLET_PREFIX = re.compile(r"^(?:thon|xom|ap|lang|buon|soc|khom|khu pho|to dan pho|kp|to \d|doi \d|ban \d)\b")
_COMMUNE_PREFIX = re.compile(r"^(?:xa|phuong|thi tran|p|x)\.?\s+\S")
_DISTRICT_PREFIX = re.compile(r"^(?:quan|huyen|thi xa|q|h)\.?\s+\S")
_CITY_PREFIX = re.compile(r"^(?:thanh pho|tp)\.?\s+(.+)$")
_PROVINCE_PREFIX = re.compile(r"^tinh\s+\S")


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
    if any(f" {word} " in padded for word in ("to dan pho", "khu pho", "thon", "xom", "ap", "lang", "buon", "soc", "khom")):
        return 1
    if any(f" {word} " in padded for word in ("so nha", "ten duong", "duong", "ngo", "hem", "ngach", "kiet", "pho")):
        return 0
    return None


def address_content_rank(value: str) -> int:
    """0 chi tiết, 1 thôn/xóm, 2 xã/phường/thị trấn, 3 quận/huyện/thị xã, 4 tỉnh/thành phố trung ương."""
    folded = fold_vietnamese(value)
    if not folded:
        return 0
    if _PROVINCE_PREFIX.match(folded):
        return 4
    city = _CITY_PREFIX.match(folded)
    if city:
        rest = city.group(1).strip()
        if rest in PROVINCE_NAMES:
            return 4
        return 3
    if _DISTRICT_PREFIX.match(folded):
        return 3
    if _COMMUNE_PREFIX.match(folded):
        return 2
    if _HAMLET_PREFIX.match(folded):
        return 1
    if folded in PROVINCE_NAMES:
        return 4
    if folded in DISTRICT_NAMES:
        return 3
    return 0


def address_levels_present(value: str) -> set[int]:
    """Bậc đã có trong một ô đầy đủ, để khỏi nối lại thôn/xã/tỉnh đã viết trong ô đó."""
    folded = fold_vietnamese(value)
    if not folded:
        return set()
    padded = f" {folded} "
    levels: set[int] = set()
    if any(character.isdigit() for character in folded) or any(
        f" {word} " in padded for word in ("duong", "ngo", "hem", "ngach", "kiet", "so nha")
    ):
        levels.add(0)
    elif " pho " in padded and " thanh pho " not in padded:
        levels.add(0)
    if _HAMLET_PREFIX.search(folded) or any(f" {word} " in padded for word in ("thon", "xom", "khu pho", "to dan pho")):
        levels.add(1)
    if any(f" {word} " in padded for word in ("xa", "phuong", "thi tran")):
        levels.add(2)
    if any(f" {word} " in padded for word in ("quan", "huyen", "thi xa")) or _CITY_PREFIX.search(folded):
        levels.add(3)
    if " tinh " in padded or any(f" {name} " in padded for name in _PROVINCE_PHRASES):
        levels.add(4)
        if 3 in levels and _CITY_PREFIX.search(folded):
            rest = _CITY_PREFIX.search(folded)
            if rest and rest.group(1).strip() in PROVINCE_NAMES:
                levels.discard(3)
    return levels


def is_complete_address(value: str) -> bool:
    levels = address_levels_present(value)
    return 0 in levels and 4 in levels
