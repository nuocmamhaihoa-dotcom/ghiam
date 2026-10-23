"""Khớp cột Excel theo tên cột kho. Cột không giống thì bỏ qua, không đoán nội dung."""

from __future__ import annotations

import re
from typing import Any

from control_plane.customers import FIELDS, LINE_FIELDS, collapse, expand_scientific
from control_plane.vietnam_places import fold_vietnamese, has_address_keyword, has_vietnam_place

_SCI = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_DIGIT = re.compile(r"\d")
_FACEBOOK_HOST = re.compile(r"(?:facebook\.com|fb\.com|fb\.me)", re.I)
_TIKTOK_HOST = re.compile(r"tiktok\.com", re.I)
_MOBILE_PREFIXES = frozenset(
    {
        "32",
        "33",
        "34",
        "35",
        "36",
        "37",
        "38",
        "39",
        "52",
        "55",
        "56",
        "58",
        "59",
        "70",
        "76",
        "77",
        "78",
        "79",
        "81",
        "82",
        "83",
        "84",
        "85",
        "86",
        "87",
        "88",
        "89",
        "90",
        "91",
        "92",
        "93",
        "94",
        "96",
        "97",
        "98",
        "99",
    }
)
_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "name": (
        "ten khach",
        "ten khach hang",
        "ho ten",
        "ho va ten",
        "hoten",
        "name",
        "ten",
        "khach",
        "khach hang",
        "full name",
        "fullname",
    ),
    "phone": (
        "so dien thoai khach",
        "so dien thoai",
        "dien thoai",
        "sdt",
        "phone",
        "tel",
        "mobile",
        "msisdn",
        "so dt",
    ),
    "address": (
        "dia chi khach",
        "dia chi",
        "dia chi 2",
        "dia chi 3",
        "address",
        "addr",
        "so nha",
        "duong",
        "ten duong",
        "pho",
        "street",
    ),
    "adminAddress": (
        "dia chi hanh chinh",
        "hanh chinh",
        "tinh thanh",
        "que quan",
        "phuong",
        "xa",
        "quan",
        "huyen",
        "tinh",
        "thanh pho",
        "ward",
        "district",
        "city",
        "province",
    ),
    "orderContent": (
        "noi dung hang mua",
        "hang mua",
        "san pham",
        "noi dung",
        "ten hang",
        "mat hang",
        "sku",
        "product",
        "item",
        "order",
    ),
    "codAmount": (
        "gia tien cod",
        "tien cod",
        "cod",
        "thu ho",
        "tien thu ho",
        "gia tien",
        "so tien",
        "thanh tien",
        "amount",
        "price",
    ),
    "facebookUid": (
        "uid facebook",
        "facebook uid",
        "uid fb",
        "fb uid",
        "id facebook",
        "facebook id",
        "facebook",
        "uid",
        "fbid",
    ),
    "tiktokId": (
        "id tiktok",
        "tiktok id",
        "tiktok",
        "id tt",
        "tt id",
        "username tiktok",
    ),
}
_FIELD_PRIORITY = {
    "tiktokId": 1.08,
    "phone": 1.06,
    "codAmount": 1.05,
    "facebookUid": 1.03,
    "address": 1.02,
    "adminAddress": 1.01,
    "orderContent": 1.005,
    "name": 1.0,
}
_MIN_SCORE = 0.42
_ADDRESS_EXTRA = 0.42
_ADDRESS_PART_HINTS = frozenset(
    {
        "dia chi",
        "dia chi 2",
        "dia chi 3",
        "address",
        "addr",
        "so nha",
        "duong",
        "ten duong",
        "pho",
        "street",
    }
)
_ADMIN_PART_HINTS = frozenset(
    {
        "dia chi hanh chinh",
        "hanh chinh",
        "phuong",
        "xa",
        "quan",
        "huyen",
        "tinh",
        "tinh thanh",
        "thanh pho",
        "que quan",
        "ward",
        "district",
        "city",
        "province",
    }
)
_REASONS = {
    "name": "chữ, không có số",
    "phone": "đầu số nhà mạng Việt Nam",
    "address": "địa chỉ giao / số nhà / đường",
    "adminAddress": "tỉnh / huyện / xã / phường",
    "orderContent": "tên hàng / size / sản phẩm",
    "codAmount": "số tiền COD / thu hộ",
    "facebookUid": "dãy số, không phải SĐT",
    "tiktokId": "bắt đầu bằng @",
}
_ORDER_HINT = re.compile(
    r"(?i)\b(?:size|sz|sku|mau|doi|chiec|giay|dep|ao|quan|nuoc mam|san pham)\b"
)
_COD_HINT = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*(?:k|nghin|tr|trieu|d|đ|vnd|dong|cod)\b")


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _national_mobile(digits: str) -> str:
    if digits.startswith("0084") and len(digits) >= 13:
        return digits[4:]
    if digits.startswith("084") and len(digits) >= 12:
        return digits[3:]
    if digits.startswith("84") and len(digits) >= 11:
        return digits[2:]
    if digits.startswith("0") and len(digits) >= 10:
        return digits[1:]
    return digits


