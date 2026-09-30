"""Normalize phone numbers without guessing when the format is unclear."""

from __future__ import annotations

from models.records import PhoneResult

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
