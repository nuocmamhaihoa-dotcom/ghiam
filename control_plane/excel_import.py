"""Đọc Excel/CSV; mỗi file tự khớp cột, không lấy nhầm vị trí file khác."""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from array import array
from pathlib import Path
from typing import Any, Iterable, Iterator

from control_plane.column_detect import (
    assign_fields,
    complete_core_mapping,
    looks_like_label_row,
    suggest_mapping as detect_mapping,
)
from control_plane.customers import (
    FIELDS,
    LINE_FIELDS,
    clean_phone,
    clean_row,
    collapse,
    expand_scientific,
    has_data,
    join_address_lines,
)
from control_plane.vietnam_places import fold_vietnamese

TableSource = bytes | bytearray | os.PathLike[str] | str
IMPORT_BATCH = 2500
SHARED_SPILL = 150_000
SAMPLE_ROWS = 8
DETECT_ROWS = 200
PREVIEW_STORE = SAMPLE_ROWS + DETECT_ROWS + 1
_COL_REF = re.compile(r"^([A-Z]+)(\d+)$")

FIELD_LABELS = {
    "name": "Tên khách",
    "phone": "Số điện thoại khách",
    "address": "Địa chỉ khách",
    "adminAddress": "Địa chỉ hành chính",
    "orderContent": "Nội dung hàng mua",
    "codAmount": "Giá tiền COD",
    "facebookUid": "UID Facebook",
    "tiktokId": "ID TikTok",
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


def _si_text(item: ET.Element) -> str:
    parts: list[str] = []
    for child in item:
        tag = _local(child.tag)
        if tag == "t" and child.text:
            parts.append(child.text)
            continue
        if tag != "r":
            continue
        for node in child:
            if _local(node.tag) == "t" and node.text:
                parts.append(node.text)
    return "".join(parts)


class SharedStringStore:
    """Chuỗi dùng chung của xlsx. File lớn ghi ra đĩa, không ôm hàng triệu chuỗi trong RAM."""

    def __init__(self) -> None:
        self._values: list[str] = []
        self._path: Path | None = None
        self._handle: Any = None
        self._offsets: array[int] = array("Q")
        self._spilled = False
        self._cache: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self._offsets) if self._spilled else len(self._values)

    def prepare_disk(self) -> None:
        if self._spilled:
            return
        from control_plane.settings import settings

        settings.ensure_dirs()
        handle = tempfile.NamedTemporaryFile(
            prefix="khach-sst-",
            suffix=".bin",
            dir=str(settings.tmp_dir()),
            delete=False,
        )
        self._path = Path(handle.name)
        self._handle = handle
        self._offsets = array("Q")
        for value in self._values:
            self._write(value)
        self._values = []
        self._spilled = True

    def append(self, value: str) -> None:
        if self._spilled:
            self._write(value)
            return
        self._values.append(value)
        if len(self._values) >= SHARED_SPILL:
            self.prepare_disk()

    def _write(self, value: str) -> None:
        data = value.encode("utf-8")
        self._offsets.append(self._handle.tell())
        self._handle.write(len(data).to_bytes(4, "little"))
        self._handle.write(data)

    def finalize(self) -> None:
        if self._handle is not None:
            self._handle.flush()

    def __getitem__(self, index: int) -> str:
        if index < 0:
            raise IndexError(index)
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        if self._spilled:
            if index >= len(self._offsets):
                raise IndexError(index)
            self._handle.seek(self._offsets[index])
            size = int.from_bytes(self._handle.read(4), "little")
            value = self._handle.read(size).decode("utf-8")
        else:
            if index >= len(self._values):
                raise IndexError(index)
            value = self._values[index]
        if len(self._cache) >= 4096:
            self._cache = {key: text for key, text in self._cache.items() if key < 64}
        self._cache[index] = value
        return value

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None
        self._values = []
        self._offsets = array("Q")
        self._cache = {}
        self._spilled = False


