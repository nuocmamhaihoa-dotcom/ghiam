"""Đọc Excel/CSV; mỗi file tự khớp cột, không lấy nhầm vị trí file khác."""

from __future__ import annotations

import codecs
import csv
import io
import os
import re
import tempfile
import unicodedata
import zipfile
import zlib
import xml.etree.ElementTree as ET
from array import array
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from control_plane.column_detect import (
    assign_fields,
    complete_core_mapping,
    explain_mapping,
    header_field,
    looks_like_facebook_uid,
    looks_like_label_row,
    looks_like_vn_phone,
    mapping_spec_chosen,
    suggested_payload,
    suggest_mapping as detect_mapping,
)
from control_plane.customers import (
    FIELDS,
    LINE_FIELDS,
    clean_phone,
    clean_row,
    collapse,
    expand_scientific,
    extract_phone,
    has_data,
    has_phone,
    is_facebook_id_digits,
    is_vn_phone,
    _incoming_address_line,
    compose_address,
    join_address_lines,
    phones_in_text,
)
from control_plane.settings import settings
from control_plane.vietnam_places import address_content_rank, address_header_rank, fold_vietnamese
from control_plane.xlsx_fast import LazySharedStrings, count_value_rows, finish_cell, iter_sheet_rows

TableSource = bytes | bytearray | os.PathLike[str] | str
IMPORT_BATCH = 2000
SHARED_SPILL = 150_000
SAMPLE_ROWS = 8
DETECT_ROWS = 200
PREVIEW_STORE = SAMPLE_ROWS + DETECT_ROWS + 1
_COL_REF = re.compile(r"^([A-Z]+)(\d+)$")
_DIGIT_RUN8 = re.compile(r"(?:\d[ .\-()]*){8}")
_XLSX_ERRORS = (KeyError, ET.ParseError, zipfile.BadZipFile, OSError, zlib.error, EOFError)
EXCEL_SHEET_ROWS = 1_000_000

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
        return finish_cell("".join(parts))
    value = ""
    for node in cell:
        if _local(node.tag) == "v" and node.text is not None:
            value = node.text
            break
    if kind == "s":
        try:
            return finish_cell(str(shared[int(value)]))
        except (ValueError, IndexError):
            return ""
    return finish_cell(value)


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

    for index, table in enumerate(kept):
        other_header = [collapse(cell) for cell in table[0]]
        same = [header_key(item) for item in other_header] == [header_key(item) for item in header]
        labeled = looks_like_label_row(table[0]) if table else False
        if index == 0:
            body = table[1:]
        elif labeled:
            body = table[1:]
        else:
            body = table
            same = True
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


def _new_shared(archive: zipfile.ZipFile) -> LazySharedStrings:
    folder = settings.tmp_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return LazySharedStrings(archive, folder)


def iter_xlsx_rows(data: TableSource) -> Iterator[list[str]]:
    try:
        archive = _open_xlsx(data)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("File Excel bị hỏng hoặc không phải .xlsx.") from exc
    shared = _new_shared(archive)
    try:
        paths = _sheet_paths(archive)
        if not paths:
            raise ValueError("File Excel không có bảng tính.")
        first_rows: list[list[str] | None] = []
        for path in paths:
            with archive.open(path) as handle:
                first_rows.append(next(iter_sheet_rows(handle, shared), None))
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
            if not looks_like_label_row(first):
                continue
            for title in other_header:
                if not header_key(title) or header_key(title) in header_index:
                    continue
                header_index[header_key(title)] = len(header)
                header.append(title)
        if header is None:
            return
        yield list(header)
        first_sheet = True
        for path, first in zip(paths, first_rows):
            if first is None:
                first_sheet = False
                continue
            labeled = looks_like_label_row(first)
            other_header = [collapse(cell) for cell in first]
            same = [header_key(item) for item in other_header] == [header_key(item) for item in header]
            skip_first = labeled or first_sheet
            first_sheet = False
            with archive.open(path) as handle:
                rows = iter_sheet_rows(handle, shared)
                first_of_sheet = next(rows, None)

                def sheet_body() -> Iterator[list[str]]:
                    if first_of_sheet is not None and not skip_first:
                        yield first_of_sheet
                    yield from rows

                width = len(header)
                for row in sheet_body():
                    if labeled and not same:
                        aligned = _align_row(row, other_header, header, header_index)
                        if any(aligned):
                            yield aligned
                        continue
                    if len(row) == width:
                        yield row
                    else:
                        yield list(row[:width]) + [""] * max(0, width - len(row))
    except ValueError:
        raise
    except _XLSX_ERRORS as exc:
        raise ValueError("File Excel bị hỏng hoặc không đọc được.") from exc
    finally:
        shared.close()
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


