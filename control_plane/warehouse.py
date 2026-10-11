"""Nhận cột từ nhiều form Excel rồi chuẩn hoá vào kho tổng.

Mỗi dòng file là một dòng kho. Ô chỉ vào một trường khi khớp dấu hiệu khóa:
số điện thoại Việt, UID Facebook (chỉ chữ số hoặc link profile), ID TikTok
(bắt đầu bằng @ hoặc link tiktok.com/@). Ô không khớp thì để trống.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FIELDS = ("name", "phone", "address", "uid", "id")
FIELD_LABELS = {
    "name": "Tên",
    "phone": "Số điện thoại",
    "address": "Địa chỉ",
    "uid": "UID Facebook",
    "id": "ID TikTok",
}
MAX_SCAN_ROWS = 40
MAX_HEADER_SCAN = 25
SAMPLE_LIMIT = 20
HEADER_WEIGHT = 0.62
ASSIGN_MIN = 0.22

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")
_PHONE_KEEP = re.compile(r"[^\d+]")
_UID_OK = re.compile(r"^\d{8,20}$")
_TIKTOK_HANDLE = re.compile(r"^[A-Za-z0-9._]{2,24}$")
_TIKTOK_URL = re.compile(r"tiktok\.com/@([A-Za-z0-9._]{2,24})\b", re.IGNORECASE)
_FB_PROFILE_ID = re.compile(
    r"(?:facebook\.com|fb\.com)/profile\.php\?[^#]*\bid=(\d{8,20})\b",
    re.IGNORECASE,
)
_FB_BARE_ID = re.compile(r"(?:facebook\.com|fb\.com)/(\d{8,20})(?:[/?#]|$)", re.IGNORECASE)
_HAS_LETTER = re.compile(r"[A-Za-zÀ-ỹ]")
_ADDRESS_MARK = re.compile(
    r"\b(duong|pho|quan|huyen|tinh|phuong|xa|thon|ap|ngo|hem|tp|tphcm|hn|"
    r"thanh pho|so nha|dia chi|q\d+|p\d+)\b"
    r"|[/]|(\d+\s*(duong|pho|ngo|hem))",
    re.IGNORECASE,
)
_STREET_WORDS = (
    "đường",
    "phố",
    "quận",
    "huyện",
    "tỉnh",
    "phường",
    "xã",
    "thôn",
    "ấp",
    "ngõ",
    "hẻm",
    "tp.",
    "tphcm",
    "thành phố",
    "số nhà",
    "địa chỉ",
)


def fold(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    folded = _PUNCT.sub(" ", stripped.casefold())
    return _SPACES.sub(" ", folded).strip()


def cell_text(value: object) -> str:
    """Đọc ô Excel thành chữ, tránh mất số 0 và tránh khoa học với số nguyên."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if abs(value) >= 2**53:
            return format(value, ".0f")
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    text = str(value).replace("\ufeff", "").strip()
    if text.lower() in {"none", "null", "nan"}:
        return ""
    return text


_HEADER_ALIASES: dict[str, frozenset[str]] = {
    "name": frozenset(
        {
            "name",
            "fullname",
            "full name",
            "customer name",
            "ten",
            "ho ten",
            "ho va ten",
            "hoten",
            "ten khach",
            "ten khach hang",
            "ten nguoi",
            "ten lien he",
            "ho ten khach",
            "customer",
            "khach hang",
        }
    ),
    "phone": frozenset(
        {
            "phone",
            "phone number",
            "tel",
            "telephone",
            "mobile",
            "cellphone",
            "sdt",
            "so dt",
            "so dien thoai",
            "dien thoai",
            "dt",
            "sdt khach",
            "so may",
            "so lien he",
        }
    ),
    "address": frozenset(
        {
            "address",
            "addr",
            "dia chi",
            "diachi",
            "dc",
            "dia chi nha",
            "noi o",
            "dia chi khach",
            "dia chi giao",
            "home address",
        }
    ),
    "uid": frozenset(
        {
            "uid",
            "uid facebook",
            "facebook uid",
            "fb uid",
            "fbid",
            "fb id",
            "facebook id",
            "id facebook",
            "uid fb",
            "link facebook",
            "link fb",
            "user uid",
            "psid",
        }
    ),
    "id": frozenset(
        {
            "tiktok",
            "tik tok",
            "tiktok id",
            "id tiktok",
            "tiktok username",
            "username tiktok",
            "tai khoan tiktok",
            "tk tiktok",
            "link tiktok",
            "tiktok link",
        }
    ),
}

