"""Đọc bốn dãy ký tự theo vị trí rồi ghép tên giống nhau thành một hàng ba ô.

Đây là hướng bổ sung. Hướng ghép cũ không đọc các ô này và không bị sửa.

Trên màn danh bạ, bên phải khung ảnh (cột chữ, sau ảnh đại diện, trước nút Follow):
số điện thoại ở trên, tên khách ngay dưới. Trên màn hồ sơ, bên trái khung ảnh:
tên khách và id bắt đầu bằng @. Hai tên cùng một khoá thì thành một hàng:
số điện thoại, tên khách, @id. Nhiều số hoặc nhiều @id cùng tên thì không đoán.
Số hoặc @id đã có trong bảng ghép sẵn thì giữ nguyên hàng đó.
"""

from __future__ import annotations

from doc_video.ocr import TextLine
from doc_video.text import (
    SCREEN_WORDS,
    clean_name,
    find_handle,
    fold,
    is_name,
    looks_like_phone,
)

# Cột chữ của danh bạ: sau ảnh đại diện, trước nút Follow sát mép phải.
RIGHT_START = 0.16
RIGHT_STOP = 0.80
# Tên và @id của hồ sơ nằm ở nửa trái khung ảnh.
LEFT_STOP = 0.60


def screen_title(lines: list[TextLine], height: int) -> TextLine | None:
    return next(
        (line for line in lines if line.y0 < 0.15 * height and "danh ba" in fold(line.text).casefold()),
        None,
    )


def _on_right(line: TextLine, width: int) -> bool:
    return int(RIGHT_START * width) <= line.x0 < int(RIGHT_STOP * width)


def _name_usable(phone: TextLine, name: TextLine) -> bool:
    folded = " ".join(fold(clean_name(name.text)).casefold().split())
    if folded in SCREEN_WORDS or looks_like_phone(name.text):
        return False
    if is_name(clean_name(name.text)):
        return True
    return name.x1 - name.x0 >= 2 * max(name.text_h, phone.text_h)


def select_right_pairs(
    pairs: list[tuple[TextLine, TextLine]], width: int, title: TextLine | None
) -> list[tuple[TextLine, TextLine]]:
    """Cặp số ở trên, tên ngay dưới, nằm ở cột phải của khung danh bạ.

    Dòng đè lên chữ "Danh bạ" vẫn bỏ: nửa trên chữ số không còn trên ảnh.
    """
    found: list[tuple[TextLine, TextLine]] = []
    for phone, name in pairs:
        if not _on_right(phone, width):
            continue
        if name.x0 < int(0.08 * width) or name.x0 >= int(RIGHT_STOP * width):
            continue
        if title is not None and phone.y0 < title.y1:
            continue
        if not _name_usable(phone, name):
            continue
        found.append((phone, name))
    return found


def _starts_with_at(text: str) -> bool:
    return "".join(str(text or "").split()).startswith("@")


def _merge_at_pieces(lines: list[TextLine]) -> list[TextLine]:
    """Gộp các mảnh cùng một dòng @id bị tách ngang. Dòng không có @ giữ nguyên."""
    out: list[TextLine] = []
    used: set[int] = set()
    for index, line in enumerate(lines):
        if index in used:
            continue
        compact = "".join(line.text.split())
        if "@" not in compact and "®" not in compact:
            out.append(line)
            continue
        pieces = [line]
        for other_index, other in enumerate(lines):
            if other_index == index or other_index in used:
                continue
            if other.h > 1.6 * max(line.h, 1):
                continue
            if abs((other.y0 + other.y1) - (line.y0 + line.y1)) > 1.2 * max(line.h, 1):
                continue
            if other.x0 < line.x0 - 20 or other.x1 > line.x0 + 480:
                continue
            pieces.append(other)
            used.add(other_index)
        used.add(index)
        if len(pieces) == 1:
            out.append(line)
            continue
        pieces.sort(key=lambda item: item.x0)
        out.append(
            TextLine(
                "".join(item.text.strip() for item in pieces),
                min(item.x0 for item in pieces),
                min(item.y0 for item in pieces),
                max(item.x1 for item in pieces),
                max(item.y1 for item in pieces),
                max(item.text_h for item in pieces),
            )
        )
    return out


def _name_above_left(lines: list[TextLine], handle: TextLine, width: int) -> TextLine | None:
    limit = int(LEFT_STOP * width)
    for above in reversed(lines):
        if above is handle:
            continue
        if above.y1 < handle.y0 - 6 * max(handle.h, 1):
            continue
        if above.y0 >= handle.y0 or above.y1 > handle.y0 + 0.3 * max(handle.h, 1):
            continue
        if above.x0 >= limit or above.x0 < 0:
            continue
        if abs(above.x0 - handle.x0) > 4 * max(handle.h, 1):
            continue
        if above.h < 0.7 * max(handle.h, 1):
            continue
        if looks_like_phone(above.text):
            continue
        folded = " ".join(fold(clean_name(above.text)).casefold().split())
        if folded in SCREEN_WORDS:
            continue
        return above
    return None


def select_left_profile(
    lines: list[TextLine], height: int, width: int
) -> tuple[TextLine | None, TextLine | None]:
    """Tên khách và dòng @id ở bên trái màn hồ sơ. @id phải bắt đầu bằng @.

    Lấy @id trên cùng còn trong phần trên màn hình. @id bên phải (tài khoản gợi ý
    hoặc nút) không dùng cho hướng này.
    """
    merged = _merge_at_pieces(lines)
    limit = int(LEFT_STOP * width)
    candidates = [
        line
        for line in merged
        if 0.05 * height <= line.y0 <= 0.62 * height and line.x0 < limit and _starts_with_at(line.text)
    ]
    if not candidates:
        return None, None
    handle = min(candidates, key=lambda line: (line.y0, line.x0))
    if not find_handle(handle.text) and not _starts_with_at(handle.text):
        return None, None
    return _name_above_left(merged, handle, width), handle
