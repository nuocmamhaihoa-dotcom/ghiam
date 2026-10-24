"""Đọc xlsx nhanh: quét byte bằng regex thay cho cây XML; chuỗi dùng chung nạp dần và nằm trên đĩa (mmap)."""

from __future__ import annotations

import mmap
import re
import tempfile
import zipfile
from array import array
from html import unescape
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from control_plane.customers import expand_scientific

READ_CHUNK = 1 << 22

_SHEET_PREFIX = re.compile(rb"<(\w+:)?sheetData\b")
_SST_PREFIX = re.compile(rb"<(\w+:)?sst\b")
_ATTR_R = re.compile(rb"""\br\s*=\s*["']\$?([A-Za-z]{1,3})""")
_ATTR_T = re.compile(rb"""\bt\s*=\s*["'](\w+)""")
_X_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")
_COLUMN_CACHE: dict[bytes, int] = {}


def _column_index(letters: bytes) -> int:
    cached = _COLUMN_CACHE.get(letters)
    if cached is not None:
        return cached
    number = 0
    for code in letters.upper():
        number = number * 26 + (code - 64)
    _COLUMN_CACHE[letters] = number - 1
    return number - 1


def decode_text(raw: bytes) -> str:
    """Byte trong XML thành chữ: bỏ &amp;…, giải _x000D_ của Excel."""
    text = raw.decode("utf-8", "replace")
    if "&" in text:
        text = unescape(text)
    if "_x" in text:
        text = _X_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)
    return text


def finish_cell(text: str) -> str:
    """Giống collapse + expand_scientific cũ, nhưng chỉ gọi regex khi ô bắt đầu bằng số."""
    value = " ".join(text.split())
    if value and not value.isdigit() and (value[0].isdigit() or value[0] in "+-"):
        return expand_scientific(value)
    return value


class _TextPatterns:
    def __init__(self, prefix: bytes) -> None:
        p = re.escape(prefix)
        self.t_open = b"<" + prefix + b"t>"
        self.t_close = b"</" + prefix + b"t>"
        self.t_attr = b"<" + prefix + b"t "
        self.r_mark = b"<" + prefix + b"r>"
        self.r_mark2 = b"<" + prefix + b"r "
        self.rph = b"<" + prefix + b"rPh"
        self.t_any = re.compile(rb"<" + p + rb"t\b[^>]*?(?:/>|>(.*?)</" + p + rb"t>)", re.S)
        self.rph_block = re.compile(rb"<" + p + rb"rPh\b.*?</" + p + rb"rPh>", re.S)

    def text_bytes(self, body: bytes) -> bytes:
        """Chữ trong <si> hoặc <is>: một <t>, hoặc nhiều <r><t>; bỏ phiên âm <rPh>."""
        if body.startswith(self.t_open) and body.endswith(self.t_close):
            inner = body[len(self.t_open) : -len(self.t_close)]
            if b"<" not in inner:
                return inner
        elif body.startswith(self.t_attr) and body.endswith(self.t_close):
            start = body.find(b">")
            if start > 0 and body[start - 1 : start] != b"/":
                inner = body[start + 1 : -len(self.t_close)]
                if b"<" not in inner:
                    return inner
        if self.rph in body:
            body = self.rph_block.sub(b"", body)
        return b"".join(match.group(1) or b"" for match in self.t_any.finditer(body))


