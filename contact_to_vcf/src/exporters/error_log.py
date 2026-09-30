"""Append-only CSV log of rejected rows."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path


class ErrorLog:
    header = ["row_number", "name", "phone", "error_reason"]

    def __init__(self, path: Path, *, resume_bytes: int | None) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if resume_bytes is None or not path.exists():
            self._handle = path.open("wb")
            self._write(self.header, first=True)
        else:
            self._handle = path.open("r+b")
            bounded = min(resume_bytes, path.stat().st_size)
            self._handle.truncate(bounded)
            self._handle.seek(bounded)

    def write_rows(self, rows: list[tuple[int, str, str, str]]) -> None:
        for row_number, name, phone, reason in rows:
            self._write([str(row_number), name, phone, reason], first=False)

    def flush(self) -> int:
        self._handle.flush()
        os.fsync(self._handle.fileno())
        return self._handle.tell()

    def close(self) -> None:
        self._handle.close()

    def _write(self, cells: list[str], *, first: bool) -> None:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(cells)
        payload = buffer.getvalue()
        self._handle.write(payload.encode("utf-8-sig" if first else "utf-8"))
