"""Streaming XLSX reader using openpyxl read-only mode."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from models.records import RawRecord


class XlsxReader:
    def __init__(self, path: Path, has_header: bool) -> None:
        self.path = path
        self.has_header = has_header

    def column_names(self) -> list[str]:
        workbook = load_workbook(self.path, read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            first = next(sheet.iter_rows(values_only=True), None)
        finally:
            workbook.close()
        if first is None:
            return []
        cells = [_cell_to_str(value) for value in first]
        if self.has_header:
            return _header_names(cells)
        return [f"Cột {index + 1}" for index in range(len(cells))]

    def iter_stream(self, start_offset: int, start_row_number: int):
        del start_offset
        workbook = load_workbook(self.path, read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            pending_blank: list[RawRecord] = []
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if row_number < start_row_number:
                    continue
                if self.has_header and row_number == 1:
                    continue
                columns = tuple(_cell_to_str(value) for value in row)
                if all(cell == "" for cell in columns):
                    pending_blank.append(
                        RawRecord(row_number, (), row_number, "Dòng trống")
                    )
                    continue
                for blank in pending_blank:
                    yield blank
                pending_blank.clear()
                yield RawRecord(row_number, columns, row_number)
        finally:
            workbook.close()


def _cell_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".")
    return str(value)


def _header_names(cells: list[str]) -> list[str]:
    trimmed = list(cells)
    while trimmed and trimmed[-1].strip() == "":
        trimmed.pop()
    names: list[str] = []
    seen: dict[str, int] = {}
    for index, cell in enumerate(trimmed):
        base = cell.strip() or f"Cột {index + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        names.append(base if count == 1 else f"{base} ({count})")
    return names
