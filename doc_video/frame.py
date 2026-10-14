"""Đọc một khung hình: màn hình danh bạ (số + tên) hoặc màn hình hồ sơ (tên + @id).

Bước 1 đọc cả màn hình để biết dòng nào ở đâu. Nhận dạng theo nội dung,
không theo vị trí nút: dòng toàn chữ số là số điện thoại, tên là dòng ngay
dưới nó; dòng bắt đầu bằng @ là id, tên là dòng ngay trên nó.

Bước 2 cắt riêng từng dòng và đọc lại bằng nhiều bộ đọc, ở nhiều cỡ phóng,
rồi bỏ phiếu theo từng ký tự (text.vote_chars). Mỗi bộ đọc sai một kiểu:
tesseract tiếng Việt hay mất chữ (Hữu thành Hữ) và đọc chữ thành số (Ngô
thành Ng6); bộ chỉ chữ không dấu giữ đúng khoảng trắng và ký tự đặc biệt
nhưng làm hỏng dấu; VietOCR đọc dấu tốt nhất nhưng hay chèn khoảng trắng.
Bỏ phiếu giữ phần đúng của từng bộ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

from doc_video import ocr, viet
from doc_video.text import (
    clean_name,
    find_handle,
    fold,
    handle_from_read,
    handle_from_viet,
    is_name,
    looks_like_phone,
    normalize_phone,
    prefer_plain_digits_and_case,
    vote,
    vote_chars,
)

WORK_WIDTH = 1200
# Cỡ chữ (điểm ảnh) đưa vào từng bộ đọc. Đo trên ảnh danh bạ và hồ sơ dựng lại có đáp án:
# tesseract đọc tên tốt nhất khi chữ cao khoảng 32 điểm, VietOCR và bộ không dấu ở khoảng 80.
NAME_SMALL = 32
NAME_LARGE = 80
LOWER_HANDLE = "@._0123456789abcdefghijklmnopqrstuvwxyz"
VIE_DIGIT_WEIGHT = 0.6
LOOSE_HANDLE = re.compile(r"@[^A-Za-z0-9._]?[A-Za-z0-9._]{3,24}")


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
    height, width = pixels.shape
    middle = pixels[height // 5 : height * 4 // 5, width // 5 : width * 4 // 5]
    # Nền đen của giao diện tối trùng màu với dải đen, nên không cắt: cắt sẽ
    # xén sát dòng cuối và dòng đó bị coi là dòng cuộn dở ở mép dưới.
    dark = float(middle.mean()) < 100
    rows = np.flatnonzero(pixels.max(axis=1) > 40)
    cols = np.flatnonzero(pixels.max(axis=0) > 40)
    if rows.size and cols.size and not dark:
        box = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
        if box[2] - box[0] >= 0.4 * gray.width and box[3] - box[1] >= 0.4 * gray.height:
            gray = gray.crop(box)
    if gray.width < WORK_WIDTH:
        scale = WORK_WIDTH / gray.width
        gray = gray.resize((WORK_WIDTH, round(gray.height * scale)), Image.Resampling.LANCZOS)
    # Giao diện tối: đảo màu để mọi bước sau chỉ phải đọc chữ tối trên nền sáng.
    if dark:
        gray = ImageOps.invert(gray)
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


def _looks_like_handle_line(text: str) -> bool:
    """Lần dò dòng đôi khi chèn một ký tự lạ ngay sau @ (@†17pbgif6o)."""
    compact = "".join(str(text or "").split())
    return bool(find_handle(compact) or LOOSE_HANDLE.search(compact))


def _profile_lines(lines: list[ocr.TextLine], height: int) -> tuple[ocr.TextLine | None, ocr.TextLine] | None:
    """Dòng @id ở phần trên màn hình và dòng tên ngay trên nó.

    Ảnh đại diện cao đẩy @id xuống quá nửa màn hình. Lấy @id trên cùng còn
    trong vùng này, không lấy @id của tài khoản gợi ý ở phía dưới. Lần dò
    dòng không thấy dấu @ thì dựa vào hàng Đã follow / Follower / Thích: @id
    là dòng chữ ngay trên hàng số đếm.
    """
    for index, line in enumerate(lines):
        if line.y0 < 0.05 * height or line.y0 > 0.62 * height or not _looks_like_handle_line(line.text):
            continue
        return _name_above(lines, index), line
    index = _handle_above_stats(lines, height)
    if index is not None:
        return _name_above(lines, index), lines[index]
    return None


def _name_above(lines: list[ocr.TextLine], index: int) -> ocr.TextLine | None:
    line = lines[index]
    for above in reversed(lines[:index]):
        if above.y1 < line.y0 - 6 * line.h:
            break
        if above.y1 > line.y0 + 0.3 * line.h:
            continue
        if abs(above.x0 - line.x0) > 4 * line.h:
            continue
        if is_name(above.text):
            return above
    return None


def _handle_above_stats(lines: list[ocr.TextLine], height: int) -> int | None:
    """Vị trí dòng @id khi chỉ nhận ra được hàng Follower và Thích của màn hồ sơ."""
    labels = [line for line in lines if "follower" in fold(line.text).casefold() and line.y0 < 0.7 * height]
    for label in labels:
        row = [
            line
            for line in lines
            if abs(line.y0 - label.y0) < label.h and fold(line.text).casefold().strip() in ("thich", "da follow", "follower")
        ]
        if len(row) < 2:
            continue
        above = [index for index, line in enumerate(lines) if line.y1 <= label.y0 and line.y0 > label.y0 - 8 * label.h]
        for index in sorted(above, key=lambda i: -lines[i].y0):
            text = "".join(lines[index].text.split())
            if not text or text.isdigit() or abs(lines[index].x0 - label.x0) > 3 * label.h:
                continue
            if " " in lines[index].text.strip() or sum(ch.isalnum() for ch in text) < 3:
                return None
            return index
    return None


def _fit(piece: Image.Image, text_height: int, target: int) -> Image.Image:
    """Phóng để chữ cao khoảng target điểm ảnh, đổi về chữ tối trên nền sáng, thêm lề trắng."""
    scale = target / max(1, text_height)
    if abs(scale - 1) > 0.05:
        size = (max(1, round(piece.width * scale)), max(1, round(piece.height * scale)))
        piece = piece.resize(size, Image.Resampling.LANCZOS)
    # Giao diện tối: chữ sáng trên nền đen. Bộ đọc quen chữ tối trên nền sáng.
    if float(np.asarray(piece).mean()) < 110:
        piece = ImageOps.invert(piece)
    border = 20
    canvas = Image.new("L", (piece.width + 2 * border, piece.height + 2 * border), 255)
    canvas.paste(piece, (border, border))
    return canvas


def _crop(screen: Image.Image, line: ocr.TextLine, target: int = NAME_LARGE) -> Image.Image:
    pad = max(6, line.text_h // 3)
    piece = screen.crop((max(0, line.x0 - pad), max(0, line.y0 - pad), min(screen.width, line.x1 + pad), min(screen.height, line.y1 + pad)))
    return _fit(piece, line.text_h, target)


def _read_names(small: list[Image.Image], large: list[Image.Image]) -> list[str]:
    """Đọc tên bằng mọi bộ đọc rồi bỏ phiếu. small và large là cùng các dòng ở hai cỡ phóng."""
    best = ocr.read_texts(small, "vie")
    fast = ocr.read_texts(small, "vie", best=False)
    plain = ocr.read_texts(large, "eng", ocr.ASCII)
    if viet.available():
        extra, extra_weight = viet.read_lines(large), 1.5
    else:
        extra, extra_weight = ocr.read_texts(large, "vie"), 0.6
    names = []
    for index in range(len(small)):
        reads = [
            (clean_name(extra[index]), extra_weight),
            (clean_name(best[index]), 1.0),
            (clean_name(fast[index]), 1.0),
            (clean_name(plain[index]), 0.7),
        ]
        voted = clean_name(vote_chars(reads, loose=True))
        names.append(prefer_plain_digits_and_case(voted, clean_name(plain[index])))
    return names


def _read_contacts(screen: Image.Image, pairs: list[tuple[ocr.TextLine, ocr.TextLine]]) -> list[Contact]:
    phone_crops = [_crop(screen, phone) for phone, _ in pairs]
    best_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS)
    fast_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS, best=False)
    name_lines = [name for _, name in pairs]
    names = _read_names([_crop(screen, line, NAME_SMALL) for line in name_lines], [_crop(screen, line) for line in name_lines])
    contacts = []
    for index, (phone_line, _) in enumerate(pairs):
        phone = vote([normalize_phone(best_digits[index]), normalize_phone(fast_digits[index]), normalize_phone(phone_line.text)])
        name = clean_name(names[index])
        if phone and is_name(name):
            contacts.append(Contact(phone, name))
    return contacts


def _read_handle(screen: Image.Image, line: ocr.TextLine) -> str:
    """Đọc dòng @id ở nhiều cỡ, bằng nhiều bộ, rồi bỏ phiếu theo từng ký tự.

    Bộ tiếng Việt đọc chữ cái trong @id tốt, nhưng hay đọc 3 thành 5; chữ số
    của nó được tin ít hơn chữ số của bộ tiếng Anh.
    """
    reads: list[tuple[str, float, float]] = [(find_handle(line.text), 1.0, VIE_DIGIT_WEIGHT)]
    for target, lang, whitelist, best in (
        (28, "vie", "", False),
        (46, "vie", "", False),
        (60, "eng", LOWER_HANDLE, False),
        (60, "eng", ocr.ASCII, False),
        (80, "eng", ocr.ASCII, True),
    ):
        text = ocr.read_texts([_crop(screen, line, target)], lang, whitelist, best)[0]
        reads.append((handle_from_read(text), 1.0, VIE_DIGIT_WEIGHT if lang == "vie" else 1.0))
    if viet.available():
        reads.append((handle_from_viet(viet.read_lines([_crop(screen, line)])[0]), 1.0, 1.0))
    bodies = [(handle[1:], weight, digit_weight) for handle, weight, digit_weight in reads if handle]
    return find_handle("@" + vote_chars(bodies, loose=False)) if bodies else ""


def _read_profile(screen: Image.Image, name_line: ocr.TextLine | None, handle_line: ocr.TextLine) -> tuple[str, str]:
    handle = _read_handle(screen, handle_line)
    if not handle:
        return "", handle
    candidates: list[str] = []
    if name_line is not None:
        candidates.append(_read_names([_crop(screen, name_line, NAME_SMALL)], [_crop(screen, name_line)])[0])
        candidates.append(name_line.text)
    else:
        band = _band_above(screen, handle_line)
        if band is not None:
            piece, text_height = band
            candidates.append(_read_names([_fit(piece, text_height, NAME_SMALL)], [_fit(piece, text_height, NAME_LARGE)])[0])
    for raw in candidates:
        name = clean_name(raw)
        if is_name(name):
            return name, handle
    return "", handle


def _band_above(screen: Image.Image, line: ocr.TextLine) -> tuple[Image.Image, int] | None:
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
    return piece, piece.height
