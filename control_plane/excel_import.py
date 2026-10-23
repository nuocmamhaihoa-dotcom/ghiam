"""Đọc Excel/CSV và áp một bảng chọn cột cho mọi file trong lần nạp."""

from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from control_plane.customers import FIELDS, clean_row, collapse, expand_scientific, has_data

MAX_ROWS = 200_000
SAMPLE_ROWS = 8
_COL_REF = re.compile(r"^([A-Z]+)(\d+)$")

FIELD_LABELS = {
    "name": "Tên khách",
    "phone": "Số điện thoại khách",
    "address": "Địa chỉ khách",
    "facebookUid": "UID Facebook",
    "tiktokId": "ID TikTok",
}

_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "name": (
        "ten khach",
        "tên khách",
        "ten khach hang",
        "tên khách hàng",
        "ho ten",
        "họ tên",
        "họ và tên",
        "hoten",
        "name",
        "ten",
        "tên",
        "khach",
        "khách",
    ),
    "phone": (
        "so dien thoai khach",
        "số điện thoại khách",
        "so dien thoai",
        "số điện thoại",
        "dien thoai",
        "điện thoại",
        "sdt",
        "sđt",
        "phone",
        "tel",
        "mobile",
    ),
    "address": (
        "dia chi khach",
        "địa chỉ khách",
        "dia chi",
        "địa chỉ",
        "address",
        "addr",
    ),
    "facebookUid": (
        "uid facebook",
        "facebook uid",
        "uid fb",
        "fb uid",
        "facebook",
        "uid",
        "id facebook",
        "facebook id",
    ),
    "tiktokId": (
        "id tiktok",
        "tiktok id",
        "tiktok",
        "id tt",
        "tt id",
    ),
}


