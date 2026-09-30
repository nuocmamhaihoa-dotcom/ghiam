"""Byte-accurate line reader so a checkpoint can resume without rereading."""

from __future__ import annotations

from pathlib import Path


class ByteRecordReader:
    """Yield decoded records and the absolute offset after each record.

    CSV mode keeps newlines that sit inside quoted fields with the same record.
    TXT mode splits on every newline.
    """

    def __init__(self, path: Path, encoding: str, *, quote_aware: bool) -> None:
        self.path = path
        self.encoding = encoding
        self.quote_aware = quote_aware
        self._handle = path.open("rb")
        self._buffer = b""
        self._eof = False
        self._position = 0

    def close(self) -> None:
        self._handle.close()

    def seek(self, offset: int) -> None:
        self._handle.seek(offset)
        self._buffer = b""
        self._eof = False
        self._position = offset

    def __iter__(self) -> ByteRecordReader:
        return self

    def __next__(self) -> tuple[str | None, int]:
        record = self._next_record()
        if record is None:
            raise StopIteration
        return record

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._handle.read(1024 * 1024)
        if not chunk:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def _next_record(self) -> tuple[str | None, int] | None:
        in_quotes = False
        index = 0
        while True:
            while index < len(self._buffer):
                byte = self._buffer[index]
                if self.quote_aware and byte == 0x22:
                    in_quotes = not in_quotes
                elif byte == 0x0A and not in_quotes:
                    raw = self._buffer[:index]
                    consumed = index + 1
                    self._buffer = self._buffer[consumed:]
                    self._position += consumed
                    return self._decode(raw), self._position
                index += 1
            if not self._fill():
                if not self._buffer:
                    return None
                raw = self._buffer
                self._position += len(raw)
                self._buffer = b""
                return self._decode(raw), self._position

    def _decode(self, raw: bytes) -> str | None:
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            return raw.decode(self.encoding)
        except UnicodeDecodeError:
            return None
