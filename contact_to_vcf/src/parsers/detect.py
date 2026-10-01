"""Detect source format and build a streaming reader."""

from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from models.records import FileInspection, JobConfig
from parsers.csv_parser import CsvReader
from parsers.txt_parser import TxtReader
from parsers.xlsx_parser import XlsxReader
from utils.fingerprint import estimate_text_rows, file_fingerprint

Reader = CsvReader | TxtReader | XlsxReader
_XLSX_SUFFIXES = {".xlsx", ".xlsm", ".xltx"}
_PHONE_RUN = re.compile(r"\+?\d[\d\s.\-]{7,16}\d")


def detect_format(path: Path) -> str:
    """Classify a file. Excel workbooks stay workbooks; every other file is text."""
    if _looks_like_xlsx(path):
        return "xlsx"
    if path.suffix.lower() in {".csv", ".tsv"}:
        return "csv"
    return "txt"


def prepare_source(path: Path) -> Path:
    """Turn a non-table file into text the phone reader can scan."""
    if _looks_like_xlsx(path):
        return path
    if _looks_like_docx(path):
        return _write_text(path, _docx_plain(path))
    if _is_binary(path):
        return _write_text(path, _binary_phones(path))
    return path


def detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:8192]
    try:
        sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "cp1258"
    return "utf-8-sig"


def sniff_delimiter(path: Path, encoding: str) -> str:
    """Pick the delimiter that actually splits the sample into columns."""
    with path.open("rb") as handle:
        raw = handle.read(8192)
    try:
        sample = raw.decode(encoding)
    except UnicodeError:
        return ","
    counts = {delimiter: sample.count(delimiter) for delimiter in ",;\t|"}
    try:
        chosen = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        chosen = ""
    if chosen and counts.get(chosen, 0) > 0:
        return chosen
    best = max(counts, key=counts.get)
    if counts[best] > 0:
        return best
    return ","


def open_reader(config: JobConfig) -> Reader:
    if config.file_format == "csv":
        return CsvReader(
            config.input_path,
            config.delimiter,
            config.has_header,
            config.encoding,
        )
    if config.file_format == "txt":
        return TxtReader(
            config.input_path,
            config.delimiter,
            config.has_header,
            config.encoding,
        )
    if config.file_format == "xlsx":
        return XlsxReader(config.input_path, config.has_header)
    raise ValueError("Định dạng nguồn không được hỗ trợ")


def inspect_source(
    path: Path,
    *,
    file_format: str | None = None,
    delimiter: str | None = None,
    has_header: bool | None = None,
    encoding: str | None = None,
) -> FileInspection:
    resolved_format = file_format or detect_format(path)
    resolved_encoding = encoding or (
        "utf-8-sig" if resolved_format == "xlsx" else detect_encoding(path)
    )
    if resolved_format == "xlsx":
        resolved_delimiter = ""
        header = True if has_header is None else has_header
        reader: Reader = XlsxReader(path, header)
        estimated = _xlsx_estimate(path)
        if header and estimated:
            estimated = max(0, estimated - 1)
    else:
        header = (resolved_format == "csv") if has_header is None else has_header
        if delimiter in (None, "", "auto"):
            resolved_delimiter = sniff_delimiter(path, resolved_encoding)
        else:
            resolved_delimiter = delimiter
        if resolved_format == "csv":
            reader = CsvReader(path, resolved_delimiter, header, resolved_encoding)
        else:
            reader = TxtReader(path, resolved_delimiter, header, resolved_encoding)
        estimated = estimate_text_rows(path)
        if header and estimated:
            estimated = max(0, estimated - 1)
    columns = reader.column_names()
    samples: list[tuple[str, ...]] = []
    for record in reader.iter_stream(0, 1):
        if record.error:
            continue
        samples.append(record.columns)
        if len(samples) >= 5:
            break
    size, fingerprint = file_fingerprint(path)
    return FileInspection(
        path=path,
        file_format=resolved_format,
        encoding=resolved_encoding,
        delimiter=resolved_delimiter,
        has_header=header,
        columns=columns,
        samples=samples,
        size=size,
        fingerprint=fingerprint,
        estimated_rows=estimated,
    )


def _looks_like_xlsx(path: Path) -> bool:
    if path.suffix.lower() in _XLSX_SUFFIXES:
        return True
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return any(name.startswith("xl/") for name in archive.namelist())
    except zipfile.BadZipFile:
        return False


def _looks_like_docx(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _docx_plain(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(name))
            parts.append(" ".join(root.itertext()))
    return "\n".join(parts)


def _is_binary(path: Path) -> bool:
    sample = path.read_bytes()[:4096]
    return b"\x00" in sample


def _binary_phones(path: Path) -> str:
    raw = path.read_bytes()
    decoded = "\n".join(
        (
            raw.decode("utf-8", "ignore"),
            raw.decode("utf-16-le", "ignore"),
            raw.decode("cp1258", "ignore"),
        )
    )
    found: list[str] = []
    seen: set[str] = set()
    for match in _PHONE_RUN.findall(decoded):
        if match not in seen:
            seen.add(match)
            found.append(match)
    return "\n".join(found)


def _write_text(path: Path, text: str) -> Path:
    destination = path.with_name(path.name + ".phones.txt")
    destination.write_text(text, encoding="utf-8")
    return destination


def _xlsx_estimate(path: Path) -> int | None:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        max_row = sheet.max_row
    finally:
        workbook.close()
    if max_row is None:
        return None
    return int(max_row)