def column_letter(index: int) -> str:
    if index < 0:
        return ""
    number = index + 1
    letters = []
    while number:
        number, rem = divmod(number - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cell_ref(ref: str) -> tuple[int, int] | None:
    match = _COL_REF.fullmatch((ref or "").upper())
    if not match:
        return None
    letters, row_text = match.groups()
    column = 0
    for character in letters:
        column = column * 26 + (ord(character) - 64)
    return column - 1, int(row_text)


def _shared_strings(payload: bytes) -> list[str]:
    values: list[str] = []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return values
    for item in root:
        if _local(item.tag) != "si":
            continue
        parts: list[str] = []
        for node in item.iter():
            if _local(node.tag) == "t" and node.text:
                parts.append(node.text)
        values.append("".join(parts))
    return values


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        parts: list[str] = []
        for node in cell.iter():
            if _local(node.tag) == "t" and node.text:
                parts.append(node.text)
        return expand_scientific(collapse("".join(parts)))
    value = ""
    for node in cell:
        if _local(node.tag) == "v" and node.text is not None:
            value = node.text
            break
    if kind == "s":
        try:
            return expand_scientific(collapse(shared[int(value)]))
        except (ValueError, IndexError):
            return ""
    if kind in ("b", "str", "e"):
        return collapse(value)
    return expand_scientific(collapse(value))


def _sheet_rows(payload: bytes, shared: list[str]) -> list[list[str]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    sheet_data = None
    for node in root:
        if _local(node.tag) == "sheetData":
            sheet_data = node
            break
    if sheet_data is None:
        return []
    table: list[list[str]] = []
    for row in sheet_data:
        if _local(row.tag) != "row":
            continue
        cells: dict[int, str] = {}
        max_col = -1
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            parsed = _cell_ref(cell.attrib.get("r", ""))
            if parsed is None:
                continue
            column, _row_number = parsed
            cells[column] = _cell_text(cell, shared)
            if column > max_col:
                max_col = column
        if max_col < 0:
            table.append([])
            continue
        table.append([cells.get(index, "") for index in range(max_col + 1)])
        if len(table) > MAX_ROWS + 1:
            break
    return table


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    first_id = ""
    for node in workbook.iter():
        if _local(node.tag) == "sheet":
            first_id = node.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
                "",
            )
            break
    if not first_id:
        return "xl/worksheets/sheet1.xml"
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.attrib.get("Id") == first_id:
            target = rel.attrib.get("Target", "worksheets/sheet1.xml")
            if target.startswith("/"):
                return target.lstrip("/")
            return "xl/" + target.lstrip("/")
    return "xl/worksheets/sheet1.xml"


def read_xlsx(data: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("File Excel bị hỏng hoặc không phải .xlsx.") from exc
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        shared = _shared_strings(archive.read("xl/sharedStrings.xml"))
    path = _first_sheet_path(archive)
    if path not in archive.namelist():
        raise ValueError("File Excel không có bảng tính.")
    return _sheet_rows(archive.read(path), shared)


def read_csv(data: bytes) -> list[list[str]]:
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1258", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = data.decode("utf-8", errors="replace")
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[list[str]] = []
    for row in reader:
        rows.append([expand_scientific(collapse(cell)) for cell in row])
        if len(rows) > MAX_ROWS + 1:
            break
    return rows


def read_table(filename: str, data: bytes) -> list[list[str]]:
    name = (filename or "").casefold()
    if name.endswith(".xls"):
        raise ValueError("Hãy lưu file .xls thành .xlsx rồi nạp lại.")
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return read_xlsx(data)
    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        return read_csv(data)
    if data[:2] == b"PK":
        return read_xlsx(data)
    return read_csv(data)


def _trim_table(rows: list[list[str]]) -> list[list[str]]:
    width = 0
    for row in rows:
        last = -1
        for index, cell in enumerate(row):
            if collapse(cell):
                last = index
        if last + 1 > width:
            width = last + 1
    trimmed: list[list[str]] = []
    for row in rows:
        padded = list(row[:width]) + [""] * max(0, width - len(row))
        if any(collapse(cell) for cell in padded):
            trimmed.append(padded)
        if len(trimmed) > MAX_ROWS + 1:
            break
    return trimmed


def header_key(value: str) -> str:
    return collapse(value).casefold()


def suggest_mapping(headers: list[str]) -> dict[str, int | None]:
    used: set[int] = set()
    mapping: dict[str, int | None] = {field: None for field in FIELDS}
    keyed = [header_key(header) for header in headers]
    for field, hints in _HEADER_HINTS.items():
        for index, key in enumerate(keyed):
            if index in used or not key:
                continue
            if key in hints:
                mapping[field] = index
                used.add(index)
                break
    return mapping


def describe_columns(headers: list[str]) -> list[dict[str, Any]]:
    columns = []
    for index, header in enumerate(headers):
        label = collapse(header) or f"Cột {column_letter(index)}"
        if collapse(header):
            title = f"Cột {column_letter(index)} — {label}"
        else:
            title = f"Cột {column_letter(index)}"
        columns.append({"index": index, "letter": column_letter(index), "header": label, "title": title})
    return columns


def preview_table(filename: str, data: bytes) -> dict[str, Any]:
    rows = _trim_table(read_table(filename, data))
    if not rows:
        raise ValueError("File không có dòng dữ liệu.")
    headers = [collapse(cell) or f"Cột {column_letter(index)}" for index, cell in enumerate(rows[0])]
    body = rows[1:]
    truncated = len(body) > MAX_ROWS
    body = body[:MAX_ROWS]
    samples = body[:SAMPLE_ROWS]
    return {
        "name": filename,
        "columns": describe_columns(headers),
        "headers": headers,
        "samples": samples,
        "rowCount": len(body),
        "truncated": truncated,
        "suggested": suggest_mapping(headers),
    }


def resolve_index(headers: list[str], spec: dict[str, Any] | None) -> int | None:
    if not spec:
        return None
    header = collapse(str(spec.get("header") or ""))
    if header:
        wanted = header_key(header)
        for index, item in enumerate(headers):
            if header_key(item) == wanted:
                return index
    raw_index = spec.get("index")
    if raw_index is None or raw_index == "":
        return None
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    if 0 <= index < len(headers):
        return index
    return None


def rows_from_table(rows: list[list[str]], mapping: dict[str, Any]) -> list[dict[str, str]]:
    table = _trim_table(rows)
    if not table:
        return []
    headers = [collapse(cell) or f"Cột {column_letter(index)}" for index, cell in enumerate(table[0])]
    indexes = {field: resolve_index(headers, mapping.get(field)) for field in FIELDS}
    extracted: list[dict[str, str]] = []
    for row in table[1:]:
        raw: dict[str, str] = {}
        for field in FIELDS:
            index = indexes[field]
            raw[field] = row[index] if index is not None and index < len(row) else ""
        cleaned = clean_row(raw)
        if has_data(cleaned):
            extracted.append(cleaned)
    return extracted


def rows_from_files(files: Iterable[tuple[str, bytes]], mapping: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    incoming: list[dict[str, str]] = []
    reports: list[dict[str, Any]] = []
    for filename, data in files:
        table = read_table(filename, data)
        rows = rows_from_table(table, mapping)
        incoming.extend(rows)
        reports.append({"name": filename, "rows": len(rows)})
    return incoming, reports


def write_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    def xml_escape(value: str) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def cell_xml(column: int, row_number: int, value: str) -> str:
        return (
            f'<c r="{column_letter(column)}{row_number}" t="inlineStr">'
            f"<is><t>{xml_escape(value)}</t></is></c>"
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    all_rows = [headers, *rows]
    for row_number, row in enumerate(all_rows, start=1):
        cells = "".join(cell_xml(index, row_number, cell) for index, cell in enumerate(row))
        lines.append(f'<row r="{row_number}">{cells}</row>')
    lines.append("</sheetData></worksheet>")
    sheet = "\n".join(lines)
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Khach" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def mapping_from_form(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    source = raw or {}
    mapping: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        item = source.get(field) or {}
        if not isinstance(item, dict):
            item = {"index": item}
        mapping[field] = {
            "index": item.get("index"),
            "header": item.get("header") or "",
        }
    return mapping
