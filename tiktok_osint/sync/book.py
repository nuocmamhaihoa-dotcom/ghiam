from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from tiktok_osint.domain.normalize import normalize_email, normalize_phone
from tiktok_osint.errors import ValidationError


@dataclass(frozen=True)
class ImportedContact:
    display_name: str
    phone_raw: str | None
    phone_e164: str | None
    email: str | None


def parse_contact_csv(text: str) -> list[ImportedContact]:
    sample = text.lstrip("\ufeff")
    if not sample.strip():
        return []
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames:
        raise ValidationError("CSV danh bạ cần dòng tiêu đề")
    fields = {name.strip().lower(): name for name in reader.fieldnames}
    name_key = _pick(fields, ("name", "display_name", "ten", "họ tên", "ho ten"))
    phone_key = _pick(fields, ("phone", "tel", "sdt", "sđt", "mobile"))
    email_key = _pick(fields, ("email", "mail"))
    if name_key is None and phone_key is None and email_key is None:
        raise ValidationError("CSV cần cột name, phone hoặc email")
    rows: list[ImportedContact] = []
    for raw in reader:
        rows.append(
            build_contact(
                display_name=_cell(raw, name_key) or "Không tên",
                phone=_cell(raw, phone_key),
                email=_cell(raw, email_key),
            )
        )
    return [row for row in rows if row.phone_raw or row.email or row.display_name != "Không tên"]


_MAX_PHONE_LINES = 5000
_PHONE_CANDIDATE = re.compile(r"(?<!\d)(?:\+|00)?\d(?:[\d.\-\s()]{0,18}\d)?(?!\d)")


def parse_phone_lines(text: str) -> tuple[list[ImportedContact], int, list[dict[str, object]]]:
    """Đọc danh sách số điện thoại do người dùng dán: mỗi dòng một số, có thể kèm tên."""
    lines = text.splitlines()
    data_lines = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    if len(data_lines) > _MAX_PHONE_LINES:
        raise ValidationError(f"Mỗi lần nhập tối đa {_MAX_PHONE_LINES} số")
    contacts: list[ImportedContact] = []
    rejected: list[dict[str, object]] = []
    seen: set[str] = set()
    skipped = 0
    for index, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = _first_phone(line)
        if found is None:
            rejected.append({"line": index, "raw": line[:180], "reason": "Không thấy số điện thoại hợp lệ"})
            continue
        token, start, end = found
        name = _name_around_phone(line, start, end)
        contact = build_contact(display_name=name or token, phone=token, email=None)
        if contact.phone_e164 is None:
            rejected.append({"line": index, "raw": line[:180], "reason": "Số không chuẩn hóa được"})
            continue
        if contact.phone_e164 in seen:
            skipped += 1
            continue
        seen.add(contact.phone_e164)
        contacts.append(contact)
    return contacts, skipped, rejected


def parse_address_book_file(filename: str, text: str) -> tuple[list[ImportedContact], int, list[dict[str, object]]]:
    lower = filename.lower()
    if lower.endswith(".vcf"):
        return parse_vcard(text), 0, []
    if lower.endswith(".csv") and _csv_has_header(text):
        return parse_contact_csv(text), 0, []
    return parse_phone_lines(text)


def parse_vcard(text: str) -> list[ImportedContact]:
    cards = re.split(r"(?i)END:VCARD", text)
    rows: list[ImportedContact] = []
    for card in cards:
        if "BEGIN:VCARD" not in card.upper():
            continue
        unfolded = re.sub(r"\r?\n[ \t]", "", card)
        name = _vcard_value(unfolded, "FN") or _vcard_value(unfolded, "N") or "Không tên"
        phones = re.findall(r"(?im)^TEL[^:]*:(.+)$", unfolded)
        emails = re.findall(r"(?im)^EMAIL[^:]*:(.+)$", unfolded)
        phone = phones[0].strip() if phones else None
        email = emails[0].strip() if emails else None
        if name == "Không tên" and not phone and not email:
            continue
        rows.append(build_contact(display_name=name.replace(";", " ").strip(), phone=phone, email=email))
    return rows


def build_contact(*, display_name: str, phone: str | None, email: str | None) -> ImportedContact:
    clean_name = " ".join(display_name.split()).strip() or "Không tên"
    if len(clean_name) > 200:
        raise ValidationError("Tên liên hệ quá dài")
    phone_raw = phone.strip() if phone and phone.strip() else None
    email_raw = email.strip() if email and email.strip() else None
    return ImportedContact(
        display_name=clean_name,
        phone_raw=phone_raw,
        phone_e164=normalize_phone(phone_raw) if phone_raw else None,
        email=normalize_email(email_raw) if email_raw else None,
    )


def _pick(fields: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in fields:
            return fields[name]
    return None


def _cell(row: dict[str, str | None], key: str | None) -> str | None:
    if key is None:
        return None
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _vcard_value(card: str, field: str) -> str | None:
    match = re.search(rf"(?im)^{field}[^:]*:(.+)$", card)
    if not match:
        return None
    return match.group(1).strip() or None


def _first_phone(line: str) -> tuple[str, int, int] | None:
    for match in _PHONE_CANDIDATE.finditer(line):
        token = match.group(0).strip()
        if normalize_phone(token):
            return token, match.start(), match.end()
    return None


def _name_around_phone(line: str, start: int, end: int) -> str | None:
    raw = f"{line[:start]} {line[end:]}"
    raw = re.sub(r"[,;\|\t]+", " ", raw)
    raw = re.sub(r"\s+[-–—]\s+", " ", raw)
    name = " ".join(raw.split()).strip(" -–—")
    return name or None


def _csv_has_header(text: str) -> bool:
    sample = text.lstrip("\ufeff").strip()
    if not sample:
        return False
    header = sample.splitlines()[0].strip().lower()
    tokens = {part.strip() for part in re.split(r"[,;\t]", header)}
    known = {"name", "display_name", "ten", "họ tên", "ho ten", "phone", "tel", "sdt", "sđt", "mobile", "email", "mail"}
    return bool(tokens & known)