def _bom_encoding(head: bytes) -> str | None:
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    return None


def _csv_encoding_of_bytes(data: bytes) -> str:
    found = _bom_encoding(data[:4])
    if found:
        return found
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1258"


def _csv_encoding_of_path(path: Path) -> str:
    """UTF-8 nếu cả file hợp lệ; file Excel CSV tiếng Việt kiểu cũ thì cp1258. Không đoán theo vài nghìn ký tự đầu."""
    with path.open("rb") as handle:
        found = _bom_encoding(handle.read(4))
        if found:
            return found
        handle.seek(0)
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            while True:
                chunk = handle.read(1 << 22)
                if not chunk:
                    decoder.decode(b"", final=True)
                    return "utf-8"
                decoder.decode(chunk)
        except UnicodeDecodeError:
            return "cp1258"


def _decode_csv_text(data: bytes) -> str:
    encoding = _csv_encoding_of_bytes(data)
    text = data.decode(encoding, errors="replace")
    return unicodedata.normalize("NFC", text) if encoding == "cp1258" else text


def _iter_csv_cells(data: TableSource) -> Iterator[list[str]]:
    """cp1258 (CSV Excel tiếng Việt kiểu cũ) ghi dấu thanh tách rời; gộp về dạng chuẩn NFC."""
    close = None
    if isinstance(data, (bytes, bytearray)):
        text = _decode_csv_text(bytes(data))
        sample = text[:4000]
        stream: Any = io.StringIO(text)
    else:
        path = Path(data)
        encoding = _csv_encoding_of_path(path)
        handle = path.open("r", encoding=encoding, errors="replace", newline="")
        sample = handle.read(4000)
        handle.seek(0)
        close = handle
        stream = (unicodedata.normalize("NFC", line) for line in handle) if encoding == "cp1258" else handle
    try:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        yield from csv.reader(stream, dialect)
    finally:
        if close is not None:
            close.close()


def iter_csv_rows(data: TableSource) -> Iterator[list[str]]:
    for row in _iter_csv_cells(data):
        yield [finish_cell(cell) for cell in row]


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


def _read_head(filename: str, data: TableSource, limit: int) -> tuple[list[list[str]], bool]:
    """Đọc tối đa `limit` dòng đầu rồi dừng; True nếu file còn dòng phía sau."""
    rows: list[list[str]] = []
    stream = iter_raw_rows(filename, data)
    try:
        for row in stream:
            if len(rows) >= limit:
                return rows, True
            rows.append(row)
        return rows, False
    except ValueError:
        raise
    except Exception as exc:
        label = filename or "Excel"
        raise ValueError(f"Không đọc được file {label}.") from exc
    finally:
        stream.close()


def _first_filled_row(rows: Iterable[list[str]]) -> list[str] | None:
    for row in rows:
        if any(collapse(cell) for cell in row):
            return row
    return None


def _count_xlsx_data_rows(data: TableSource, progress: Callable[[int], None] | None = None) -> int:
    try:
        archive = _open_xlsx(data)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("File Excel bị hỏng hoặc không phải .xlsx.") from exc
    shared = _new_shared(archive)
    try:
        total = 0
        for path in _sheet_paths(archive):
            with archive.open(path) as handle:
                first = _first_filled_row(iter_sheet_rows(handle, shared))
            if first is None:
                continue
            with archive.open(path) as handle:
                done = total
                count = count_value_rows(handle, (lambda seen: progress(done + seen)) if progress else None)
            if looks_like_label_row(first):
                count -= 1
            total += max(0, count)
        return total
    except ValueError:
        raise
    except _XLSX_ERRORS as exc:
        raise ValueError("File Excel bị hỏng hoặc không đọc được.") from exc
    finally:
        shared.close()
        archive.close()


def _count_csv_data_rows(data: TableSource) -> int:
    count = 0
    first: list[str] | None = None
    for row in _iter_csv_cells(data):
        if not any(cell.strip() for cell in row):
            continue
        if first is None:
            first = [collapse(cell) for cell in row]
        count += 1
    if first is not None and looks_like_label_row(first):
        count -= 1
    return count