_NAME_LAST = frozenset({"ho", "ho dem", "last name", "surname", "family name"})
_NAME_FIRST = frozenset({"ten rieng", "first name", "given name"})
_SKIP_HEADERS = frozenset(
    {
        "stt",
        "so thu tu",
        "no",
        "tt",
        "ghi chu",
        "note",
        "notes",
        "email",
        "mail",
        "ngay",
        "date",
        "ngay sinh",
    }
)


def header_field(text: str) -> str | None:
    key = fold(text)
    if not key or key in _SKIP_HEADERS:
        return None
    if key in _NAME_LAST or key in _NAME_FIRST:
        return "name"
    for field_name, aliases in _HEADER_ALIASES.items():
        if key in aliases:
            return field_name
    for field_name, aliases in _HEADER_ALIASES.items():
        if any(alias and (key == alias or key.startswith(alias + " ") or alias in key.split()) for alias in aliases):
            if len(key) <= 28:
                return field_name
    return None


def header_score(text: str) -> dict[str, float]:
    scores = {name: 0.0 for name in FIELDS}
    key = fold(text)
    if not key or key in _SKIP_HEADERS:
        return scores
    hit = header_field(text)
    if hit:
        scores[hit] = 1.0 if key in _HEADER_ALIASES[hit] or key in _NAME_LAST | _NAME_FIRST else 0.82
        return scores
    for field_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias and alias in key:
                scores[field_name] = max(scores[field_name], 0.55 if len(alias) >= 3 else 0.0)
    return scores


def _vn_mobile(digits: str) -> str:
    if digits.startswith("84") and len(digits) == 11:
        return "0" + digits[2:]
    if len(digits) == 9 and digits[0] in "35789":
        return "0" + digits
    if len(digits) == 10 and digits.startswith(("02", "03", "05", "07", "08", "09")):
        return digits
    if len(digits) == 11 and digits.startswith("02"):
        return digits
    return ""


def normalize_phone(raw: str) -> str:
    """Chỉ nhận số Việt / +quốc tế. Số 12–20 chữ số để dành cho UID."""
    text = cell_text(raw)
    if text.lower().startswith("tel:"):
        text = text[4:].strip()
    compact = _PHONE_KEEP.sub("", text)
    plus = compact.startswith("+")
    digits = "".join(ch for ch in compact if ch.isdigit())
    local = _vn_mobile(digits)
    if local:
        return local
    if plus and 8 <= len(digits) <= 15:
        return "+" + digits
    return ""


