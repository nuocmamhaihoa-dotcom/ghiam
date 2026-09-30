"""Normalize phone numbers without guessing when the format is unclear."""

from __future__ import annotations

import re

from models.records import PhoneResult

_FIELD_SPLIT = re.compile(r"[\n\r;/|]+")

_SEPARATORS = str.maketrans("", "", " \t-().")


def normalize_phone(raw: str, *, normalize: bool, vn_to_e164: bool) -> PhoneResult:
    """Clean a phone number.

    When ``normalize`` is false, only outer whitespace is removed.
    When ``vn_to_e164`` is true, a clear Vietnam number is rewritten to +84.
    Numbers that are not clearly a phone are rejected instead of rewritten.
    """
    if raw is None:
        return PhoneResult(False, "", "Thiếu số điện thoại")
    original = str(raw).strip()
    if original == "":
        return PhoneResult(False, "", "Thiếu số điện thoại")

    if not normalize:
        if _invalid(original, allow_separators=True):
            return PhoneResult(False, original, "Số chứa ký tự không hợp lệ")
        digits = _digits(original)
        if len(digits) < 8:
            return PhoneResult(False, original, "Số quá ngắn")
        if len(digits) > 15:
            return PhoneResult(False, original, "Số quá dài")
        return PhoneResult(True, original, "")

    cleaned = original.translate(_SEPARATORS)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if _invalid(cleaned, allow_separators=False):
        return PhoneResult(False, original, "Số chứa ký tự không hợp lệ")

    if vn_to_e164:
        converted = _to_e164(cleaned)
        if converted is None:
            return PhoneResult(
                False,
                original,
                "Số không xác định, không đủ cơ sở để chuẩn hóa",
            )
        return PhoneResult(True, converted, "")

    if cleaned.startswith("+"):
        body = cleaned[1:]
        if not body.isdigit():
            return PhoneResult(False, original, "Số chứa ký tự không hợp lệ")
        return _length_result(original, body, plus=True)
    if not cleaned.isdigit():
        return PhoneResult(False, original, "Số chứa ký tự không hợp lệ")
    return _length_result(original, cleaned, plus=False)


def canonical_phones(raw: str) -> list[str]:
    """Return every stored phone from one cell.

    A clear Vietnam number becomes ``+84…``. Any other value that contains
    digits is kept, so a short or foreign number is still stored. A cell
    with no digits produces an empty list.
    """
    if raw is None:
        return []
    original = str(raw).strip()
    if original == "":
        return []
    parts = _phone_parts(original)
    if len(parts) <= 1:
        phone = _canonical_one(original)
        return [phone] if phone else []
    found = [phone for part in parts if (phone := _canonical_one(part))]
    if found:
        return found
    phone = _canonical_one(original)
    return [phone] if phone else []


def _canonical_one(raw: str) -> str | None:
    cleaned = str(raw).strip().translate(_SEPARATORS).replace(",", "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if any(character.isalpha() for character in cleaned):
        runs = re.findall(r"\d+", cleaned)
        long_runs = [run for run in runs if len(run) >= 8]
        if long_runs:
            cleaned = max(long_runs, key=len)
        elif runs:
            cleaned = "".join(runs)
        else:
            return None
    if cleaned.startswith("+"):
        body = cleaned[1:]
        if not body.isdigit():
            digits = _digits(body)
            return digits or None
        converted = _to_e164("+" + body)
        return converted if converted is not None else "+" + body
    digits = _digits(cleaned)
    if digits == "":
        return None
    converted = _to_e164(digits)
    return converted if converted is not None else digits


def extract_phones(raw: str, *, normalize: bool, vn_to_e164: bool) -> list[PhoneResult]:
    """Return every clear phone in one cell.

    A single number, including spaced or dotted forms, stays one phone.
    A cell such as ``090… / 091…`` becomes one result per number.
    """
    if raw is None:
        return [PhoneResult(False, "", "Thiếu số điện thoại")]
    original = str(raw).strip()
    if original == "":
        return [PhoneResult(False, "", "Thiếu số điện thoại")]
    direct = normalize_phone(original, normalize=normalize, vn_to_e164=vn_to_e164)
    if direct.ok:
        return [direct]
    parts = _phone_parts(original)
    if len(parts) <= 1:
        return [direct]
    parsed = [
        normalize_phone(part, normalize=normalize, vn_to_e164=vn_to_e164) for part in parts
    ]
    good = [item for item in parsed if item.ok]
    if good:
        return good
    return [direct]


def _phone_parts(value: str) -> list[str]:
    parts = [part.strip() for part in _FIELD_SPLIT.split(value) if part.strip()]
    if len(parts) > 1:
        return parts
    comma_parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(comma_parts) >= 2 and all(_digit_count(part) >= 8 for part in comma_parts):
        return comma_parts
    return parts


def _digit_count(value: str) -> int:
    return sum(character.isdigit() for character in value)


def prepare_name(raw: str, *, keep_original: bool) -> tuple[str | None, str]:
    if raw is None:
        return None, "Thiếu tên"
    text = str(raw).replace("\x00", "")
    if not keep_original:
        text = " ".join(text.split())
    if text.strip() == "":
        return None, "Thiếu tên"
    return text, ""


def _to_e164(cleaned: str) -> str | None:
    if cleaned.startswith("+"):
        body = cleaned[1:]
        if not body.isdigit() or body.startswith("0"):
            return None
        if body.startswith("84"):
            national = body[2:]
            if national.startswith("0"):
                national = national[1:]
            if 8 <= len(national) <= 10:
                return "+84" + national
            return None
        if 8 <= len(body) <= 15:
            return "+" + body
        return None

    if not cleaned.isdigit():
        return None
    if cleaned.startswith("0") and len(cleaned) in (10, 11):
        return "+84" + cleaned[1:]
    if cleaned.startswith("84") and len(cleaned) in (11, 12):
        national = cleaned[2:]
        if national.startswith("0"):
            return None
        if 9 <= len(national) <= 10:
            return "+84" + national
    return None


def _length_result(original: str, digits: str, *, plus: bool) -> PhoneResult:
    if len(digits) < 8:
        return PhoneResult(False, original, "Số quá ngắn")
    if len(digits) > 15:
        return PhoneResult(False, original, "Số quá dài")
    phone = f"+{digits}" if plus else digits
    return PhoneResult(True, phone, "")


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _invalid(value: str, *, allow_separators: bool) -> bool:
    allowed = set("0123456789+")
    if allow_separators:
        allowed.update(" \t-().")
    if any(character not in allowed for character in value):
        return True
    if value.count("+") > 1:
        return True
    if "+" in value and not value.startswith("+"):
        return True
    return False