def count_data_rows(filename: str, data: TableSource, progress: Callable[[int], None] | None = None) -> int:
    """Số dòng khách đúng như lúc nạp: dòng có dữ liệu, trừ dòng tiêu đề đầu mỗi sheet."""
    name = (filename or "").casefold()
    if name.endswith(".xls"):
        raise ValueError("Hãy lưu file .xls thành .xlsx rồi nạp lại.")
    try:
        if _is_xlsx_source(filename, data):
            return _count_xlsx_data_rows(data, progress)
        return _count_csv_data_rows(data)
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


def _field_mapping_only(mapping: dict[str, Any] | None) -> dict[str, Any]:
    source = mapping or {}
    return {field: source[field] for field in FIELDS if field in source}


def _chosen_mapping_for_file(mapping: dict[str, Any], filename: str) -> dict[str, Any]:
    maps = mapping.get("fileMaps") if isinstance(mapping, dict) else None
    if isinstance(maps, dict):
        chosen = maps.get(filename)
        if isinstance(chosen, dict) and any(mapping_spec_chosen(chosen.get(field)) for field in FIELDS):
            return _field_mapping_only(chosen)
    return _field_mapping_only(mapping)


def chosen_mapping_for_file(mapping: dict[str, Any], filename: str) -> dict[str, Any]:
    """Cột người dùng chọn cho file này (theo tên file), chưa tự bổ sung."""
    return _chosen_mapping_for_file(mapping, filename)


def _head_rows(rows: Iterator[list[str]], limit: int | None) -> list[list[str]]:
    head: list[list[str]] = []
    for row in rows:
        head.append(row)
        if limit is not None and len(head) >= limit:
            break
    return head


def _read_first_xlsx_sheet(data: TableSource, max_store: int | None = None) -> tuple[list[list[str]], int]:
    try:
        archive = _open_xlsx(data)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("File Excel bị hỏng hoặc không phải .xlsx.") from exc
    shared = _new_shared(archive)
    try:
        paths = _sheet_paths(archive)
        if not paths:
            return [], 0
        with archive.open(paths[0]) as handle:
            rows = _head_rows(iter_sheet_rows(handle, shared), max_store)
        return rows, len(rows)
    except ValueError:
        raise
    except _XLSX_ERRORS as exc:
        raise ValueError("File Excel bị hỏng hoặc không đọc được.") from exc
    finally:
        shared.close()
        archive.close()


_GENERIC_HEADER = re.compile(r"^Cột [A-Z]{1,3}$")


def _without_generic_headers(chosen: dict[str, Any]) -> dict[str, Any]:
    """Bỏ lựa chọn kiểu "Cột A" (file không tiêu đề) khi áp sang bảng khác bố cục."""
    kept: dict[str, Any] = {}
    for field, spec in chosen.items():
        if not isinstance(spec, dict):
            continue
        header = collapse(str(spec.get("header") or ""))
        if header and not _GENERIC_HEADER.match(header):
            kept[field] = spec
    return kept


def _mapping_from_head(rows: list[list[str]], chosen: dict[str, Any]) -> dict[str, Any] | None:
    table = _trim_table(rows)
    if not table:
        return None
    headers, body, _headerless = _split_header_row(table)
    values = column_values_from_rows(body[:DETECT_ROWS], len(headers))
    auto = _mapping_from_assign(headers, values)
    user = _adapt_mapping_to_headers(chosen, headers)
    merged = dict(auto)
    merged.update(user)
    return complete_core_mapping(merged, headers, values)


def _head_signature(rows: list[list[str]]) -> str | None:
    table = _trim_table(rows)
    if not table:
        return None
    headers, _body, headerless = _split_header_row(table)
    if headerless:
        return f"#{len(headers)}"
    return _header_signature(headers)


def mapping_for_file(
    mapping: dict[str, Any],
    filename: str,
    data: TableSource,
) -> dict[str, Any]:
    """Mỗi file tự khớp cột. Chỉ lấy lựa chọn tay khi trùng tên tiêu đề của file đó."""
    chosen = _chosen_mapping_for_file(mapping, filename)
    if _is_xlsx_source(filename, data):
        stored, _total = _read_first_xlsx_sheet(data, max_store=PREVIEW_STORE)
    else:
        stored, _more = _read_head(filename, data, PREVIEW_STORE)
    return _mapping_from_head(stored, chosen) or chosen


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