def _shared_strings_from(source: Any) -> list[str]:
    values: list[str] = []
    try:
        for _event, elem in ET.iterparse(source, events=("end",)):
            if _local(elem.tag) != "si":
                continue
            values.append(_si_text(elem))
            elem.clear()
    except ET.ParseError:
        return values
    return values


def _shared_strings(payload: bytes) -> list[str]:
    return _shared_strings_from(io.BytesIO(payload))


def _fill_shared_strings(source: Any, store: SharedStringStore) -> None:
    try:
        for event, elem in ET.iterparse(source, events=("start", "end")):
            if event == "start" and _local(elem.tag) == "sst":
                try:
                    unique = int(elem.attrib.get("uniqueCount") or 0)
                except ValueError:
                    unique = 0
                if unique >= SHARED_SPILL:
                    store.prepare_disk()
                continue
            if event != "end" or _local(elem.tag) != "si":
                continue
            store.append(_si_text(elem))
            elem.clear()
    except ET.ParseError:
        store.finalize()
        return
    store.finalize()


def _cell_text(cell: ET.Element, shared: Any) -> str:
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


def _parse_row(row: ET.Element, shared: Any) -> list[str]:
    cells: dict[int, str] = {}
    max_col = -1
    sequential = 0
    for cell in row:
        if _local(cell.tag) != "c":
            continue
        parsed = _cell_ref(cell.attrib.get("r", ""))
        if parsed is None:
            column = sequential
        else:
            column, _row_number = parsed
        sequential = column + 1
        cells[column] = _cell_text(cell, shared)
        if column > max_col:
            max_col = column
    if max_col < 0:
        return []
    return [cells.get(index, "") for index in range(max_col + 1)]


def _sheet_rows_from(
    source: Any,
    shared: Any,
    max_store: int | None = None,
) -> tuple[list[list[str]], int]:
    table: list[list[str]] = []
    total = 0
    try:
        for _event, elem in ET.iterparse(source, events=("end",)):
            if _local(elem.tag) != "row":
                continue
            total += 1
            if max_store is None or len(table) < max_store:
                table.append(_parse_row(elem, shared))
            elem.clear()
    except ET.ParseError:
        return [], 0
    return table, total


def _sheet_rows(
    payload: bytes,
    shared: Any,
    max_store: int | None = None,
) -> tuple[list[list[str]], int]:
    return _sheet_rows_from(io.BytesIO(payload), shared, max_store=max_store)


def _rel_target_path(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return "xl/" + target.lstrip("/")


def _sheet_paths(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    if "xl/workbook.xml" not in names:
        if "xl/worksheets/sheet1.xml" in names:
            return ["xl/worksheets/sheet1.xml"]
        return []
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_by_id: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            rel_id = rel.attrib.get("Id") or ""
            target = rel.attrib.get("Target", "")
            if rel_id and target:
                rels_by_id[rel_id] = _rel_target_path(target)
    paths: list[str] = []
    seen: set[str] = set()
    for node in workbook.iter():
        if _local(node.tag) != "sheet":
            continue
        rel_id = node.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
            "",
        )
        path = rels_by_id.get(rel_id) or "xl/worksheets/sheet1.xml"
        if path in seen or path not in archive.namelist():
            continue
        seen.add(path)
        paths.append(path)
    if not paths and "xl/worksheets/sheet1.xml" in archive.namelist():
        paths.append("xl/worksheets/sheet1.xml")
    return paths


def _combine_sheets(tables: list[list[list[str]]]) -> list[list[str]]:
    kept = [table for table in tables if table]
    if not kept:
        return []
    header = [collapse(cell) for cell in kept[0][0]]
    header_index = {header_key(title): index for index, title in enumerate(header) if header_key(title)}
    combined: list[list[str]] = [list(header)]

    def ensure_column(title: str) -> int:
        key = header_key(title)
        dest = header_index.get(key)
        if dest is not None:
            return dest
        dest = len(header)
        header.append(title)
        header_index[key] = dest
        for prev in combined:
            prev.append("")
        return dest

    for table in kept:
        other_header = [collapse(cell) for cell in table[0]]
        same = [header_key(item) for item in other_header] == [header_key(item) for item in header]
        body = table[1:] if other_header and any(collapse(cell) for cell in other_header) else table
        if same:
            width = len(header)
            for row in body:
                combined.append(list(row[:width]) + [""] * max(0, width - len(row)))
            continue
        for row in body:
            aligned = [""] * len(header)
            for index, cell in enumerate(row):
                title = other_header[index] if index < len(other_header) else ""
                if not header_key(title):
                    continue
                dest = ensure_column(title)
                if dest >= len(aligned):
                    aligned.extend([""] * (dest + 1 - len(aligned)))
                aligned[dest] = cell
            if any(collapse(cell) for cell in aligned):
                combined.append(aligned)
    combined[0] = list(header)
    return combined


def _is_zip_bytes(data: TableSource) -> bool:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[:2]) == b"PK"
    path = Path(data)
    with path.open("rb") as handle:
        return handle.read(2) == b"PK"