def phone_key(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def normalize_tiktok(raw: str) -> str:
    """ID TikTok chỉ khi ô là @tên hoặc link tiktok.com/@tên."""
    text = cell_text(raw).strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    url = _TIKTOK_URL.search(compact)
    if url:
        return "@" + url.group(1).casefold()
    if "tiktok" in compact.casefold():
        found = re.findall(r"@([A-Za-z0-9._]{2,24})", compact)
        if len(found) == 1 and _TIKTOK_HANDLE.match(found[0]):
            return "@" + found[0].casefold()
    if compact.startswith("@"):
        handle = compact[1:].split("?")[0].split("/")[0].split("#")[0]
        if _TIKTOK_HANDLE.match(handle):
            return "@" + handle.casefold()
    return ""


def normalize_id(raw: str) -> str:
    return normalize_tiktok(raw)


def id_key(value: str) -> str:
    text = cell_text(value).strip()
    if text.startswith("@"):
        return text.casefold()
    return text


def _facebook_uid(text: str) -> str:
    compact = text.replace(" ", "")
    for pattern in (_FB_PROFILE_ID, _FB_BARE_ID):
        match = pattern.search(compact)
        if match and not normalize_phone(match.group(1)):
            return match.group(1)
    return ""


def normalize_uid(raw: str) -> str:
    """UID Facebook: cả ô là chữ số 8–20, hoặc link profile. Không lấy số điện thoại."""
    text = cell_text(raw).replace(" ", "")
    if not text or normalize_tiktok(text):
        return ""
    from_url = _facebook_uid(text)
    if from_url:
        return from_url
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if _UID_OK.match(text) and not normalize_phone(text):
        return text
    return ""


def normalize_name(raw: str) -> str:
    text = _SPACES.sub(" ", cell_text(raw)).strip()
    if not text or normalize_phone(text):
        return ""
    if len(text) > 80:
        text = text[:80].rstrip()
    return text


def normalize_address(raw: str) -> str:
    text = _SPACES.sub(" ", cell_text(raw)).strip()
    if not text or normalize_phone(text):
        return ""
    return text[:200]


def name_key(name: str) -> str:
    return " ".join(normalize_name(name).casefold().split())


def address_key(address: str) -> str:
    return fold(normalize_address(address))


def normalize_field(field_name: str, raw: str) -> str:
    if field_name == "phone":
        return normalize_phone(raw)
    if field_name == "uid":
        return normalize_uid(raw)
    if field_name == "id":
        return normalize_id(raw)
    if normalize_tiktok(raw) or normalize_uid(raw) or normalize_phone(raw):
        return ""
    if field_name == "name":
        return normalize_name(raw) if _looks_name(raw) else ""
    if field_name == "address":
        return normalize_address(raw) if _looks_address(raw) else ""
    return ""


def _looks_name(text: str) -> bool:
    if normalize_tiktok(text) or normalize_uid(text) or normalize_phone(text):
        return False
    if cell_text(text).lstrip().startswith("@"):
        return False
    value = normalize_name(text)
    if len(value) < 2:
        return False
    if any(mark in value.casefold() for mark in _STREET_WORDS):
        return False
    words = value.split()
    if not (1 <= len(words) <= 6):
        return False
    digits = sum(ch.isdigit() for ch in value)
    return digits <= 1 and bool(_HAS_LETTER.search(value))


def _looks_address(text: str) -> bool:
    if normalize_tiktok(text) or normalize_uid(text) or normalize_phone(text):
        return False
    if cell_text(text).lstrip().startswith("@"):
        return False
    value = cell_text(text)
    if len(value) < 10:
        return False
    folded = fold(value)
    if _ADDRESS_MARK.search(folded):
        return True
    return len(value) >= 18 and bool(_HAS_LETTER.search(value)) and not normalize_phone(value)


def is_index_column(values: list[str]) -> bool:
    numbers: list[int] = []
    for raw in values:
        text = cell_text(raw)
        if text.isdigit():
            numbers.append(int(text))
        elif text:
            return False
    if len(numbers) < 4:
        return False
    step = sum(1 for a, b in zip(numbers, numbers[1:]) if b - a == 1)
    return step / max(1, len(numbers) - 1) >= 0.7 and numbers[0] <= 5


def shape_scores(values: list[str]) -> dict[str, float]:
    scores = {name: 0.0 for name in FIELDS}
    useful = [cell_text(item) for item in values if cell_text(item)]
    if not useful:
        return scores
    if is_index_column(useful):
        return scores
    n = len(useful)
    phone_hits = sum(1 for item in useful if normalize_phone(item))
    uid_hits = sum(1 for item in useful if normalize_uid(item))
    id_hits = sum(1 for item in useful if normalize_id(item))
    name_hits = sum(1 for item in useful if _looks_name(item))
    address_hits = sum(1 for item in useful if _looks_address(item))
    scores["phone"] = phone_hits / n
    scores["uid"] = uid_hits / n
    scores["id"] = id_hits / n
    scores["name"] = name_hits / n
    scores["address"] = address_hits / n
    if scores["phone"] >= 0.7:
        scores["uid"] *= 0.15
        scores["id"] *= 0.2
    if scores["uid"] >= 0.7:
        scores["phone"] *= 0.15
        scores["id"] *= 0.25
    return scores


@dataclass
class ColumnGuess:
    index: int
    header: str
    letter: str
    field: str | None
    confidence: float
    header_scores: dict[str, float]
    shape_scores: dict[str, float]


@dataclass
class DetectedSheet:
    name: str
    header_row: int
    mapping: dict[str, int]
    columns: list[ColumnGuess]
    sample: list[dict[str, str]]
    row_count: int
    confidence: float
    warnings: list[str]
    method: str
    fingerprint: str
    reused_template: bool = False


@dataclass
class PreviewResult:
    sheets: list[DetectedSheet]
    chosen: str
    filename: str


def column_letter(index: int) -> str:
    if index < 0:
        return "?"
    n = index + 1
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def combine_scores(header: dict[str, float], shape: dict[str, float]) -> dict[str, float]:
    header_peak = max(header.values()) if header else 0.0
    weight_h = HEADER_WEIGHT if header_peak >= 0.55 else 0.2
    weight_s = 1.0 - weight_h
    return {
        name: round(header.get(name, 0.0) * weight_h + shape.get(name, 0.0) * weight_s, 4)
        for name in FIELDS
    }


def assign_fields(columns: list[ColumnGuess]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    taken: set[int] = set()
    candidates: list[tuple[float, str, int]] = []
    for col in columns:
        combined = combine_scores(col.header_scores, col.shape_scores)
        for field_name, score in combined.items():
            if score >= ASSIGN_MIN:
                candidates.append((score, field_name, col.index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    for score, field_name, index in candidates:
        if field_name in mapping or index in taken:
            continue
        mapping[field_name] = index
        taken.add(index)
        for col in columns:
            if col.index == index:
                col.field = field_name
                col.confidence = score
    return mapping


def detect_header_row(rows: list[list[object]]) -> tuple[int, str]:
    best_row = 0
    best_score = -1.0
    method = "shape"
    limit = min(len(rows), MAX_HEADER_SCAN)
    for index in range(limit):
        cells = [cell_text(item) for item in rows[index]]
        if not any(cells):
            continue
        hits = sum(1 for cell in cells if header_field(cell))
        if hits < 2:
            continue
        following = rows[index + 1 : index + 6]
        data_like = 0
        for follow in following:
            texts = [cell_text(item) for item in follow]
            if any(normalize_phone(item) or _looks_name(item) or normalize_uid(item) for item in texts):
                data_like += 1
        score = hits + data_like * 0.4
        if score > best_score:
            best_score = score
            best_row = index
            method = "header"
    if best_score < 0:
        return 0, "shape"
    return best_row, method


def fingerprint_for(headers: Iterable[str]) -> str:
    joined = "|".join(fold(item) for item in headers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def rows_from_csv(payload: bytes) -> list[list[object]]:
    for encoding in ("utf-8-sig", "utf-8", "cp1258", "cp1252", "latin-1"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = payload.decode("utf-8", errors="replace")
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return [list(row) for row in reader]


def rows_from_xlsx(payload: bytes, sheet: str | None = None) -> tuple[str, list[str], list[list[object]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Thiếu openpyxl để đọc file Excel") from exc
    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        names = list(book.sheetnames)
        chosen = sheet if sheet in names else names[0]
        table: list[list[object]] = []
        for row in book[chosen].iter_rows(values_only=True):
            table.append(list(row))
        return chosen, names, table
    finally:
        book.close()


def list_xlsx_sheets(payload: bytes) -> list[str]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Thiếu openpyxl để đọc file Excel") from exc
    book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()


def detect_table(
    rows: list[list[object]],
    sheet_name: str,
    saved_map: dict[str, int] | None = None,
) -> DetectedSheet:
    if not rows:
        return DetectedSheet(
            name=sheet_name,
            header_row=0,
            mapping={},
            columns=[],
            sample=[],
            row_count=0,
            confidence=0.0,
            warnings=["Trang tính trống"],
            method="empty",
            fingerprint="",
        )
    width = max((len(row) for row in rows), default=0)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]
    header_row, method = detect_header_row(padded)
    headers = [cell_text(item) for item in padded[header_row]] if method == "header" else [""] * width
    data_start = header_row + 1 if method == "header" else 0
    data_rows = padded[data_start:]
    nonempty = [row for row in data_rows if any(cell_text(item) for item in row)]
    columns: list[ColumnGuess] = []
    for index in range(width):
        sample_values = [cell_text(row[index]) for row in nonempty[:MAX_SCAN_ROWS]]
        guess = ColumnGuess(
            index=index,
            header=headers[index] if index < len(headers) else "",
            letter=column_letter(index),
            field=None,
            confidence=0.0,
            header_scores=header_score(headers[index] if index < len(headers) else ""),
            shape_scores=shape_scores(sample_values),
        )
        columns.append(guess)
    reused = False
    if saved_map:
        mapping = {field_name: index for field_name, index in saved_map.items() if 0 <= index < width}
        for col in columns:
            for field_name, index in mapping.items():
                if col.index == index:
                    col.field = field_name
                    col.confidence = 1.0
        reused = True
        method = "template"
    else:
        mapping = assign_fields(columns)
    records = apply_mapping(nonempty, mapping)
    warnings: list[str] = []
    if "name" not in mapping and "phone" not in mapping:
        warnings.append("Chưa chắc cột tên và số điện thoại. Hãy sửa map trước khi nạp.")
    if any(col.shape_scores.get("uid", 0) > 0 and col.field == "uid" for col in columns):
        if any(len(cell_text(row[mapping["uid"]])) >= 16 for row in nonempty[:MAX_SCAN_ROWS] if "uid" in mapping):
            warnings.append("UID Facebook dài: nếu Excel lưu dạng số có thể mất chính xác. Nên để cột UID là chữ.")
    mapped_conf = [col.confidence for col in columns if col.field]
    confidence = round(sum(mapped_conf) / len(mapped_conf), 3) if mapped_conf else 0.0
    if reused:
        confidence = max(confidence, 0.95)
    return DetectedSheet(
        name=sheet_name,
        header_row=header_row,
        mapping=mapping,
        columns=columns,
        sample=records[:SAMPLE_LIMIT],
        row_count=len(records),
        confidence=confidence,
        warnings=warnings,
        method=method,
        fingerprint=fingerprint_for(headers),
        reused_template=reused,
    )


def classify_cell(text: str) -> list[tuple[str, float]]:
    """Một ô chỉ nhận một kiểu khóa. Không khớp thì mới xét tên hoặc địa chỉ."""
    raw = cell_text(text)
    if not raw:
        return []
    scored: list[tuple[str, float]] = []
    if normalize_tiktok(raw):
        scored.append(("id", 1.0))
    elif normalize_phone(raw):
        scored.append(("phone", 1.0))
    elif normalize_uid(raw):
        scored.append(("uid", 0.98))
    else:
        if _looks_address(raw):
            scored.append(("address", 0.9 if _ADDRESS_MARK.search(fold(raw)) else 0.7))
        if _looks_name(raw):
            scored.append(("name", 0.84 if len(raw.split()) >= 2 else 0.62))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def record_from_row(row: list[object], mapping: dict[str, int] | None = None) -> dict[str, str]:
    """Một hàng nhập → một hàng kho. Map cột nếu khớp; ô còn lại nhận dạng theo dấu hiệu."""
    mapping = mapping or {}
    record = {name: "" for name in FIELDS}
    used: set[int] = set()
    for field_name, index in mapping.items():
        if field_name not in FIELDS or index < 0 or index >= len(row) or index in used:
            continue
        text = cell_text(row[index])
        kinds = classify_cell(text)
        if any(kind == field_name for kind, _score in kinds):
            value = normalize_field(field_name, text) or (
                normalize_name(text) if field_name == "name" else ""
            )
            if field_name == "name" and not value:
                value = normalize_name(text)
            if field_name == "address" and not value:
                value = normalize_address(text)
            if value:
                record[field_name] = value
                used.add(index)
                continue
        if kinds:
            continue
        if field_name == "name" and _looks_name(text):
            value = normalize_name(text)
            if value:
                record["name"] = value
                used.add(index)
        elif field_name == "address" and _looks_address(text):
            value = normalize_address(text)
            if value:
                record["address"] = value
                used.add(index)
    leftovers: list[tuple[int, str, list[tuple[str, float]]]] = []
    for index, raw in enumerate(row):
        if index in used:
            continue
        text = cell_text(raw)
        if not text:
            continue
        leftovers.append((index, text, classify_cell(text)))
    leftovers.sort(key=lambda item: (-(item[2][0][1] if item[2] else 0.0), item[0]))
    for index, text, kinds in leftovers:
        for field_name, score in kinds:
            if record[field_name] or score < 0.55:
                continue
            value = normalize_field(field_name, text)
            if field_name == "name" and not value:
                value = normalize_name(text)
            if field_name == "address" and not value:
                value = normalize_address(text)
            if not value:
                continue
            record[field_name] = value
            used.add(index)
            break
    return record


def apply_mapping(rows: list[list[object]], mapping: dict[str, int] | None = None) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in rows:
        record = record_from_row(row, mapping)
        if not any(record.values()):
            continue
        if not record["name"] and not record["phone"] and not record["uid"] and not record["id"]:
            continue
        records.append(record)
    return records


def preview_bytes(
    payload: bytes,
    filename: str,
    templates: dict[str, dict[str, int]] | None = None,
    sheet: str | None = None,
) -> PreviewResult:
    templates = templates or {}
    suffix = Path(filename).suffix.lower()
    sheets: list[DetectedSheet] = []
    if suffix in {".xlsx", ".xlsm"}:
        names = list_xlsx_sheets(payload)
        targets = [sheet] if sheet and sheet in names else names
        for name in targets:
            _, _, rows = rows_from_xlsx(payload, name)
            headers = []
            if rows:
                header_row, method = detect_header_row(rows)
                if method == "header":
                    headers = [cell_text(item) for item in rows[header_row]]
            saved = templates.get(fingerprint_for(headers))
            sheets.append(detect_table(rows, name, saved))
    elif suffix in {".csv", ".tsv", ".txt"}:
        rows = rows_from_csv(payload)
        header_row, method = detect_header_row(rows)
        headers = [cell_text(item) for item in rows[header_row]] if rows and method == "header" else []
        saved = templates.get(fingerprint_for(headers))
        sheets.append(detect_table(rows, "csv", saved))
    else:
        raise ValueError("Chỉ nhận .xlsx, .xlsm, .csv, .tsv, .txt")
    if not sheets:
        raise ValueError("Không đọc được trang tính")
    chosen = max(sheets, key=lambda item: (item.confidence, item.row_count))
    return PreviewResult(sheets=sheets, chosen=chosen.name, filename=filename)


def sheet_to_dict(sheet: DetectedSheet) -> dict[str, Any]:
    return {
        "name": sheet.name,
        "headerRow": sheet.header_row,
        "mapping": sheet.mapping,
        "columns": [
            {
                "index": col.index,
                "letter": col.letter,
                "header": col.header,
                "field": col.field,
                "confidence": col.confidence,
                "headerScores": col.header_scores,
                "shapeScores": col.shape_scores,
            }
            for col in sheet.columns
        ],
        "sample": sheet.sample,
        "rowCount": sheet.row_count,
        "confidence": sheet.confidence,
        "warnings": sheet.warnings,
        "method": sheet.method,
        "fingerprint": sheet.fingerprint,
        "reusedTemplate": sheet.reused_template,
        "fields": FIELD_LABELS,
    }


def records_from_bytes(
    payload: bytes,
    filename: str,
    sheet: str | None,
    mapping: dict[str, int],
    header_row: int | None = None,
    has_header: bool | None = None,
) -> list[dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        _, _, rows = rows_from_xlsx(payload, sheet)
    else:
        rows = rows_from_csv(payload)
    if not rows:
        return []
    if header_row is None or has_header is None:
        header_row, method = detect_header_row(rows)
        has_header = method == "header"
    start = header_row + 1 if has_header else 0
    width = max((len(row) for row in rows), default=0)
    padded = [list(row) + [""] * (width - len(row)) for row in rows[start:]]
    nonempty = [row for row in padded if any(cell_text(item) for item in row)]
    return apply_mapping(nonempty, mapping)