def preview_table(
    filename: str,
    data: TableSource,
    progress: Callable[[int], None] | None = None,
    count_rows: bool = True,
) -> dict[str, Any]:
    stored, truncated = _read_head(filename, data, PREVIEW_STORE)
    rows = _trim_table(stored)
    if not rows:
        raise ValueError("File không có dòng dữ liệu.")
    headers, body, _headerless = _split_header_row(rows)
    samples = body[:SAMPLE_ROWS]
    detect_rows = body[:DETECT_ROWS]
    if count_rows and truncated:
        row_count = count_data_rows(filename, data, progress)
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


def _header_signature(headers: list[str]) -> str:
    return "\n".join(header_key(str(header or "")) for header in headers)


def phone_from_cell(value: str) -> str:
    """Lấy số khách trong ô: cả ô, hoặc số lẫn trong chữ. Không lấy UID Facebook."""
    return extract_phone(value)


def _usable_mapped_phone(value: str) -> str:
    if is_vn_phone(value):
        return value
    extracted = phone_from_cell(value)
    if extracted:
        return extracted
    phone = clean_phone(value)
    if not phone or is_facebook_id_digits(phone) or is_facebook_id_digits(value):
        return ""
    if looks_like_facebook_uid(value):
        return ""
    return phone


def phone_from_row_cells(row: Iterable[str], skip_indexes: set[int] | None = None) -> str:
    """Số điện thoại trên dòng — chuẩn lọc trùng khi thêm file mới."""
    skip = skip_indexes or set()
    fallback = ""
    for index, cell in enumerate(row):
        phone = phone_from_cell(cell)
        if not phone:
            continue
        if index in skip:
            if not fallback:
                fallback = phone
            continue
        return phone
    return fallback


def _phone_skip_indexes(indexes: dict[str, int | None]) -> set[int]:
    skip: set[int] = set()
    for field in ("facebookUid", "tiktokId"):
        index = indexes.get(field)
        if index is not None:
            skip.add(index)
    return skip


def _with_row_phone(
    cleaned: dict[str, str],
    row: list[str],
    indexes: dict[str, int | None],
) -> dict[str, str]:
    """SĐT trên dòng: cột đã chọn nếu dùng được, không thì số đầu tiên trên dòng. Không có thì để trống."""
    current = cleaned.get("phone") or ""
    found = _usable_mapped_phone(current) or phone_from_row_cells(row, _phone_skip_indexes(indexes))
    if found == current:
        return cleaned
    updated = dict(cleaned)
    updated["phone"] = found
    return updated


def _line_indexes(
    headers: list[str],
    indexes: dict[str, int | None],
    line_specs: dict[str, list[dict[str, Any]]],
    allow_index: bool,
) -> dict[str, list[int]]:
    resolved: dict[str, list[int]] = {}
    for field in LINE_FIELDS:
        found = [
            index
            for index in (resolve_index(headers, spec, allow_index=allow_index) for spec in line_specs[field])
            if index is not None
        ]
        if not found and indexes[field] is not None:
            found = [int(indexes[field])]
        resolved[field] = found
    return resolved


def _mapped_row(
    row: list[str],
    headers: list[str],
    indexes: dict[str, int | None],
    line_specs: dict[str, list[dict[str, Any]]],
    allow_index: bool = True,
    line_indexes: dict[str, list[int]] | None = None,
) -> dict[str, str]:
    if headers and len(row) < len(headers):
        row = list(row) + [""] * (len(headers) - len(row))
    lines = line_indexes if line_indexes is not None else _line_indexes(headers, indexes, line_specs, allow_index)
    size = len(row)
    raw: dict[str, str] = {}
    for field in FIELDS:
        if field == "address":
            pieces = [
                (index, headers[index] if index < len(headers) else "", row[index])
                for index in lines[field]
                if index < size and row[index]
            ]
            if len(pieces) <= 1:
                raw[field] = _incoming_address_line(pieces[0][2] if pieces else "")
            else:
                raw[field] = compose_address(pieces)
            continue
        if field in LINE_FIELDS:
            parts = [row[index] for index in lines[field] if index < size and row[index]]
            raw[field] = parts[0] if len(parts) == 1 else join_address_lines(*parts)
            continue
        index = indexes[field]
        raw[field] = row[index] if index is not None and index < size else ""
    return _with_row_phone(clean_row(raw), row, indexes)