def looks_like_vn_phone(value: str) -> bool:
    text = collapse(value)
    if not text or _FACEBOOK_HOST.search(text) or _TIKTOK_HOST.search(text):
        return False
    if text[:1] == "@":
        return False
    digits = _digits(text)
    if not digits:
        return False
    if digits.startswith("0084") and len(digits) >= 13:
        national = _national_mobile(digits)
    elif digits.startswith("084") and len(digits) >= 12:
        national = _national_mobile(digits)
    elif digits.startswith("84") and len(digits) >= 11:
        national = _national_mobile(digits)
    elif digits.startswith("0") and len(digits) == 10:
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    elif digits.startswith("02") and 10 <= len(digits) <= 11:
        return True
    else:
        return False
    return len(national) == 9 and national[:2] in _MOBILE_PREFIXES


def looks_like_facebook_uid(value: str) -> bool:
    text = collapse(value)
    if not text or looks_like_vn_phone(text):
        return False
    if _TIKTOK_HOST.search(text) or text[:1] == "@":
        return False
    if _FACEBOOK_HOST.search(text):
        return True
    expanded = expand_scientific(text)
    digits = _digits(expanded)
    if expanded != text and 5 <= len(digits) <= 20:
        return not looks_like_vn_phone(expanded)
    if re.fullmatch(r"\d{5,20}", expanded):
        return True
    return False


def looks_like_tiktok_id(value: str) -> bool:
    text = collapse(value)
    if not text:
        return False
    if text[:1] == "@":
        return True
    return bool(_TIKTOK_HOST.search(text) and "@" in text)


def looks_like_address(value: str) -> bool:
    text = collapse(value)
    if not text or looks_like_vn_phone(text) or looks_like_facebook_uid(text) or looks_like_tiktok_id(text):
        return False
    return has_vietnam_place(text) or has_address_keyword(text)


def looks_like_admin_address(value: str) -> bool:
    return looks_like_address(value)


def looks_like_cod(value: str) -> bool:
    text = collapse(value)
    if not text or looks_like_vn_phone(text) or looks_like_facebook_uid(text) or looks_like_tiktok_id(text):
        return False
    if _COD_HINT.search(text):
        return True
    digits = _digits(text)
    if not digits or (digits.startswith("0") and len(digits) >= 9):
        return False
    return 3 <= len(digits) <= 10 and bool(re.fullmatch(r"[\d.,\s]+", text))


def looks_like_order_content(value: str) -> bool:
    text = collapse(value)
    if not text or looks_like_vn_phone(text) or looks_like_facebook_uid(text) or looks_like_tiktok_id(text):
        return False
    if looks_like_cod(text) or looks_like_address(text):
        return False
    return bool(_ORDER_HINT.search(fold_vietnamese(text)))


def looks_like_name(value: str) -> bool:
    text = collapse(value)
    if not text or looks_like_tiktok_id(text) or looks_like_vn_phone(text) or looks_like_facebook_uid(text):
        return False
    if _DIGIT.search(text) or _SCI.fullmatch(text):
        return False
    if not _LETTER.search(text):
        return False
    words = fold_vietnamese(text).split()
    if not (1 <= len(words) <= 7):
        return False
    if looks_like_address(text) and (has_address_keyword(text) or len(words) >= 4 or any(character.isdigit() for character in text)):
        return False
    if looks_like_address(text) and len(words) <= 2 and has_vietnam_place(text):
        return False
    return True


def header_key(value: str) -> str:
    return fold_vietnamese(value)


def header_field(header: str) -> str | None:
    key = header_key(header)
    if not key:
        return None
    best: tuple[int, str] | None = None
    for field, hints in _HEADER_HINTS.items():
        for hint in hints:
            hit = 0
            if key == hint:
                hit = 100 + len(hint)
            elif len(hint) >= 5 and (
                key.startswith(hint + " ")
                or key.endswith(" " + hint)
                or f" {hint} " in f" {key} "
            ):
                hit = 50 + len(hint)
            if hit and (best is None or hit > best[0]):
                best = (hit, field)
    return best[1] if best else None


