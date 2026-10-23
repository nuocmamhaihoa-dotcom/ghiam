"""Nhận cột Excel theo nội dung ô trước, tên cột chỉ là điểm phụ."""

from __future__ import annotations

import re
from typing import Any

from control_plane.customers import FIELDS, collapse, expand_scientific
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
        "tinh thanh",
        "que quan",
        "so nha",
        "duong",
        "ten duong",
        "pho",
        "phuong",
        "xa",
        "quan",
        "huyen",
        "tinh",
        "thanh pho",
        "ward",
        "district",
        "street",
        "city",
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
    "facebookUid": 1.03,
    "address": 1.02,
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
        "street",
        "city",
    }
)
_REASONS = {
    "name": "chữ, không có số",
    "phone": "đầu số nhà mạng Việt Nam",
    "address": "địa danh / đơn vị hành chính Việt Nam",
    "facebookUid": "dãy số, không phải SĐT",
    "tiktokId": "bắt đầu bằng @",
}


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
    for field, hints in _HEADER_HINTS.items():
        if key in hints:
            return field
    return None


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
        scores["name"] = _ratio(sum(1 for item in cells if looks_like_name(item)), total)
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
            scores[hinted] = min(1.0, scores[hinted] + 0.22)
    return scores


def assign_fields(headers: list[str], column_values: list[list[str]]) -> tuple[dict[str, int | None], dict[str, dict[str, Any]]]:
    mapping: dict[str, int | None] = {field: None for field in FIELDS}
    details: dict[str, dict[str, Any]] = {}
    ranked: list[tuple[float, str, int, float]] = []
    for index, header in enumerate(headers):
        values = column_values[index] if index < len(column_values) else []
        scores = score_column(header, values)
        for field, score in scores.items():
            if score < _MIN_SCORE:
                continue
            ranked.append((score * _FIELD_PRIORITY[field], field, index, score))
    ranked.sort(key=lambda item: (item[0], -item[2]), reverse=True)
    used_fields: set[str] = set()
    used_columns: set[int] = set()
    for _weighted, field, index, score in ranked:
        if field in used_fields or index in used_columns:
            continue
        mapping[field] = index
        used_fields.add(field)
        used_columns.add(index)
        hinted = header_field(headers[index]) == field
        details[field] = {
            "index": index,
            "indexes": [index],
            "score": round(score, 3),
            "header": collapse(headers[index]),
            "reason": _REASONS[field],
            "fromHeader": hinted,
        }
    address_indexes = _extra_address_columns(headers, column_values, used_columns, mapping.get("address"))
    if address_indexes:
        mapping["address"] = address_indexes[0]
        first_header = collapse(headers[address_indexes[0]])
        details["address"] = {
            "index": address_indexes[0],
            "indexes": address_indexes,
            "score": details.get("address", {}).get("score", 1.0),
            "header": first_header,
            "reason": "mỗi cột địa chỉ thành một dòng",
            "fromHeader": any(header_field(headers[index]) == "address" for index in address_indexes),
        }
    return mapping, details


def _extra_address_columns(
    headers: list[str],
    column_values: list[list[str]],
    used_columns: set[int],
    primary: int | None,
) -> list[int]:
    chosen: list[int] = []
    if primary is not None:
        chosen.append(primary)
    for index, header in enumerate(headers):
        if index in used_columns:
            continue
        values = column_values[index] if index < len(column_values) else []
        scores = score_column(header, values)
        key = header_key(header)
        part = key in _ADDRESS_PART_HINTS
        exclusive = max(scores["phone"], scores["facebookUid"], scores["tiktokId"])
        if exclusive >= 0.7:
            continue
        if scores["address"] >= _ADDRESS_EXTRA or (part and scores["name"] < 0.75):
            chosen.append(index)
            used_columns.add(index)
    return sorted(dict.fromkeys(chosen))


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
        if field == "address" and len(indexes) > 1:
            how = "mỗi cột một dòng địa chỉ"
        elif item.get("fromHeader") and field != "address":
            how = "tên cột và nội dung"
        parts.append(f"{_label(field)} ← {', '.join(labels)} ({how})")
    if not parts:
        return "Chưa chắc cột nào. Hãy chọn tay rồi nạp."
    return "Đã tự nhận: " + "; ".join(parts) + ". Hãy xem lại rồi nạp."


def _label(field: str) -> str:
    labels = {
        "name": "Tên khách",
        "phone": "Số điện thoại",
        "address": "Địa chỉ",
        "facebookUid": "UID Facebook",
        "tiktokId": "ID TikTok",
    }
    return labels[field]