def rows_from_mapped_headers(
    headers: list[str],
    body: list[list[str]],
    mapping: dict[str, Any],
) -> list[dict[str, str]]:
    """Áp cột đã chọn lên dòng mẫu, không đoán lại dòng tiêu đề."""
    indexes = {field: resolve_index(headers, mapping.get(field), allow_index=True) for field in FIELDS}
    line_specs = {field: _address_specs(mapping.get(field)) for field in LINE_FIELDS}
    extracted: list[dict[str, str]] = []
    for row in body:
        cleaned = _mapped_row(row, headers, indexes, line_specs)
        if has_phone(cleaned):
            extracted.append(cleaned)
    return extracted


def _headers_are_generic(headers: list[str]) -> bool:
    return bool(headers) and all(_GENERIC_HEADER.match(collapse(str(header)) or "") for header in headers)


def _layout_group_key(headers: list[str], payload: dict[str, Any]) -> str:
    """File không tiêu đề cùng số cột nhưng khác cột tên/SĐT/địa chỉ là hai kiểu, chọn riêng."""
    base = _header_signature(headers)
    if not _headers_are_generic(headers):
        return base
    parts: list[str] = []
    for field in ("name", "phone", "address"):
        value = payload.get(field)
        try:
            parts.append("" if value is None or value == "" else str(int(value)))
        except (TypeError, ValueError):
            parts.append("")
    return base + "\n#role:" + ",".join(parts)


def _column_address_rank(header: str, samples: list[list[str]], index: int) -> int:
    forced = address_header_rank(header)
    if forced is not None:
        return forced
    counts = [0, 0, 0, 0, 0]
    seen = 0
    for row in samples:
        if index >= len(row):
            continue
        text = collapse(str(row[index]))
        if not text:
            continue
        counts[address_content_rank(text)] += 1
        seen += 1
        if seen >= 8:
            break
    if not seen:
        return 0
    return max(range(5), key=lambda rank: (counts[rank], -rank))


def _choice_columns(headers: list[str], samples: list[list[str]]) -> list[dict[str, Any]]:
    """Cột không có tên thật thì hiện một ô mẫu, để phân biệt cột E với cột F."""
    columns = describe_columns(headers)
    for column in columns:
        header = str(column.get("header") or "")
        index = int(column["index"])
        column["addressRank"] = _column_address_rank(header, samples, index)
        if not _GENERIC_HEADER.match(header):
            continue
        sample = ""
        for row in samples:
            if index < len(row):
                sample = collapse(str(row[index]))
                if sample:
                    break
        if not sample:
            continue
        if len(sample) > 42:
            sample = sample[:41].rstrip() + "…"
        column["title"] = f"Cột {column['letter']} — {sample}"
    return columns