def _ratio(hits: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return hits / total


def score_column(header: str, values: list[str]) -> dict[str, float]:
    cells = [collapse(item) for item in values if collapse(item)]
    total = len(cells)
    scores = {field: 0.0 for field in FIELDS}
    hinted = header_field(header)
    if total:
        scores["phone"] = _ratio(sum(1 for item in cells if looks_like_vn_phone(item)), total)
        scores["facebookUid"] = _ratio(sum(1 for item in cells if looks_like_facebook_uid(item)), total)
        scores["tiktokId"] = _ratio(sum(1 for item in cells if looks_like_tiktok_id(item)), total)
        scores["address"] = _ratio(sum(1 for item in cells if looks_like_address(item)), total)
        scores["orderContent"] = _ratio(sum(1 for item in cells if looks_like_order_content(item)), total)
        scores["codAmount"] = _ratio(sum(1 for item in cells if looks_like_cod(item)), total)
        scores["name"] = _ratio(sum(1 for item in cells if looks_like_name(item)), total)
        if hinted == "adminAddress":
            scores["adminAddress"] = _ratio(sum(1 for item in cells if looks_like_admin_address(item)), total)
            scores["address"] = 0.0
    if hinted:
        if hinted == "tiktokId" and total:
            handle_like = sum(
                1
                for item in cells
                if not _DIGIT.search(item) and " " not in collapse(item) and 2 <= len(collapse(item)) <= 40
            )
            if _ratio(handle_like, total) >= 0.45:
                scores["tiktokId"] = max(scores["tiktokId"], 0.86)
                scores["name"] = min(scores["name"], 0.2)
        strongest_other = max((scores[field] for field in FIELDS if field != hinted), default=0.0)
        if not total:
            scores[hinted] = max(scores[hinted], 0.55)
        elif scores[hinted] >= 0.3 or strongest_other < 0.7:
            scores[hinted] = min(1.22, scores[hinted] + 0.22)
    return scores


def assign_fields(headers: list[str], column_values: list[list[str]]) -> tuple[dict[str, int | None], dict[str, dict[str, Any]]]:
    del column_values
    mapping: dict[str, int | None] = {field: None for field in FIELDS}
    details: dict[str, dict[str, Any]] = {}
    matches: dict[str, list[int]] = {field: [] for field in FIELDS}
    for index, header in enumerate(headers):
        field = header_field(header)
        if field is None:
            continue
        matches[field].append(index)
    for field in FIELDS:
        indexes = matches[field]
        if not indexes:
            continue
        mapping[field] = indexes[0]
        details[field] = {
            "index": indexes[0],
            "indexes": indexes if field in LINE_FIELDS else [indexes[0]],
            "score": 1.0,
            "header": collapse(headers[indexes[0]]),
            "reason": "trùng tên cột kho",
            "fromHeader": True,
        }
    return mapping, details


def suggest_mapping(
    headers: list[str],
    column_values: list[list[str]] | None = None,
) -> dict[str, int | None]:
    mapping, _details = assign_fields(headers, column_values or [[] for _ in headers])
    return mapping


def explain_mapping(details: dict[str, dict[str, Any]], headers: list[str]) -> str:
    parts: list[str] = []
    for field in FIELDS:
        item = details.get(field)
        if not item:
            continue
        indexes = item.get("indexes") or [item["index"]]
        labels = []
        for index in indexes:
            letter = chr(65 + index) if 0 <= index < 26 else str(index + 1)
            title = collapse(headers[index]) if 0 <= index < len(headers) else ""
            labels.append(f"Cột {letter}" + (f" — {title}" if title else ""))
        how = item["reason"]
        if field in LINE_FIELDS and len(indexes) > 1:
            how = "mỗi cột một dòng"
        parts.append(f"{_label(field)} ← {', '.join(labels)} ({how})")
    if not parts:
        return "Không cột nào trùng tên với kho. Cột lạ (giới tính, ngày sinh, link...) bị bỏ qua. Hãy chọn tay nếu cần."
    return "Khớp theo tên cột kho: " + "; ".join(parts) + ". Cột không giống kho đã bỏ qua."


def _label(field: str) -> str:
    labels = {
        "name": "Tên khách",
        "phone": "Số điện thoại",
        "address": "Địa chỉ",
        "adminAddress": "Địa chỉ hành chính",
        "orderContent": "Hàng mua",
        "codAmount": "Tiền COD",
        "facebookUid": "UID Facebook",
        "tiktokId": "ID TikTok",
    }
    return labels[field]