class LazySharedStrings:
    """sharedStrings.xml đọc dần theo chỉ số cần dùng; chuỗi ghi ra file tạm và đọc lại qua mmap."""

    def __init__(self, archive: zipfile.ZipFile | None, tmp_dir: str | Path | None = None) -> None:
        self._stream: BinaryIO | None = None
        if archive is not None and "xl/sharedStrings.xml" in archive.namelist():
            self._stream = archive.open("xl/sharedStrings.xml")
        self._done = self._stream is None
        self._buf = b""
        self._si: re.Pattern[bytes] | None = None
        self._text: _TextPatterns | None = None
        self._offsets = array("Q", [0])
        self._end = 0
        self._tmp_dir = str(tmp_dir) if tmp_dir else None
        self._si_close = b"</si>"
        self._file: Any = None
        self._map: mmap.mmap | None = None
        self._mapped = 0

    def _setup(self, head: bytes) -> None:
        match = _SST_PREFIX.search(head)
        prefix = (match.group(1) or b"") if match else b""
        p = re.escape(prefix)
        self._si = re.compile(rb"<" + p + rb"si\b[^>]*?(?:/>|>(.*?)</" + p + rb"si>)", re.S)
        self._si_close = b"</" + prefix + b"si>"
        self._text = _TextPatterns(prefix)

    def _store(self, bodies: list[bytes]) -> None:
        assert self._text is not None
        out = bytearray()
        end = self._end
        offsets = self._offsets
        text_bytes = self._text.text_bytes
        for body in bodies:
            if body:
                raw = text_bytes(body)
                if b"&" in raw or b"_x" in raw:
                    raw = decode_text(raw).encode("utf-8")
                out += raw
                end += len(raw)
            offsets.append(end)
        if out:
            if self._file is None:
                self._file = tempfile.TemporaryFile(prefix="khach-sst-", dir=self._tmp_dir)
            self._file.write(out)
        self._end = end

    def _parse_more(self) -> None:
        if self._done or self._stream is None:
            self._done = True
            return
        chunk = self._stream.read(READ_CHUNK)
        if not chunk:
            if self._buf and self._si is not None:
                self._store(self._si.findall(self._buf))
            self._done = True
            self._stream.close()
            self._stream = None
            self._buf = b""
            return
        data = self._buf + chunk if self._buf else chunk
        if self._si is None:
            self._setup(data)
        assert self._si is not None
        cut = data.rfind(self._si_close)
        if cut < 0:
            self._buf = data
            return
        cut += len(self._si_close)
        self._buf = data[cut:]
        self._store(self._si.findall(data, 0, cut))

    def _ensure(self, index: int) -> bool:
        while index >= len(self._offsets) - 1 and not self._done:
            self._parse_more()
        return index < len(self._offsets) - 1

    def _remap(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        if self._map is not None:
            self._map.close()
        self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self._mapped = len(self._map)

    def __getitem__(self, index: int) -> str:
        offsets = self._offsets
        if index < 0:
            raise IndexError(index)
        if index + 1 >= len(offsets):
            if not self._ensure(index):
                raise IndexError(index)
            offsets = self._offsets
        start = offsets[index]
        stop = offsets[index + 1]
        if start == stop:
            return ""
        if stop > self._mapped:
            self._remap()
        assert self._map is not None
        return self._map[start:stop].decode("utf-8", "replace")

    def __len__(self) -> int:
        while not self._done:
            self._parse_more()
        return len(self._offsets) - 1

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        self._done = True


class _SheetPatterns:
    def __init__(self, prefix: bytes) -> None:
        p = re.escape(prefix)
        self.row = re.compile(rb"<" + p + rb"row\b[^>]*?(?:/>|>(.*?)</" + p + rb"row>)", re.S)
        self.cell = re.compile(rb"<" + p + rb"c\b([^>]*?)(?:/>|>(.*?)</" + p + rb"c>)", re.S)
        self.v_open = b"<" + prefix + b"v>"
        self.v_close = b"</" + prefix + b"v>"
        self.v_any = re.compile(rb"<" + p + rb"v\b[^>]*?(?:/>|>(.*?)</" + p + rb"v>)", re.S)
        self.is_open = b"<" + prefix + b"is>"
        self.is_block = re.compile(rb"<" + p + rb"is\b[^>]*>(.*?)</" + p + rb"is>", re.S)
        self.text = _TextPatterns(prefix)
        self.row_end = b"</" + prefix + b"row>"
        self.cell_mark = b"<" + prefix + b"c "
        self.cell_bare = b"<" + prefix + b"c>"
        self.common = re.compile(
            rb'<' + p + rb'c r="([A-Z]{1,3})\d+"(?: s="\d+")?(?: t="(s|n|str|b|e)")?(?: s="\d+")?'
            rb'(?:/>|><' + p + rb'v>([^<]*)</' + p + rb'v></' + p + rb'c>)'
        )


def _sheet_patterns(head: bytes) -> _SheetPatterns:
    match = _SHEET_PREFIX.search(head)
    return _SheetPatterns((match.group(1) or b"") if match else b"")


def _cell_value(body: bytes | None, patterns: _SheetPatterns) -> bytes:
    if not body:
        return b""
    start = body.find(patterns.v_open)
    if start >= 0:
        start += len(patterns.v_open)
        stop = body.find(patterns.v_close, start)
        return body[start:stop] if stop >= 0 else b""
    match = patterns.v_any.search(body)
    if match:
        return match.group(1) or b""
    return b""


def _parse_row_body(body: bytes | None, shared: Any, patterns: _SheetPatterns) -> list[str]:
    if not body:
        return []
    out: list[str] = []
    sequential = 0
    for match in patterns.cell.finditer(body):
        attrs, inner = match.groups()
        if attrs.startswith(b' r="'):
            letters = attrs[4 : attrs.find(b'"', 4)].rstrip(b"0123456789")
            column = _column_index(letters) if letters else sequential
        else:
            found = _ATTR_R.search(attrs)
            column = _column_index(found.group(1)) if found else sequential
        sequential = column + 1
        kind = b""
        if b"t=" in attrs:
            at = attrs.find(b' t="')
            if at >= 0:
                kind = attrs[at + 4 : attrs.find(b'"', at + 4)]
            else:
                kind_match = _ATTR_T.search(attrs)
                kind = kind_match.group(1) if kind_match else b""
        if kind == b"s":
            raw = _cell_value(inner, patterns)
            try:
                text = finish_cell(shared[int(raw)])
            except (ValueError, IndexError):
                text = ""
        elif kind == b"inlineStr":
            block = patterns.is_block.search(inner or b"")
            text = finish_cell(decode_text(patterns.text.text_bytes(block.group(1)))) if block else ""
        else:
            raw = _cell_value(inner, patterns)
            value = decode_text(raw) if raw else ""
            if kind in (b"b", b"str", b"e"):
                text = " ".join(value.split())
            else:
                text = finish_cell(value)
        size = len(out)
        if column == size:
            out.append(text)
        elif column > size:
            out.extend([""] * (column - size))
            out.append(text)
        else:
            out[column] = text
    return out


def _row_parser(shared: Any, patterns: _SheetPatterns) -> Callable[[bytes], list[str]]:
    """Đường nhanh cho ô kiểu Excel/Google thường gặp; dòng có ô lạ (công thức, chữ trong ô…) thì đọc đầy đủ."""
    common = patterns.common.findall
    cell_mark = patterns.cell_mark
    cell_bare = patterns.cell_bare
    columns = _COLUMN_CACHE
    finish = finish_cell

    def parse(body: bytes) -> list[str]:
        if not body:
            return []
        cells = common(body)
        if len(cells) != body.count(cell_mark) + body.count(cell_bare):
            return _parse_row_body(body, shared, patterns)
        out: list[str] = []
        for letters, kind, value in cells:
            column = columns.get(letters)
            if column is None:
                column = _column_index(letters)
            if not value:
                text = ""
            elif kind == b"s":
                try:
                    text = finish(shared[int(value)])
                except (ValueError, IndexError):
                    text = ""
            elif kind == b"str" or kind == b"b" or kind == b"e":
                text = " ".join(decode_text(value).split())
            else:
                text = finish(decode_text(value))
            size = len(out)
            if column == size:
                out.append(text)
            elif column > size:
                out.extend([""] * (column - size))
                out.append(text)
            else:
                out[column] = text
        return out

    return parse


def iter_sheet_rows(stream: BinaryIO, shared: Any) -> Iterator[list[str]]:
    """Từng <row> của sheet thành list chữ, giống ElementTree cũ nhưng nhanh hơn nhiều lần."""
    data = stream.read(READ_CHUNK)
    if not data:
        return
    patterns = _sheet_patterns(data)
    rows = patterns.row.findall
    close = patterns.row_end
    parse = _row_parser(shared, patterns)
    while True:
        chunk = stream.read(READ_CHUNK)
        if not chunk:
            for body in rows(data):
                yield parse(body)
            return
        cut = data.rfind(close)
        if cut < 0:
            data += chunk
            continue
        cut += len(close)
        for body in rows(data, 0, cut):
            yield parse(body)
        data = data[cut:] + chunk


def count_value_rows(stream: BinaryIO, progress: Callable[[int], None] | None = None) -> int:
    """Đếm dòng có ít nhất một ô có giá trị, không giải mã chuỗi — dùng cho số dòng xem trước."""
    data = stream.read(READ_CHUNK)
    if not data:
        return 0
    patterns = _sheet_patterns(data)
    end_tag = patterns.row_end
    v_open = patterns.v_open
    v_attr = patterns.v_open[:-1] + b" "
    is_open = patterns.is_open
    count = 0
    tail = b""
    while data:
        parts = (tail + data).split(end_tag) if tail else data.split(end_tag)
        tail = parts.pop()
        for part in parts:
            if v_open in part or is_open in part or v_attr in part:
                count += 1
        if progress is not None:
            progress(count)
        data = stream.read(READ_CHUNK)
    return count