def _open_xlsx(data: TableSource) -> zipfile.ZipFile:
    if isinstance(data, (bytes, bytearray)):
        return zipfile.ZipFile(io.BytesIO(data))
    return zipfile.ZipFile(os.fspath(data))


def _iter_sheet_rows(source: Any, shared: Any) -> Iterator[list[str]]:
    try:
        for _event, elem in ET.iterparse(source, events=("end",)):
            if _local(elem.tag) != "row":
                continue
            yield _parse_row(elem, shared)
            elem.clear()
    except ET.ParseError:
        return


def _align_row(
    row: list[str],
    other_header: list[str],
    header: list[str],
    header_index: dict[str, int],
) -> list[str]:
    aligned = [""] * len(header)
    for index, cell in enumerate(row):
        title = other_header[index] if index < len(other_header) else ""
        if not header_key(title):
            continue
        dest = header_index.get(header_key(title))
        if dest is None:
            continue
        if dest >= len(aligned):
            aligned.extend([""] * (dest + 1 - len(aligned)))
        aligned[dest] = cell
    return aligned


def iter_xlsx_rows(data: TableSource) -> Iterator[list[str]]:
    archive = None
    shared = SharedStringStore()
    try:
        archive = _open_xlsx(data)
    except zipfile.BadZipFile as exc:
        shared.close()
        raise ValueError("File Excel bị hỏng hoặc không phải .xlsx.") from exc
    try:
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as handle:
                _fill_shared_strings(handle, shared)
        paths = _sheet_paths(archive)
        if not paths:
            raise ValueError("File Excel không có bảng tính.")
        first_rows: list[list[str] | None] = []
        for path in paths:
            with archive.open(path) as handle:
                first_rows.append(next(_iter_sheet_rows(handle, shared), None))
        header: list[str] | None = None
        header_index: dict[str, int] = {}
        for first in first_rows:
            if not first:
                continue
            other_header = [collapse(cell) for cell in first]
            if header is None:
                header = list(other_header)
                header_index = {
                    header_key(title): index for index, title in enumerate(header) if header_key(title)
                }
                continue
            for title in other_header:
                if not header_key(title) or header_key(title) in header_index:
                    continue
                header_index[header_key(title)] = len(header)
                header.append(title)
        if header is None:
            return
        yield list(header)
        for path, first in zip(paths, first_rows):
            if first is None:
                continue
            other_header = [collapse(cell) for cell in first]
            same = [header_key(item) for item in other_header] == [header_key(item) for item in header]
            with archive.open(path) as handle:
                rows = _iter_sheet_rows(handle, shared)
                next(rows, None)
                for row in rows:
                    if same:
                        width = len(header)
                        yield list(row[:width]) + [""] * max(0, width - len(row))
                    else:
                        aligned = _align_row(row, other_header, header, header_index)
                        if any(collapse(cell) for cell in aligned):
                            yield aligned
    except ValueError:
        raise
    except (KeyError, ET.ParseError, zipfile.BadZipFile, OSError) as exc:
        raise ValueError("File Excel bị hỏng hoặc không đọc được.") from exc
    finally:
        shared.close()
        if archive is not None:
            archive.close()


