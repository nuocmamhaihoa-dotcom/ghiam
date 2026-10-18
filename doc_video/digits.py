"""Một phiếu chữ số nữa, đọc hình nét chứ không thay bộ đọc chữ.

Ảnh đưa vào là dòng số đã cắt: chữ tối trên nền sáng, chữ cao khoảng 80 điểm.
Tách các nét mực chồng lên nhau theo chiều ngang thành từng chữ số, rồi nhận
bằng số lỗ và mật độ nét. Chỉ trả về khi đủ mười chữ số và chữ nào cũng nhận
được. Còn lại trả về chuỗi rỗng, để phiếu này không đè lên hai lần đọc chữ đã
thống nhất.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# Ảnh lớn hơn mức một dòng số thì không phải dòng điện thoại.
_MAX_PIXELS = 2_000_000


def read_digit_line(image: Image.Image) -> str:
    """Mười chữ số trên một dòng, hoặc chuỗi rỗng khi không chắc."""
    try:
        gray = np.asarray(image.convert("L"))
    except (OSError, ValueError):
        return ""
    if gray.size == 0 or gray.size > _MAX_PIXELS:
        return ""
    if float(gray.mean()) < 110:
        gray = 255 - gray
    background = float(np.percentile(gray, 90))
    ink_level = float(np.percentile(gray, 10))
    if background - ink_level < 40:
        return ""
    ink = gray < (background + ink_level) / 2
    glyphs = _glyphs(ink)
    if len(glyphs) != 10:
        return ""
    digits = [_classify(glyph) for glyph in glyphs]
    if any(not digit for digit in digits):
        return ""
    return "".join(digits)


def _glyphs(ink: np.ndarray) -> list[np.ndarray]:
    """Các chữ đứng cạnh nhau. Nét của cùng một chữ chồng theo chiều ngang thì gộp lại."""
    rows = np.flatnonzero(ink.any(axis=1))
    if rows.size == 0:
        return []
    band = ink[int(rows[0]) : int(rows[-1]) + 1]
    height = band.shape[0]
    cols = band.any(axis=0)
    runs: list[tuple[int, int]] = []
    x = 0
    width = int(cols.size)
    while x < width:
        if not cols[x]:
            x += 1
            continue
        start = x
        while x < width and cols[x]:
            x += 1
        runs.append((start, x))
    gap_limit = max(2, int(0.08 * height))
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] < gap_limit:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    glyphs: list[np.ndarray] = []
    heights: list[int] = []
    for start, end in merged:
        piece = band[:, start:end]
        ys = np.flatnonzero(piece.any(axis=1))
        xs = np.flatnonzero(piece.any(axis=0))
        if ys.size == 0 or xs.size == 0:
            continue
        glyph = piece[int(ys[0]) : int(ys[-1]) + 1, int(xs[0]) : int(xs[-1]) + 1]
        glyphs.append(glyph)
        heights.append(glyph.shape[0])
    if not heights:
        return []
    main = max(heights)
    return [glyph for glyph in glyphs if glyph.shape[0] >= 0.55 * main and glyph.shape[1] >= 0.12 * main]


def _classify(glyph: np.ndarray) -> str:
    holes = _holes(glyph)
    if len(holes) >= 2:
        return "8"
    if len(holes) == 1:
        _area, center_y, share = holes[0]
        if center_y < 0.42:
            return "9"
        if center_y > 0.58:
            return "6"
        if share > 0.25:
            return "0"
        return "4"
    return _open_digit(glyph)


def _holes(glyph: np.ndarray) -> list[tuple[int, float, float]]:
    """Lỗ kín: (diện tích, tâm dọc, diện tích chia cho khung chữ). Bỏ lỗ quá nhỏ."""
    height, width = glyph.shape
    if height < 3 or width < 3:
        return []
    labels = _label(~glyph)
    found: list[tuple[int, float, float]] = []
    box = height * width
    for mark in range(1, int(labels.max()) + 1):
        ys, _xs = np.nonzero(labels == mark)
        if ys.size == 0:
            continue
        if int(ys.min()) == 0 or int(ys.max()) == height - 1:
            continue
        xs = np.flatnonzero((labels == mark).any(axis=0))
        if xs.size == 0 or int(xs.min()) == 0 or int(xs.max()) == width - 1:
            continue
        area = int(ys.size)
        share = area / box
        if share < 0.04:
            continue
        found.append((area, float(ys.mean()) / height, share))
    return found


def _label(mask: np.ndarray) -> np.ndarray:
    """Đánh số từng vùng liền nhau. Ảnh một chữ số nên vòng lặp này đủ nhanh."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parent = [0]

    def find(mark: int) -> int:
        while parent[mark] != mark:
            parent[mark] = parent[parent[mark]]
            mark = parent[mark]
        return mark

    current = 0
    for y in range(height):
        for x in range(width):
            if not mask[y, x]:
                continue
            left = int(labels[y, x - 1]) if x else 0
            up = int(labels[y - 1, x]) if y else 0
            if left and up:
                labels[y, x] = left
                left_root, up_root = find(left), find(up)
                if left_root != up_root:
                    parent[max(left_root, up_root)] = min(left_root, up_root)
            elif left or up:
                labels[y, x] = left or up
            else:
                current += 1
                parent.append(current)
                labels[y, x] = current
    remap: dict[int, int] = {}
    next_mark = 1
    out = np.zeros_like(labels)
    for y in range(height):
        for x in range(width):
            mark = int(labels[y, x])
            if not mark:
                continue
            root = find(mark)
            if root not in remap:
                remap[root] = next_mark
                next_mark += 1
            out[y, x] = remap[root]
    return out