def _layout_picks(
    headers: list[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Mọi cột đều chọn được. Gợi ý chỉ là cột đang được chọn sẵn."""
    all_indexes = list(range(len(headers)))
    picks: dict[str, Any] = {}
    for field in FIELDS:
        suggested = payload.get(field)
        extra = [int(index) for index in (payload.get(f"{field}Columns") or []) if index is not None]
        chosen: list[int] = []
        if suggested is not None and suggested != "":
            try:
                chosen.append(int(suggested))
            except (TypeError, ValueError):
                pass
        chosen.extend(index for index in extra if index not in chosen)
        picks[field] = {
            "kind": "many" if field in LINE_FIELDS else "one",
            "indexes": all_indexes,
            "suggested": chosen if field in LINE_FIELDS else (chosen[0] if chosen else None),
        }
    return picks


def group_preview_layouts(previews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gom file cùng tiêu đề để chọn cột một lần, không trộn cột của file khác."""
    groups: list[dict[str, Any]] = []
    by_sig: dict[str, dict[str, Any]] = {}
    for preview in previews:
        headers = [str(item) for item in (preview.get("headers") or [])]
        values = column_values_from_rows(preview.get("detectRows") or preview.get("samples") or [], len(headers))
        payload, details = suggested_payload(headers, values)
        sig = _layout_group_key(headers, payload)
        group = by_sig.get(sig)
        if group is None:
            mapping = _mapping_from_assign(headers, values)
            samples = rows_from_mapped_headers(headers, preview.get("samples") or [], mapping)
            group = {
                "id": f"g{len(groups)}",
                "headers": headers,
                "columns": _choice_columns(headers, preview.get("samples") or []),
                "suggested": payload,
                "picks": _layout_picks(headers, payload),
                "detectNote": explain_mapping(details, headers),
                "mappedSamples": samples,
                "files": [],
                "rowCount": 0,
                "labelHits": sum(1 for item in details.values() if item.get("fromHeader")),
            }
            by_sig[sig] = group
            groups.append(group)
        group["files"].append(
            {
                "name": preview.get("name") or "",
                "rowCount": preview.get("rowCount") or 0,
                "truncated": bool(preview.get("truncated")),
                "samples": preview.get("samples") or [],
            }
        )
        group["rowCount"] += int(preview.get("rowCount") or 0)
    return groups


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
        cleaned = _mapped_row(row, headers, indexes, line_specs, allow_index=allow_index)
        if has_phone(cleaned):
            extracted.append(cleaned)
    return extracted


def _mapped_from_iter(
    rows: Iterator[list[str]],
    mapping: dict[str, Any],
    allow_index: bool = True,
    counts: dict[str, int] | None = None,
) -> Iterator[dict[str, str]]:
    headers: list[str] | None = None
    indexes: dict[str, int | None] = {}
    line_specs: dict[str, list[dict[str, Any]]] = {}
    lines: dict[str, list[int]] = {}
    tallies = counts if counts is not None else {}
    tallies.setdefault("fileRows", 0)
    tallies.setdefault("unmapped", 0)
    tallies.setdefault("phonesOnFile", 0)
    for row in rows:
        if not any(row):
            continue
        if headers is None:
            labeled = looks_like_label_row(row)
            if labeled:
                headers = _titles_from_row(row)
            else:
                headers = [f"Cột {column_letter(index)}" for index in range(len(row))]
            indexes = {
                field: resolve_index(headers, mapping.get(field), allow_index=allow_index) for field in FIELDS
            }
            line_specs = {field: _address_specs(mapping.get(field)) for field in LINE_FIELDS}
            lines = _line_indexes(headers, indexes, line_specs, allow_index)
            if labeled:
                continue
        tallies["fileRows"] += 1
        cleaned = _mapped_row(row, headers, indexes, line_specs, allow_index=allow_index, line_indexes=lines)
        if cleaned["phone"]:
            tallies["phonesOnFile"] += 1
            yield cleaned
        elif has_data(cleaned):
            continue
        else:
            tallies["unmapped"] += 1


def _is_xlsx_source(filename: str, data: TableSource) -> bool:
    name = (filename or "").casefold()
    if name.endswith(".xls"):
        return False
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return True
    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        return False
    return _is_zip_bytes(data)


def _mapped_from_xlsx(
    data: TableSource,
    mapping: dict[str, Any],
    allow_index: bool = True,
    counts: dict[str, int] | None = None,
    chosen: dict[str, Any] | None = None,
) -> Iterator[dict[str, str]]:
    """Mỗi sheet tự đọc tiêu đề. Sheet khác bố cục sheet đầu thì tự khớp cột riêng, không áp cột theo vị trí."""
    try:
        archive = _open_xlsx(data)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("File Excel bị hỏng hoặc không phải .xlsx.") from exc
    shared = _new_shared(archive)
    try:
        paths = _sheet_paths(archive)
        if not paths:
            raise ValueError("File Excel không có bảng tính.")
        base_signature: str | None = None
        for path in paths:
            sheet_mapping = mapping
            if chosen is not None and len(paths) > 1:
                with archive.open(path) as handle:
                    head = _head_rows(iter_sheet_rows(handle, shared), PREVIEW_STORE)
                signature = _head_signature(head)
                if signature is None:
                    pass
                elif base_signature is None:
                    base_signature = signature
                elif signature != base_signature:
                    sheet_mapping = _mapping_from_head(head, _without_generic_headers(chosen)) or mapping
            with archive.open(path) as handle:
                yield from _mapped_from_iter(
                    iter_sheet_rows(handle, shared),
                    sheet_mapping,
                    allow_index=allow_index,
                    counts=counts,
                )
    except ValueError:
        raise
    except _XLSX_ERRORS as exc:
        raise ValueError("File Excel bị hỏng hoặc không đọc được.") from exc
    finally:
        shared.close()
        archive.close()


def iter_mapped_rows(
    filename: str,
    data: TableSource,
    mapping: dict[str, Any],
    allow_index: bool = True,
) -> Iterator[dict[str, str]]:
    if _is_xlsx_source(filename, data):
        yield from _mapped_from_xlsx(data, mapping, allow_index=allow_index)
        return
    yield from _mapped_from_iter(iter_raw_rows(filename, data), mapping, allow_index)


def iter_mapped_file(
    filename: str,
    data: TableSource,
    mapping: dict[str, Any],
    first_width: int | None,
    counts: dict[str, int] | None = None,
    chosen: dict[str, Any] | None = None,
) -> tuple[int | None, Iterator[dict[str, str]]]:
    if _is_xlsx_source(filename, data):
        return first_width, _mapped_from_xlsx(data, mapping, allow_index=True, counts=counts, chosen=chosen)
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

        return first_width, _mapped_from_iter(rows(), mapping, allow_index=True, counts=counts)
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


def _xlsx_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\r\n", "&#10;")
        .replace("\r", "&#10;")
        .replace("\n", "&#10;")
    )


_XLSX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
  </cellXfs>
</styleSheet>
"""


def _xlsx_cell(column: int, row_number: int, value: str) -> str:
    style = ' s="1"' if "\n" in value or "\r" in value else ""
    return (
        f'<c r="{column_letter(column)}{row_number}"{style} t="inlineStr">'
        f'<is><t xml:space="preserve">{_xlsx_escape(value)}</t></is></c>'
    )


def _xlsx_row_xml(row_number: int, row: Iterable[str]) -> str:
    cells_list = [str(cell or "") for cell in row]
    line_count = 1
    for cell in cells_list:
        line_count = max(line_count, cell.count("\n") + cell.count("\r") + 1)
    height = ""
    if line_count > 1:
        points = min(line_count * 15, 240)
        height = f' ht="{points}" customHeight="1"'
    cells = "".join(_xlsx_cell(index, row_number, cell) for index, cell in enumerate(cells_list))
    return f'<row r="{row_number}"{height}>{cells}</row>\n'


def _xlsx_sheet_header() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>\n"
    )


def _xlsx_sheet_footer() -> str:
    return "</sheetData></worksheet>\n"


def _xlsx_pack(dest: Path | io.BytesIO, sheets: list[tuple[str, Path | str]]) -> None:
    if not sheets:
        sheets = [("Khach", "")]
    overrides = [
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    ]
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
        '  <Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\n'
        + "\n".join(f"  {item}" for item in overrides)
        + "\n</Types>\n"
    )
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    sheet_nodes = "".join(
        f'<sheet name="{_xlsx_escape(name or f"Sheet{index}")}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _body) in enumerate(sheets, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        f"  <sheets>{sheet_nodes}</sheets>\n"
        "</workbook>\n"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        + "".join(
            f'  <Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>\n'
            for index in range(1, len(sheets) + 1)
        )
        + '  <Relationship Id="rIdStyles" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>\n'
        + "</Relationships>\n"
    )
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", _XLSX_STYLES)
        for index, (_name, body) in enumerate(sheets, start=1):
            part = f"xl/worksheets/sheet{index}.xml"
            if isinstance(body, Path):
                archive.write(body, part)
            else:
                archive.writestr(part, body)


def write_xlsx_sheets(sheets: list[tuple[str, list[list[str]]]]) -> bytes:
    """Nhiều sheet. Mỗi sheet là toàn bộ dòng (gồm tiêu đề nếu có)."""
    packed: list[tuple[str, str]] = []
    for name, rows in sheets or [("Khach", [])]:
        xml = [_xlsx_sheet_header()]
        for row_number, row in enumerate(rows, start=1):
            xml.append(_xlsx_row_xml(row_number, row))
        xml.append(_xlsx_sheet_footer())
        packed.append((name or "Khach", "".join(xml)))
    buffer = io.BytesIO()
    _xlsx_pack(buffer, packed)
    return buffer.getvalue()


def write_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    return write_xlsx_sheets([("Khach", [headers, *rows])])


def write_xlsx_to_path(
    headers: list[str],
    rows: Iterable[list[str]],
    dest: Path,
    *,
    sheet_rows: int = EXCEL_SHEET_ROWS,
) -> Path:
    """Ghi Excel ra đĩa từng dòng, chia sheet trước 1.048.576 dòng. Không giữ cả kho trong RAM."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = max(1, int(sheet_rows))
    header_row = [str(item or "") for item in headers]
    with tempfile.TemporaryDirectory(prefix="kho-xlsx-") as tmp:
        tmp_path = Path(tmp)
        sheet_files: list[tuple[str, Path]] = []
        sheet_index = 1
        sheet_path = tmp_path / f"sheet{sheet_index}.xml"
        handle = sheet_path.open("w", encoding="utf-8")
        data_in_sheet = 0
        row_number = 1
        try:
            handle.write(_xlsx_sheet_header())
            handle.write(_xlsx_row_xml(row_number, header_row))

            def close_sheet() -> None:
                handle.write(_xlsx_sheet_footer())
                handle.close()
                name = "Khach" if sheet_index == 1 else f"Khach {sheet_index}"
                sheet_files.append((name, sheet_path))

            for row in rows:
                if data_in_sheet >= limit:
                    close_sheet()
                    sheet_index += 1
                    sheet_path = tmp_path / f"sheet{sheet_index}.xml"
                    handle = sheet_path.open("w", encoding="utf-8")
                    data_in_sheet = 0
                    row_number = 1
                    handle.write(_xlsx_sheet_header())
                    handle.write(_xlsx_row_xml(row_number, header_row))
                row_number += 1
                data_in_sheet += 1
                handle.write(_xlsx_row_xml(row_number, [str(cell or "") for cell in row]))
            if not handle.closed:
                close_sheet()
        except Exception:
            if not handle.closed:
                handle.close()
            raise
        _xlsx_pack(dest, sheet_files)
    return dest


def _phone_columns(sample: list[list[str]]) -> set[int]:
    """Cột SĐT của file tra cứu: tiêu đề là SĐT, hoặc ít nhất nửa số ô mẫu là số điện thoại."""
    if not sample:
        return set()
    columns: set[int] = set()
    body = sample
    if looks_like_label_row(sample[0]):
        columns.update(index for index, header in enumerate(sample[0]) if header_field(header) == "phone")
        body = sample[1:]
    width = max((len(row) for row in body), default=0)
    filled = [0] * width
    hits = [0] * width
    for row in body:
        for index, cell in enumerate(row[:width]):
            if not cell:
                continue
            filled[index] += 1
            if extract_phone(cell):
                hits[index] += 1
    columns.update(index for index in range(width) if filled[index] and hits[index] * 2 >= filled[index])
    return columns


def phones_in_row(row: list[str], phone_columns: set[int]) -> list[str]:
    """Mọi SĐT trên dòng: cột SĐT lấy cả số lạ; cột khác chỉ lấy số Việt Nam; một ô nhiều số thì lấy hết."""
    found: list[str] = []
    for index, cell in enumerate(row):
        if not cell:
            continue
        if index in phone_columns:
            phones = phones_in_text(cell)
            if not phones:
                single = extract_phone(cell)
                phones = [single] if single else []
        elif _DIGIT_RUN8.search(cell):
            phones = phones_in_text(cell)
        else:
            continue
        for phone in phones:
            if phone not in found:
                found.append(phone)
    return found


def phones_from_files(
    files: Iterable[tuple[str, TableSource]],
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    phones: list[str] = []
    reports: list[dict[str, Any]] = []
    for filename, data in files:
        found: list[str] = []
        rows_seen = 0
        try:
            stream = iter_raw_rows(filename, data)
            try:
                sample = _head_rows(stream, DETECT_ROWS)
                columns = _phone_columns(sample)
                for row in sample:
                    rows_seen += 1
                    found.extend(phones_in_row(row, columns))
                for row in stream:
                    rows_seen += 1
                    found.extend(phones_in_row(row, columns))
                    if progress is not None and rows_seen % 50000 == 0:
                        progress({"file": filename, "rows": rows_seen, "phones": len(found)})
            finally:
                stream.close()
        except ValueError as exc:
            reports.append({"name": filename, "phones": 0, "rows": rows_seen, "error": str(exc)})
            continue
        phones.extend(found)
        reports.append({"name": filename, "phones": len(found), "rows": rows_seen})
    return phones, reports


def mapping_from_form(raw: dict[str, Any] | None, *, _nested: bool = False) -> dict[str, Any]:
    source = raw or {}
    mapping: dict[str, Any] = {}
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
    if not _nested and isinstance(source.get("fileMaps"), dict):
        mapping["fileMaps"] = {
            str(name): mapping_from_form(spec, _nested=True)
            for name, spec in source["fileMaps"].items()
            if isinstance(spec, dict) and str(name).strip()
        }
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