def read_xlsx(data: TableSource, max_store: int | None = None) -> list[list[str]]:
    rows, _total = _read_xlsx(data, max_store=max_store)
    return rows


def _read_xlsx(data: TableSource, max_store: int | None = None) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    total = 0
    for row in iter_xlsx_rows(data):
        total += 1
        if max_store is None or len(rows) < max_store:
            rows.append(row)
    return rows, total


def _decode_csv_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1258", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def iter_csv_rows(data: TableSource) -> Iterator[list[str]]:
    close = None
    if isinstance(data, (bytes, bytearray)):
        text = _decode_csv_text(bytes(data))
        sample = text[:4000]
        stream: Any = io.StringIO(text)
    else:
        path = Path(data)
        text = ""
        stream = None
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1258", "cp1252"):
            try:
                handle = path.open("r", encoding=encoding, newline="")
                sample = handle.read(4000)
                handle.seek(0)
                stream = handle
                close = handle
                break
            except UnicodeDecodeError:
                if stream is not None:
                    stream.close()
                stream = None
        if stream is None:
            handle = path.open("r", encoding="utf-8", errors="replace", newline="")
            sample = handle.read(4000)
            handle.seek(0)
            stream = handle
            close = handle
    try:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        for row in csv.reader(stream, dialect):
            yield [expand_scientific(collapse(cell)) for cell in row]
    finally:
        if close is not None:
            close.close()


def read_csv(data: TableSource, max_store: int | None = None) -> list[list[str]]:
    rows, _total = _read_csv(data, max_store=max_store)
    return rows


def _read_csv(data: TableSource, max_store: int | None = None) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    total = 0
    for row in iter_csv_rows(data):
        total += 1
        if max_store is None or len(rows) < max_store:
            rows.append(row)
    return rows, total


def iter_raw_rows(filename: str, data: TableSource) -> Iterator[list[str]]:
    name = (filename or "").casefold()
    if name.endswith(".xls"):
        raise ValueError("Hãy lưu file .xls thành .xlsx rồi nạp lại.")
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        yield from iter_xlsx_rows(data)
        return
    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        yield from iter_csv_rows(data)
        return
    if _is_zip_bytes(data):
        yield from iter_xlsx_rows(data)
        return
    yield from iter_csv_rows(data)


def read_table(filename: str, data: TableSource, max_store: int | None = None) -> list[list[str]]:
    rows, _total = _read_table(filename, data, max_store=max_store)
    return rows


def _read_table(filename: str, data: TableSource, max_store: int | None = None) -> tuple[list[list[str]], int]:
    try:
        rows: list[list[str]] = []
        total = 0
        for row in iter_raw_rows(filename, data):
            total += 1
            if max_store is None or len(rows) < max_store:
                rows.append(row)
        return rows, total
    except ValueError:
        raise
    except Exception as exc:
        label = filename or "Excel"
        raise ValueError(f"Không đọc được file {label}.") from exc


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
    return trimmed


def header_key(value: str) -> str:
    return fold_vietnamese(value)


def column_values_from_rows(rows: list[list[str]], width: int) -> list[list[str]]:
    values: list[list[str]] = [[] for _ in range(width)]
    for row in rows:
        for index in range(width):
            cell = row[index] if index < len(row) else ""
            if collapse(cell):
                values[index].append(cell)
    return values


def suggest_mapping(
    headers: list[str],
    column_values: list[list[str]] | None = None,
) -> dict[str, int | None]:
    return detect_mapping(headers, column_values)


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


def _titles_from_row(row: list[str]) -> list[str]:
    return [collapse(cell) or f"Cột {column_letter(index)}" for index, cell in enumerate(row)]


