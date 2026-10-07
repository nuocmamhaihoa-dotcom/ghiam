"""Đọc mục lục MP4 mà không gọi ffmpeg.

Video quay màn hình của iPhone thường để hộp moov ở cuối. Phần đã nhận chỉ đọc được
khi một hộp moov đủ nằm trong số byte liền từ đầu file.
"""

from __future__ import annotations

from pathlib import Path


def moov_ready(path: Path, limit: int) -> bool:
    """Đúng khi một hộp moov đủ nằm trong `limit` byte đầu."""
    if limit < 8:
        return False
    try:
        handle = path.open("rb")
    except OSError:
        return False
    with handle:
        offset = 0
        for _ in range(64):
            if offset + 8 > limit:
                return False
            try:
                handle.seek(offset)
                header = handle.read(8)
            except OSError:
                return False
            if len(header) < 8:
                return False
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            header_len = 8
            if size == 1:
                if offset + 16 > limit:
                    return False
                extra = handle.read(8)
                if len(extra) < 8:
                    return False
                size = int.from_bytes(extra, "big")
                header_len = 16
            elif size == 0:
                return False
            if size < header_len:
                return False
            end = offset + size
            if end > limit:
                return False
            if kind == b"moov":
                return True
            offset = end
        return False
