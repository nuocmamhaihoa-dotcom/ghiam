"""Fast file fingerprint that does not read multi-gigabyte inputs fully."""

from __future__ import annotations

import hashlib
from pathlib import Path

_SAMPLE = 1024 * 1024


def file_fingerprint(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(_SAMPLE))
        if size > _SAMPLE:
            handle.seek(max(0, size - _SAMPLE))
            digest.update(handle.read(_SAMPLE))
    digest.update(str(size).encode("ascii"))
    return size, digest.hexdigest()


def estimate_text_rows(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            count += block.count(b"\n")
    if path.stat().st_size > 0:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                count += 1
    return count