def _spec_hits_header(spec: dict[str, Any] | None, headers: list[str]) -> bool:
    if resolve_index(headers, spec, allow_index=False) is not None:
        return True
    for item in _address_specs(spec):
        if resolve_index(headers, item, allow_index=False) is not None:
            return True
    return False


def _mapping_hits_headers(mapping: dict[str, Any], headers: list[str]) -> bool:
    return any(_spec_hits_header(mapping.get(field), headers) for field in FIELDS)


def _adapt_spec_to_headers(
    spec: dict[str, Any] | None,
    headers: list[str],
    line_field: bool,
) -> dict[str, Any] | None:
    """Giữ cột khi trùng tên tiêu đề; bỏ số cột của file khác."""
    if not spec:
        return None
    if line_field:
        columns: list[dict[str, Any]] = []
        for item in _address_specs(spec):
            index = resolve_index(headers, item, allow_index=False)
            if index is None:
                continue
            columns.append(
                {
                    "index": index,
                    "header": collapse(headers[index]) if 0 <= index < len(headers) else "",
                }
            )
        if not columns:
            return None
        return {"index": columns[0]["index"], "header": columns[0]["header"], "columns": columns}
    index = resolve_index(headers, spec, allow_index=False)
    if index is None:
        return None
    return {"index": index, "header": collapse(headers[index]) if 0 <= index < len(headers) else ""}


def _adapt_mapping_to_headers(mapping: dict[str, Any], headers: list[str]) -> dict[str, Any]:
    adapted: dict[str, Any] = {}
    for field in FIELDS:
        spec = _adapt_spec_to_headers(mapping.get(field), headers, field in LINE_FIELDS)
        if spec is not None:
            adapted[field] = spec
    return adapted


def _mapping_from_assign(
    headers: list[str],
    column_values: list[list[str]],
) -> dict[str, Any]:
    suggested, details = assign_fields(headers, column_values)
    detected: dict[str, Any] = {}
    for field in FIELDS:
        index = suggested.get(field)
        if index is None:
            continue
        spec: dict[str, Any] = {"index": index, "header": collapse(headers[index]) if 0 <= index < len(headers) else ""}
        if field in LINE_FIELDS:
            indexes = (details.get(field) or {}).get("indexes") or [index]
            spec["columns"] = [
                {
                    "index": item,
                    "header": collapse(headers[item]) if 0 <= item < len(headers) else "",
                }
                for item in indexes
            ]
        detected[field] = spec
    return detected


def mapping_for_file(
    mapping: dict[str, Any],
    filename: str,
    data: TableSource,
) -> dict[str, Any]:
    """Mỗi file tự khớp cột. File khác tiêu đề không lấy nhầm vị trí file đầu."""
    stored, _total = _read_table(filename, data, max_store=PREVIEW_STORE)
    rows = _trim_table(stored)
    if not rows:
        return mapping
    headers, body, _headerless = _split_header_row(rows)
    values = column_values_from_rows(body[:DETECT_ROWS], len(headers))
    if _mapping_hits_headers(mapping, headers):
        return complete_core_mapping(_adapt_mapping_to_headers(mapping, headers), headers, values)
    return _mapping_from_assign(headers, values)


def fill_missing_core_mapping(
    mapping: dict[str, Any],
    filename: str,
    data: TableSource,
) -> dict[str, Any]:
    return mapping_for_file(mapping, filename, data)


def _split_header_row(rows: list[list[str]]) -> tuple[list[str], list[list[str]], bool]:
    if not rows:
        return [], [], False
    first = rows[0]
    if looks_like_label_row(first):
        return _titles_from_row(first), rows[1:], False
    width = max(len(row) for row in rows)
    headers = [f"Cột {column_letter(index)}" for index in range(width)]
    return headers, rows, True