def _open_digit(glyph: np.ndarray) -> str:
    """Chữ không có lỗ: 7, 5, 2, 3, hoặc 1 khi nét không lẫn được."""
    height, width = glyph.shape
    if height < 3 or width < 2:
        return ""
    aspect = width / height
    third, two_thirds = height // 3, 2 * height // 3
    mid_x = width // 2
    top_left = _density(glyph, 0, third, 0, mid_x)
    top_right = _density(glyph, 0, third, mid_x, width)
    mid_left = _density(glyph, third, two_thirds, 0, mid_x)
    mid_right = _density(glyph, third, two_thirds, mid_x, width)
    bottom_left = _density(glyph, two_thirds, height, 0, mid_x)
    bottom_right = _density(glyph, two_thirds, height, mid_x, width)
    mid_cross = _crossings(glyph[height // 2])
    bottom_cross = _crossings(glyph[min(height - 1, 5 * height // 6)])
    center_cross = _crossings(glyph[:, width // 2])
    if mid_left <= 0.12 and bottom_right <= 0.18 and top_right >= 0.50:
        return "7"
    if top_left >= 0.48 and top_right <= 0.35 and bottom_right >= 0.45 and (mid_cross >= 2 or bottom_cross >= 2):
        return "5"
    if mid_left <= 0.12 and bottom_left >= 0.55 and top_right >= 0.40 and bottom_right <= 0.42:
        return "2"
    # Số 1 của một số font chỉ là một nét đứng bên phải, hẹp hơn mọi chữ khác.
    if (
        aspect < 0.50
        and mid_left <= 0.08
        and bottom_left <= 0.08
        and mid_right >= 0.45
        and bottom_right >= 0.45
        and center_cross == 1
    ):
        return "1"
    if (
        0.08 <= mid_left <= 0.22
        and top_right >= 0.42
        and mid_right >= 0.45
        and bottom_right >= 0.45
        and 0.18 <= bottom_left <= 0.50
    ):
        return "3"
    if (
        0.50 <= aspect <= 0.72
        and mid_cross == 1
        and bottom_cross == 1
        and center_cross == 1
        and bottom_left >= 0.35
        and bottom_right >= 0.40
        and mid_left <= 0.25
        and mid_right <= 0.35
    ):
        return "1"
    return ""


def _density(glyph: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> float:
    piece = glyph[y0:y1, x0:x1]
    if piece.size == 0:
        return 0.0
    return float(piece.mean())


def _crossings(line: np.ndarray) -> int:
    count = 0
    on = False
    for value in line:
        if value and not on:
            count += 1
            on = True
        elif not value:
            on = False
    return count
