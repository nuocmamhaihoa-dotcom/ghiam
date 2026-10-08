"""Streaming CSV reader."""

from __future__ import annotations

import csv
from pathlib import Path

from models.records import RawRecord
from parsers.byte_reader import ByteRecordReader


class CsvReader:
    def __init__(
        self,
        path: Path,
        delimiter: str,
        has_header: bool,
        encoding: str,
    ) -> None:
        self.path = path
        self.delimiter = delimiter
        self.has_header = has_header
        self.encoding = encoding
        self._columns: list[str] | None = None

    def column_names(self) -> list[str]:
        if self._columns is None:
            self._columns = _read_columns(
                self.path,
                self.delimiter,
                self.has_header,
                self.encoding,
            )
        return list(self._columns)

    def iter_stream(self, start_offset: int, start_row_number: int):
        reader = ByteRecordReader(self.path, self.encoding, quote_aware=True)
        try:
            row_number = 1
            if start_offset > 0:
                reader.seek(start_offset)
                row_number = start_row_number
            else:
                first = _next_or_none(reader)
                if first is None:
                    return
                text, offset = first
                if self.has_header:
                    if text is None:
                        raise ValueError(
                            "Không đọc được bảng mã của dòng tiêu đề. Hãy lưu file dạng UTF-8."
                        )
                    row_number = 2
                    start_row_number = 2
                else:
                    yield _csv_record(1, text, offset, self.delimiter)
                    row_number = 2
            for text, offset in reader:
                if row_number < start_row_number:
                    row_number += 1
                    continue
                yield _csv_record(row_number, text, offset, self.delimiter)
                row_number += 1
        finally:
            reader.close()


def _read_columns(path: Path, delimiter: str, has_header: bool, encoding: str) -> list[str]:
    reader = ByteRecordReader(path, encoding, quote_aware=True)
    try:
        first = _next_or_none(reader)
    finally:
        reader.close()
    if first is None or first[0] is None:
        return []
    cells = _parse_csv_line(first[0], delimiter)
    if has_header:
        return _header_names(cells)
    return [f"Cột {index + 1}" for index in range(len(cells))]


def _header_names(cells: list[str]) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    trimmed = list(cells)
    while trimmed and trimmed[-1].strip() == "":
        trimmed.pop()
    for index, cell in enumerate(trimmed):
        base = cell.strip() or f"Cột {index + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        names.append(base if count == 1 else f"{base} ({count})")
    return names


def _csv_record(row_number: int, text: str | None, offset: int, delimiter: str) -> RawRecord:
    if text is None:
        return RawRecord(row_number, (), offset, "Không đọc được ký tự trong dòng")
    if text == "":
        return RawRecord(row_number, (), offset, "Dòng trống")
    try:
        columns = tuple(_parse_csv_line(text, delimiter))
    except csv.Error as exc:
        return RawRecord(row_number, (), offset, f"CSV không hợp lệ: {exc}")
    except UnicodeError as exc:
        return RawRecord(row_number, (), offset, f"Không đọc được ký tự: {exc}")
    return RawRecord(row_number, columns, offset)


def _parse_csv_line(text: str, delimiter: str) -> list[str]:
    rows = list(csv.reader([text], delimiter=delimiter))
    if not rows:
        return []
    return rows[0]


def _next_or_none(reader: ByteRecordReader) -> tuple[str | None, int] | None:
    try:
        return next(reader)
    except StopIteration:
        return None