def preview_table(filename: str, data: TableSource) -> dict[str, Any]:
    stored, total = _read_table(filename, data, max_store=PREVIEW_STORE)
    truncated = total > len(stored)
    rows = _trim_table(stored)
    if not rows:
        raise ValueError("File không có dòng dữ liệu.")
    headers, body, headerless = _split_header_row(rows)
    samples = body[:SAMPLE_ROWS]
    detect_rows = body[:DETECT_ROWS]
    if truncated:
        row_count = total if headerless else max(len(body), total - 1)
    else:
        row_count = len(body)
    return {
        "name": filename,
        "columns": describe_columns(headers),
        "headers": headers,
        "samples": samples,
        "detectRows": detect_rows,
        "rowCount": row_count,
        "truncated": truncated,
        "suggested": suggest_mapping(headers, column_values_from_rows(detect_rows, len(headers))),
    }


def resolve_index(
    headers: list[str],
    spec: dict[str, Any] | None,
    allow_index: bool = True,
) -> int | None:
    if not spec:
        return None
    header = collapse(str(spec.get("header") or ""))
    if header:
        wanted = header_key(header)
        for index, item in enumerate(headers):
            if header_key(item) == wanted:
                return index
    if not allow_index:
        return None
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


def merge_preview_columns(previews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    seen: set[str] = set()
    first_headers = {header_key(header) for header in (previews[0].get("headers") or [])} if previews else set()
    for preview in previews:
        for column in preview.get("columns") or []:
            key = header_key(str(column.get("header") or ""))
            if not key or key in seen:
                continue
            seen.add(key)
            item = dict(column)
            if key not in first_headers:
                item["index"] = None
            columns.append(item)
    return columns


def rows_from_table(rows: list[list[str]], mapping: dict[str, Any], allow_index: bool = True) -> list[dict[str, str]]:
    table = _trim_table(rows)
    if not table:
        return []
    headers, body, _headerless = _split_header_row(table)
    indexes = {
        field: resolve_index(headers, mapping.get(field), allow_index=allow_index) for field in FIELDS
    }
    line_specs = {field: _address_specs(mapping.get(field)) for field in LINE_FIELDS}
    extracted: list[dict[str, str]] = []
    for row in body:
        raw: dict[str, str] = {}
        for field in FIELDS:
            if field in LINE_FIELDS:
                parts = []
                for spec in line_specs[field]:
                    index = resolve_index(headers, spec, allow_index=allow_index)
                    if index is not None and index < len(row):
                        parts.append(row[index])
                if not parts:
                    index = indexes[field]
                    if index is not None and index < len(row):
                        parts.append(row[index])
                raw[field] = join_address_lines(*parts)
                continue
            index = indexes[field]
            raw[field] = row[index] if index is not None and index < len(row) else ""
        cleaned = clean_row(raw)
        if has_data(cleaned):
            extracted.append(cleaned)
    return extracted


def _mapped_from_iter(
    rows: Iterator[list[str]],
    mapping: dict[str, Any],
    allow_index: bool = True,
) -> Iterator[dict[str, str]]:
    headers: list[str] | None = None
    indexes: dict[str, int | None] = {}
    line_specs: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not any(collapse(cell) for cell in row):
            continue
        if headers is None:
            if looks_like_label_row(row):
                headers = _titles_from_row(row)
                indexes = {
                    field: resolve_index(headers, mapping.get(field), allow_index=allow_index) for field in FIELDS
                }
                line_specs = {field: _address_specs(mapping.get(field)) for field in LINE_FIELDS}
                continue
            headers = [f"Cột {column_letter(index)}" for index in range(len(row))]
            indexes = {
                field: resolve_index(headers, mapping.get(field), allow_index=allow_index) for field in FIELDS
            }
            line_specs = {field: _address_specs(mapping.get(field)) for field in LINE_FIELDS}
        raw: dict[str, str] = {}
        for field in FIELDS:
            if field in LINE_FIELDS:
                parts = []
                for spec in line_specs[field]:
                    index = resolve_index(headers, spec, allow_index=allow_index)
                    if index is not None and index < len(row):
                        parts.append(row[index])
                if not parts:
                    index = indexes[field]
                    if index is not None and index < len(row):
                        parts.append(row[index])
                raw[field] = join_address_lines(*parts)
                continue
            index = indexes[field]
            raw[field] = row[index] if index is not None and index < len(row) else ""
        cleaned = clean_row(raw)
        if has_data(cleaned):
            yield cleaned


def iter_mapped_rows(
    filename: str,
    data: TableSource,
    mapping: dict[str, Any],
    allow_index: bool = True,
) -> Iterator[dict[str, str]]:
    yield from _mapped_from_iter(iter_raw_rows(filename, data), mapping, allow_index)


def iter_mapped_file(
    filename: str,
    data: TableSource,
    mapping: dict[str, Any],
    first_width: int | None,
) -> tuple[int | None, Iterator[dict[str, str]]]:
    raw = iter_raw_rows(filename, data)
    try:
        first: list[str] | None = None
        for row in raw:
            if any(collapse(cell) for cell in row):
                first = row
                break
        if first is None:
            raw.close()
            return first_width, iter(())
        width = len(first)
        if first_width is None:
            first_width = width

        def rows() -> Iterator[list[str]]:
            try:
                yield first
                yield from raw
            finally:
                raw.close()

        return first_width, _mapped_from_iter(rows(), mapping, allow_index=True)
    except Exception:
        raw.close()
        raise


def rows_from_files(
    files: Iterable[tuple[str, TableSource]],
    mapping: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    incoming: list[dict[str, str]] = []
    reports: list[dict[str, Any]] = []
    first_width: int | None = None
    for filename, data in files:
        try:
            first_width, mapped = iter_mapped_file(
                filename, data, mapping_for_file(mapping, filename, data), first_width
            )
            rows = list(mapped)
        except ValueError as exc:
            reports.append({"name": filename, "rows": 0, "error": str(exc)})
            continue
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
            f'<is><t xml:space="preserve">{xml_escape(value)}</t></is></c>'
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


def phones_from_files(files: Iterable[tuple[str, TableSource]]) -> tuple[list[str], list[dict[str, Any]]]:
    phones: list[str] = []
    reports: list[dict[str, Any]] = []
    for filename, data in files:
        found: list[str] = []
        try:
            for row in iter_raw_rows(filename, data):
                for cell in row:
                    phone = clean_phone(cell)
                    if phone:
                        found.append(phone)
        except ValueError as exc:
            reports.append({"name": filename, "phones": 0, "error": str(exc)})
            continue
        phones.extend(found)
        reports.append({"name": filename, "phones": len(found)})
    return phones, reports


def mapping_from_form(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    source = raw or {}
    mapping: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        item = source.get(field) or {}
        if not isinstance(item, dict):
            item = {"index": item}
        columns = _address_specs(item) if field in LINE_FIELDS else []
        first = columns[0] if columns else item
        mapping[field] = {
            "index": first.get("index") if isinstance(first, dict) else item.get("index"),
            "header": (first.get("header") if isinstance(first, dict) else "") or item.get("header") or "",
        }
        if field in LINE_FIELDS:
            mapping[field]["columns"] = columns or [
                {"index": item.get("index"), "header": item.get("header") or ""}
            ]
    return mapping


def _address_specs(item: dict[str, Any] | None) -> list[dict[str, Any]]:
    source = item or {}
    raw_columns = source.get("columns")
    specs: list[dict[str, Any]] = []
    if isinstance(raw_columns, list):
        for entry in raw_columns:
            if not isinstance(entry, dict):
                specs.append({"index": entry, "header": ""})
                continue
            specs.append({"index": entry.get("index"), "header": entry.get("header") or ""})
    if not specs and (source.get("index") is not None or source.get("header")):
        specs.append({"index": source.get("index"), "header": source.get("header") or ""})
    seen: set[tuple[Any, str]] = set()
    unique: list[dict[str, Any]] = []
    for spec in specs:
        key = (spec.get("index"), str(spec.get("header") or ""))
        if key in seen or (spec.get("index") is None and not spec.get("header")):
            continue
        seen.add(key)
        unique.append(spec)
    return unique
