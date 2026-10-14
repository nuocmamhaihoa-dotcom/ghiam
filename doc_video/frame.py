"""Đọc một khung hình: màn hình danh bạ (số + tên) hoặc màn hình hồ sơ (tên + @id).

Bước 1 đọc cả màn hình để biết dòng nào ở đâu. Nhận dạng theo nội dung,
không theo vị trí nút: dòng toàn chữ số là số điện thoại, tên là dòng ngay
dưới nó; dòng bắt đầu bằng @ là id, tên là dòng ngay trên nó.

Bước 2 cắt riêng từng dòng, phóng to, đọc lại bằng bộ đọc hợp với loại dữ
liệu: số và id bằng bộ tiếng Anh chỉ cho chữ không dấu, tên bằng cả bộ tiếng
Việt và bộ không dấu rồi ghép (xem text.merge_name).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

from doc_video import ocr
from doc_video.text import (
    clean_name,
    find_handle,
    is_name,
    looks_like_phone,
    merge_name,
    normalize_phone,
    vote,
)

WORK_WIDTH = 1200


@dataclass(frozen=True)
class Contact:
    phone: str
    name: str


@dataclass
class FrameRead:
    """kind: list (danh bạ), profile (hồ sơ) hoặc other. at: giây trong video, -1 với ảnh."""

    kind: str = "other"
    contacts: list[Contact] = field(default_factory=list)
    profile_name: str = ""
    handle: str = ""
    at: float = -1.0


def phone_screen(image: Image.Image) -> Image.Image:
    """Bỏ dải đen quanh màn hình điện thoại và phóng lên đủ lớn cho bộ đọc."""
    gray = ImageOps.exif_transpose(image).convert("L")
    pixels = np.asarray(gray)
    rows = np.flatnonzero(pixels.max(axis=1) > 40)
    cols = np.flatnonzero(pixels.max(axis=0) > 40)
    if rows.size and cols.size:
        box = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
        if box[2] - box[0] >= 0.4 * gray.width and box[3] - box[1] >= 0.4 * gray.height:
            gray = gray.crop(box)
    if gray.width < WORK_WIDTH:
        scale = WORK_WIDTH / gray.width
        gray = gray.resize((WORK_WIDTH, round(gray.height * scale)), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(gray)


def read_frame(image: Image.Image, at: float = -1.0) -> FrameRead:
    screen = phone_screen(image)
    lines = ocr.find_lines(screen)
    pairs = [pair for pair in _list_pairs(lines) if pair[1].y1 + pair[1].h < screen.height]
    if len(pairs) >= 2:
        return FrameRead("list", _read_contacts(screen, pairs), at=at)
    profile = _profile_lines(lines, screen.height)
    if profile is not None:
        name_line, handle_line = profile
        name, handle = _read_profile(screen, name_line, handle_line)
        if handle:
            return FrameRead("profile", profile_name=name, handle=handle, at=at)
    return FrameRead(at=at)


def _list_pairs(lines: list[ocr.TextLine]) -> list[tuple[ocr.TextLine, ocr.TextLine]]:
    """Mỗi dòng số đi với dòng chữ ngay bên dưới, thẳng mép trái."""
    pairs = []
    for index, line in enumerate(lines):
        if not looks_like_phone(line.text):
            continue
        for below in lines[index + 1 :]:
            if below.y0 > line.y1 + 1.8 * line.h:
                break
            if below.y0 < line.y1 - 0.3 * line.h:
                continue
            if abs(below.x0 - line.x0) > 1.5 * line.h:
                continue
            if looks_like_phone(below.text):
                break
            pairs.append((line, below))
            break
    return pairs


def _profile_lines(lines: list[ocr.TextLine], height: int) -> tuple[ocr.TextLine | None, ocr.TextLine] | None:
    """Dòng @id ở phần trên màn hình và dòng tên ngay trên nó.

    Ảnh đại diện cao đẩy @id xuống quá nửa màn hình. Lấy @id trên cùng còn
    trong vùng này, không lấy @id của tài khoản gợi ý ở phía dưới.
    """
    for index, line in enumerate(lines):
        if line.y0 < 0.05 * height or line.y0 > 0.62 * height or not find_handle(line.text):
            continue
        name_line = None
        for above in reversed(lines[:index]):
            if above.y1 < line.y0 - 6 * line.h:
                break
            if above.y1 > line.y0 + 0.3 * line.h:
                continue
            if abs(above.x0 - line.x0) > 4 * line.h:
                continue
            if is_name(above.text):
                name_line = above
                break
        return name_line, line
    return None


def _crop(screen: Image.Image, line: ocr.TextLine) -> Image.Image:
    """Cắt một dòng, phóng chữ lên khoảng 80 điểm ảnh, thêm lề trắng."""
    pad = max(6, line.h // 3)
    piece = screen.crop((max(0, line.x0 - pad), max(0, line.y0 - pad), min(screen.width, line.x1 + pad), min(screen.height, line.y1 + pad)))
    scale = max(1, min(4, round(80 / max(1, line.h))))
    if scale > 1:
        piece = piece.resize((piece.width * scale, piece.height * scale), Image.Resampling.LANCZOS)
    border = 20
    canvas = Image.new("L", (piece.width + 2 * border, piece.height + 2 * border), 255)
    canvas.paste(piece, (border, border))
    return canvas


def _read_names(crops: list[Image.Image]) -> list[str]:
    best_vie = ocr.read_texts(crops, "vie")
    fast_vie = ocr.read_texts(crops, "vie", best=False)
    plain = ocr.read_texts(crops, "eng", ocr.ASCII)
    return [merge_name([b, f], p) for b, f, p in zip(best_vie, fast_vie, plain)]


def _read_contacts(screen: Image.Image, pairs: list[tuple[ocr.TextLine, ocr.TextLine]]) -> list[Contact]:
    phone_crops = [_crop(screen, phone) for phone, _ in pairs]
    best_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS)
    fast_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS, best=False)
    names = _read_names([_crop(screen, name) for _, name in pairs])
    contacts = []
    for index, (phone_line, _) in enumerate(pairs):
        phone = vote([normalize_phone(best_digits[index]), normalize_phone(fast_digits[index]), normalize_phone(phone_line.text)])
        name = clean_name(names[index])
        if phone and is_name(name):
            contacts.append(Contact(phone, name))
    return contacts


def _read_profile(screen: Image.Image, name_line: ocr.TextLine | None, handle_line: ocr.TextLine) -> tuple[str, str]:
    crop = _crop(screen, handle_line)
    reads = ocr.read_texts([crop], "eng", ocr.ASCII) + ocr.read_texts([crop], "eng", ocr.ASCII, best=False)
    handle = vote([find_handle(text if "@" in text else "@" + text.strip()) for text in reads] + [find_handle(handle_line.text)])
    if not handle:
        return "", handle
    candidates: list[str] = []
    if name_line is not None:
        candidates.append(_read_names([_crop(screen, name_line)])[0])
        candidates.append(name_line.text)
    else:
        band = _band_above(screen, handle_line)
        if band is not None:
            candidates.append(_read_names([band])[0])
    for raw in candidates:
        name = clean_name(raw)
        if is_name(name):
            return name, handle
    return "", handle


def _band_above(screen: Image.Image, line: ocr.TextLine) -> Image.Image | None:
    """Dải chữ ngay trên @id khi lần dò dòng không tách được tên."""
    top = int(line.y0 - 2.2 * line.h)
    bottom = int(line.y0 - 0.15 * line.h)
    if bottom - top < 8:
        return None
    left = max(0, line.x0 - line.h)
    right = min(screen.width, max(line.x1, line.x0 + 6 * line.h))
    piece = screen.crop((left, max(0, top), right, min(screen.height, bottom)))
    if piece.width < 8 or piece.height < 8:
        return None
    scale = max(1, min(4, round(80 / piece.height)))
    if scale > 1:
        piece = piece.resize((piece.width * scale, piece.height * scale), Image.Resampling.LANCZOS)
    border = 20
    canvas = Image.new("L", (piece.width + 2 * border, piece.height + 2 * border), 255)
    canvas.paste(piece, (border, border))
    return canvas
