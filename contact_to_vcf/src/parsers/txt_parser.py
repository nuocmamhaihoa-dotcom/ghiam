"""Streaming TXT reader. The delimiter is chosen by the user."""

from __future__ import annotations

from pathlib import Path

from models.records import RawRecord
from parsers.byte_reader import ByteRecordReader


class TxtReader:
    def __init__(self, path: Path, delimiter: str, has_header: bool, encoding: str) -> None:
        self.path = path
        self.delimiter = delimiter
        self.has_header = has_header
        self.encoding = encoding

    def column_names(self) -> list[str]:
        reader = ByteRecordReader(self.path, self.encoding, quote_aware=False)
        try:
            try:
                text, _offset = next(reader)
            except StopIteration:
                return []
        finally:
            reader.close()
        if text is None:
            raise ValueError("Không đọc được bảng mã của file. Hãy lưu file dạng UTF-8.")
        cells = _split_txt(text, self.delimiter)
        if self.has_header:
            return _names(cells)
        return [f"Cột {index + 1}" for index in range(max(1, len(cells)))]

    def iter_stream(self, start_offset: int, start_row_number: int):
        reader = ByteRecordReader(self.path, self.encoding, quote_aware=False)
        try:
            row_number = 1
            if start_offset > 0:
                reader.seek(start_offset)
                row_number = start_row_number
            else:
                try:
                    text, offset = next(reader)
                except StopIteration:
                    return
                if text is None:
                    raise ValueError("Không đọc được bảng mã của file. Hãy lưu file dạng UTF-8.")
                if self.has_header:
                    row_number = 2
                else:
                    yield _txt_record(1, text, offset, self.delimiter)
                    row_number = 2
            for text, offset in reader:
                if row_number < start_row_number:
                    row_number += 1
                    continue
                yield _txt_record(row_number, text, offset, self.delimiter)
                row_number += 1
        finally:
            reader.close()


def _split_txt(text: str, delimiter: str) -> list[str]:
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return text.split(delimiter)


def _names(cells: list[str]) -> list[str]:
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


def _txt_record(row_number: int, text: str | None, offset: int, delimiter: str) -> RawRecord:
    if text is None:
        return RawRecord(row_number, (), offset, "Không đọc được ký tự trong dòng")
    if text.strip() == "":
        return RawRecord(row_number, (), offset, "Dòng trống")
    return RawRecord(row_number, tuple(_split_txt(text, delimiter)), offset)
