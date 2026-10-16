"""Chuẩn hoá số di động Việt Nam và cho biết nhà mạng gốc.

Chỉ dùng để hiện thêm cột và xuất file. Số đã lưu không bị đổi.
Nhà mạng là nhà mạng cấp đầu số; khách chuyển mạng giữ số thì có thể đã khác.
"""

from __future__ import annotations

# Đầu số di động 10 chữ số sau đợt đổi số năm 2018.
CARRIERS: dict[str, str] = {
    **{prefix: "Viettel" for prefix in ("032", "033", "034", "035", "036", "037", "038", "039", "086", "096", "097", "098")},
    **{prefix: "Vinaphone" for prefix in ("081", "082", "083", "084", "085", "088", "091", "094")},
    **{prefix: "MobiFone" for prefix in ("070", "076", "077", "078", "079", "089", "090", "093")},
    **{prefix: "Vietnamobile" for prefix in ("052", "056", "058", "092")},
    **{prefix: "Gmobile" for prefix in ("059", "099")},
    "087": "Itelecom",
    "055": "Reddi",
}


def normalize_vn(raw: str) -> str:
    """Số 10 chữ số bắt đầu bằng 0 nếu nhận ra được, không thì chuỗi rỗng."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits.startswith("0084"):
        digits = "0" + digits[4:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    elif len(digits) == 9 and digits[0] in "35789":
        digits = "0" + digits
    if len(digits) == 10 and digits.startswith("0"):
        return digits
    return ""


def carrier(raw: str) -> str:
    """Nhà mạng gốc theo đầu số. Số không đúng dạng di động Việt Nam thì rỗng."""
    number = normalize_vn(raw)
    return CARRIERS.get(number[:3], "") if number else ""


def phone_columns(raw: str) -> tuple[str, str, str]:
    """(số chuẩn, nhà mạng gốc, ghi chú kiểm số) cho một ô số điện thoại."""
    number = normalize_vn(raw)
    if not raw:
        return "", "", ""
    if not number:
        return "", "", "Không đúng dạng số di động Việt Nam"
    name = CARRIERS.get(number[:3], "")
    return number, name, "" if name else "Đầu số chưa rõ nhà mạng"
