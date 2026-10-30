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
