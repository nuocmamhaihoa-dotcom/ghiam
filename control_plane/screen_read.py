"""Đọc một khung hình TikTok: cắt đúng vùng chữ của từng dòng hoặc của hồ sơ.

Danh bạ nhận ra bằng cột nút Follow hồng bên phải. Mỗi nút là một dòng:
số ở trên, tên ngay dưới. Hồ sơ nhận ra bằng nút Follow rộng bên trái;
chỉ lấy tên lớn và dòng @ ngay dưới tên đó.

Ưu tiên tesserocr (API Tesseract lâu dài trong mỗi worker) — cùng engine,
không spawn process mỗi lần. Fallback CLI nếu chưa cài tesserocr.
Số và username đọc bằng bộ chữ lớn (tessdata_best) nếu máy có.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from control_plane.ocr_backend import resolve_ocr_engine, system_tessdata, tesserocr_available
from control_plane.screen_layout import LayoutProfile
from control_plane.screen_table import (
    ContactHit,
    FrameObs,
    choose_name,
    choose_phone,
    clean_name,
    clean_username,
    is_skipped,
    phone_in_text,
)

DIGITS = "0123456789"
HANDLE_CHARS = "@._0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Nhiều tesseract chạy song song mà mỗi cái tự mở nhiều luồng thì giành nhân và gần như đứng.
_ONE_THREAD = {**os.environ, "OMP_THREAD_LIMIT": "1", "OMP_NUM_THREADS": "1"}
# API Tesseract lâu dài theo từng tiến trình worker: (lang, tessdata) -> PyTessBaseAPI
_TESS_APIS: dict[tuple[str, str], object] = {}


@dataclass(frozen=True)
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def shift(self, dx: int, dy: int) -> "Box":
        return Box(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)


@dataclass
class Word:
    text: str
    conf: float
    box: Box


@dataclass
class Line:
    words: list[Word]

    @property
    def text(self) -> str:
        ordered = sorted(self.words, key=lambda word: word.box.x0)
        return " ".join(word.text for word in ordered)

    @property
    def box(self) -> Box:
        return Box(
            min(word.box.x0 for word in self.words),
            min(word.box.y0 for word in self.words),
            max(word.box.x1 for word in self.words),
            max(word.box.y1 for word in self.words),
        )

    @property
    def center_y(self) -> float:
        return sum(word.box.center_y() for word in self.words) / len(self.words)


@dataclass
class _Pending:
    strip: Box
    phone: str
    draft: str
    phone_box: Box
    name_box: Box
    selected: bool


@functools.lru_cache(maxsize=1)
def tesseract_ready() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        done = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    return "vie" in (done.stdout + done.stderr)


@functools.lru_cache(maxsize=1)
def best_tessdata() -> str | None:
    raw = os.environ.get("CONTROL_TESSDATA_BEST", "").strip()
    folder = Path(raw) if raw else Path(__file__).resolve().parents[1] / "tessdata_best"
    if (folder / "eng.traineddata").is_file() and (folder / "vie.traineddata").is_file():
        return str(folder)
    return None


@functools.lru_cache(maxsize=1)
def _ocr_temp_root() -> str | None:
    """Ưu tiên /dev/shm để ghi ảnh OCR tạm — cùng pixel, I/O nhanh hơn đĩa."""
    override = os.environ.get("CONTROL_OCR_TMPDIR", "").strip()
    if override:
        path = Path(override)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return str(path)
    shm = Path("/dev/shm")
    try:
        if shm.is_dir() and shutil.disk_usage(shm).free >= 512 * 1024 * 1024:
            path = shm / "fb-poller-ocr"
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
    except OSError:
        return None
    return None


def read_image(path: str | Path, layout: dict[str, object] | LayoutProfile | None = None) -> FrameObs:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return read_pillow(image, layout=layout)


def read_pillow(
    image: Image.Image,
    layout: dict[str, object] | LayoutProfile | None = None,
) -> FrameObs:
    _require_tesseract()
    profile_layout = (
        layout
        if isinstance(layout, LayoutProfile)
        else LayoutProfile.from_dict(layout if isinstance(layout, dict) else None)
    )
    width, height = image.size
    buttons = _pink_boxes(image)
    listed = _list_buttons(buttons, width, layout=profile_layout)
    if len(listed) >= 3:
        anchor_x, anchor_w = _column_anchor(listed)
        strips = [_row_strip(image, button, anchor_x, anchor_w) for button in listed]
        tapped = _tapped_rows(image, listed, strips, anchor_x, anchor_w)
        contacts = tuple(_read_rows(image, strips, tapped))
        if contacts:
            return FrameObs("list", contacts)
        return FrameObs("unknown")
    profile = _profile_button(buttons, width, height)
    if profile is not None:
        name, username = _read_profile(image, profile)
        if username:
            return FrameObs("profile", (), name, username)
    # P2: không thấy / không đọc được nút Follow — vẫn săn @ ở vùng đầu trang hồ sơ.
    name, username = _hunt_profile_handle(image, layout=profile_layout)
    if username:
        return FrameObs("profile", (), name, username)
    return FrameObs("unknown")


def _hunt_profile_handle(
    image: Image.Image,
    layout: LayoutProfile | None = None,
) -> tuple[str, str]:
    """Đọc @username khi layout hồ sơ không nhận ra nút Follow hồng.

    Chỉ lấy vùng trên màn hình (header hồ sơ). Cần handle hợp lệ (≥3 ký tự sau @).
    """
    width, height = image.size
    if width < 80 or height < 80:
        return "", ""
    if layout is not None and layout.samples >= 0:
        x0, y0, x1, y1 = layout.header_box(width, height)
    else:
        x0, y0, x1, y1 = (
            max(0, int(0.04 * width)),
            max(0, int(0.06 * height)),
            min(width, int(0.82 * width)),
            min(height, int(0.48 * height)),
        )
    header = Box(x0, y0, x1, y1)
    if header.w < 40 or header.h < 40:
        return "", ""
    raw = image.crop((header.x0, header.y0, header.x1, header.y1))
    passes: list[tuple[Image.Image, int, int]] = [
        (raw, 6, 15),
        (_enhance(raw, 2), 7, 10),
    ]
    for crop, psm, min_conf in passes:
        words = _batch([crop], "vie+eng", psm)[0]
        scale_x = header.w / max(1, crop.width)
        scale_y = header.h / max(1, crop.height)
        shifted: list[Word] = []
        for word in words:
            if word.conf < min_conf:
                continue
            box = word.box
            mapped = Box(
                header.x0 + int(box.x0 * scale_x),
                header.y0 + int(box.y0 * scale_y),
                header.x0 + int(box.x1 * scale_x),
                header.y0 + int(box.y1 * scale_y),
            )
            shifted.append(Word(word.text, word.conf, mapped))
        lines = _lines(shifted)
        handle_at = -1
        username = ""
        for index, line in enumerate(lines):
            found = clean_username(_username_token(line.text))
            if found and len(found) >= 4:  # "@" + ≥3
                handle_at = index
                username = found
                break
        if handle_at < 0:
            continue
        reread = _read_handle(image, lines[handle_at].box)
        if reread and len(reread) >= 4:
            username = reread
        handle_y = lines[handle_at].box.center_y()
        draft = ""
        for line in reversed(lines[:handle_at]):
            if line.box.center_y() >= handle_y:
                continue
            text = clean_name(line.text)
            if not text or is_skipped(text) or phone_in_text(text):
                continue
            draft = text
            break
        return choose_name(draft), username
    return "", ""


def read_tap(paths: list[str | Path]) -> FrameObs:
    """Các khung ngay trước lúc màn hình chuyển. Chỉ đọc dòng có chấm chạm hoặc nền xám."""
    _require_tesseract()
    for path in reversed(paths):
        image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        listed = _list_buttons(_pink_boxes(image), image.width)
        if len(listed) < 3:
            continue
        anchor_x, anchor_w = _column_anchor(listed)
        strips = [_row_strip(image, button, anchor_x, anchor_w) for button in listed]
        tapped = _tapped_rows(image, listed, strips, anchor_x, anchor_w)
        if len(tapped) != 1:
            continue
        index = next(iter(tapped))
        hits = _read_rows(image, [strips[index]], {0})
        if hits:
            return FrameObs("tap", (hits[0],))
    return FrameObs("unknown")


def _require_tesseract() -> None:
    if not tesseract_ready():
        raise RuntimeError("Cần cài tesseract-ocr và gói tiếng Việt tesseract-ocr-vie.")


def _blobs(mask: np.ndarray, min_count: int) -> list[tuple[int, int, int, int, int]]:
    """Các vùng liền nhau trong mask: (x0, y0, x1, y1, số điểm)."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    found: list[tuple[int, int, int, int, int]] = []
    for y in range(height):
        hits = np.flatnonzero(mask[y] & ~seen[y])
        for x in hits:
            if seen[y, x]:
                continue
            stack = [(y, int(x))]
            seen[y, x] = True
            min_y = max_y = y
            min_x = max_x = int(x)
            count = 0
            while stack:
                cy, cx = stack.pop()
                count += 1
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                if cy > 0 and mask[cy - 1, cx] and not seen[cy - 1, cx]:
                    seen[cy - 1, cx] = True
                    stack.append((cy - 1, cx))
                if cy + 1 < height and mask[cy + 1, cx] and not seen[cy + 1, cx]:
                    seen[cy + 1, cx] = True
                    stack.append((cy + 1, cx))
                if cx > 0 and mask[cy, cx - 1] and not seen[cy, cx - 1]:
                    seen[cy, cx - 1] = True
                    stack.append((cy, cx - 1))
                if cx + 1 < width and mask[cy, cx + 1] and not seen[cy, cx + 1]:
                    seen[cy, cx + 1] = True
                    stack.append((cy, cx + 1))
            if count >= min_count:
                found.append((min_x, min_y, max_x + 1, max_y + 1, count))
    return found


def _pink_boxes(image: Image.Image) -> list[Box]:
    array = np.asarray(image)
    red = array[:, :, 0].astype(np.int16)
    green = array[:, :, 1].astype(np.int16)
    blue = array[:, :, 2].astype(np.int16)
    mask = (red > 190) & (green < 110) & (blue < 150) & (red > green + 80) & (red > blue + 50)
    step = max(2, max(image.size) // 700)
    return [
        Box(x0 * step, y0 * step, x1 * step, y1 * step)
        for x0, y0, x1, y1, _count in _blobs(mask[::step, ::step], 20)
    ]


def _list_buttons(
    boxes: list[Box],
    width: int,
    layout: LayoutProfile | None = None,
) -> list[Box]:
    candidates = []
    # B2/B3: nếu đã biết cột Follow của máy, nới mép trái chọn ứng viên quanh đó.
    min_x = 0.58 * width
    if layout is not None and layout.samples > 0:
        min_x = max(0.50 * width, (layout.follow_x0 - 0.08) * width)
    for box in boxes:
        if box.w <= 0 or box.h <= 0:
            continue
        aspect = box.w / box.h
        if not 2.15 <= aspect <= 3.7:
            continue
        if not 0.08 * width <= box.w <= 0.25 * width:
            continue
        if box.x0 <= min_x:
            continue
        candidates.append(box)
    if len(candidates) < 3:
        return []
    if layout is not None and layout.samples > 0:
        expect = layout.follow_x0 * width
        anchor = min(candidates, key=lambda box: abs(box.x0 - expect)).x0
    else:
        anchor = sorted(box.x0 for box in candidates)[len(candidates) // 2]
    column = [box for box in candidates if abs(box.x0 - anchor) <= max(24, int(0.04 * width))]
    column.sort(key=lambda box: box.y0)
    kept: list[Box] = []
    for box in column:
        if kept and box.y0 - kept[-1].y0 < box.h * 0.8:
            continue
        kept.append(box)
    return _fill_hidden_button(kept)


def _fill_hidden_button(buttons: list[Box]) -> list[Box]:
    """Chấm chạm che gần hết một nút Follow thì cột bị hụt một dòng. Thêm lại dòng đó ở đúng nhịp."""
    if len(buttons) < 3:
        return buttons
    gaps = sorted(b.y0 - a.y0 for a, b in zip(buttons, buttons[1:]))
    pitch = gaps[len(gaps) // 4]
    if pitch <= 0:
        return buttons
    x0 = sorted(button.x0 for button in buttons)[len(buttons) // 2]
    width = sorted(button.w for button in buttons)[len(buttons) // 2]
    height = sorted(button.h for button in buttons)[len(buttons) // 2]
    filled = [buttons[0]]
    for upper, lower in zip(buttons, buttons[1:]):
        gap = lower.y0 - upper.y0
        if 1.6 * pitch <= gap <= 2.4 * pitch:
            top = upper.y0 + gap // 2
            filled.append(Box(x0, top, x0 + width, top + height))
        filled.append(lower)
    return filled


def _profile_button(boxes: list[Box], width: int, height: int) -> Box | None:
    candidates = []
    for box in boxes:
        if box.w <= 0 or box.h <= 0:
            continue
        aspect = box.w / box.h
        if not 3.6 <= aspect <= 6.5:
            continue
        if box.w < 0.22 * width or box.x0 >= 0.40 * width:
            continue
        if box.y0 >= 0.55 * height:
            continue
        candidates.append(box)
    if not candidates:
        return None
    return max(candidates, key=lambda box: box.w)


def _column_anchor(buttons: list[Box]) -> tuple[int, int]:
    """Nút bị ngón tay che thì lệch trái. Lấy mép và bề rộng của cả cột."""
    left = sorted(button.x0 for button in buttons)[len(buttons) // 2]
    width = sorted(button.w for button in buttons)[len(buttons) // 2]
    return left, width


def _row_strip(image: Image.Image, button: Box, anchor_x: int, anchor_w: int) -> Box:
    return Box(
        max(0, int(anchor_x - 2.72 * anchor_w)),
        max(0, button.y0 - int(0.12 * button.h)),
        max(0, anchor_x - 6),
        min(image.height, button.y1 + int(0.28 * button.h)),
    )


def _touch_dots(image: Image.Image, button_h: float) -> list[tuple[float, float]]:
    """Chấm chạm: hình tròn xám đậm nửa trong suốt, đường kính khoảng 1,3 lần chiều cao nút Follow."""
    scale = max(1, image.width // 400)
    small = image.resize((image.width // scale, image.height // scale), Image.Resampling.BOX)
    array = np.asarray(small).astype(np.int32)
    red, green, blue = array[:, :, 0], array[:, :, 1], array[:, :, 2]
    lum = (299 * red + 587 * green + 114 * blue) // 1000
    spread = array.max(axis=2) - array.min(axis=2)
    over_light = (lum >= 35) & (lum <= 105) & (spread <= 24)
    over_button = (red >= 30) & (red <= 110) & (green <= 45) & (blue <= 55) & (red > green + 15)
    want = button_h / scale
    dots = []
    for x0, y0, x1, y1, count in _blobs(over_light | over_button, max(12, int(0.4 * want * want))):
        w = x1 - x0
        h = y1 - y0
        if not (0.95 * want <= w <= 1.6 * want and 0.95 * want <= h <= 1.6 * want):
            continue
        if not 0.75 <= w / h <= 1.33:
            continue
        if not 0.62 <= count / (w * h) <= 0.9:
            continue
        dots.append(((x0 + x1) / 2 * scale, (y0 + y1) / 2 * scale))
    return dots


def _tapped_rows(image: Image.Image, buttons: list[Box], strips: list[Box], anchor_x: int, anchor_w: int) -> set[int]:
    """Dòng đang bấm: có chấm chạm nằm trong dòng, hoặc nền xám. Hai dấu hiệu chỉ hai dòng khác nhau thì bỏ."""
    button_h = float(np.median([button.h for button in buttons]))
    centers = [button.center_y() for button in buttons]
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
    pitch = float(np.median(gaps)) if gaps else button_h * 2.5
    dot_rows: set[int] = set()
    for x, y in _touch_dots(image, button_h):
        for index, (center, strip) in enumerate(zip(centers, strips)):
            if abs(y - center) <= pitch / 2 and strip.x0 <= x <= anchor_x + anchor_w:
                dot_rows.add(index)
    grey_rows = {index for index, strip in enumerate(strips) if _selected(image, strip)}
    if len(dot_rows) == 1:
        if grey_rows and grey_rows != dot_rows:
            return set()
        return dot_rows
    if dot_rows:
        return set()
    return grey_rows


def _read_rows(image: Image.Image, strips: list[Box], tapped: set[int]) -> list[ContactHit]:
    usable = [(index, strip) for index, strip in enumerate(strips) if strip.w >= 40 and strip.h >= 20]
    layouts = _batch([image.crop((s.x0, s.y0, s.x1, s.y1)) for _, s in usable], "vie+eng", 6)
    pending: list[_Pending] = []
    for (index, strip), words in zip(usable, layouts):
        placed = [Word(word.text, word.conf, word.box.shift(strip.x0, strip.y0)) for word in words if word.conf >= 20]
        phone, draft, name_box, phone_box = _phone_and_name(placed)
        if not draft or name_box is None or phone_box is None:
            continue
        pending.append(_Pending(strip, phone, draft, phone_box, name_box, index in tapped))
    if not pending:
        return []

    best = best_tessdata()
    digit_crops: list[Image.Image] = []
    for row in pending:
        crop = _crop(image, row.phone_box, 6, 4)
        digit_crops.extend([_pad(_upscale(crop, 2)), _pad(_enhance(crop, 2))])
    digit_reads = _texts(_batch(digit_crops, "eng", 7, DIGITS, best))

    name_crops = [_pad(_upscale(_crop(image, row.name_box, 8, 6), 2)) for row in pending]
    fast_names = _texts(_batch(name_crops, "vie", 7))
    best_names = _texts(_batch(name_crops, "vie", 7, tessdata=best)) if best else [""] * len(pending)

    hits: list[ContactHit] = []
    for index, row in enumerate(pending):
        reads = digit_reads[2 * index : 2 * index + 2]
        phone = choose_phone(*reads, row.phone)
        name = choose_name(fast_names[index], best_names[index], row.draft)
        if not phone or not name or is_skipped(name):
            continue
        hits.append(ContactHit(phone, name, row.selected))
    return hits


def _read_profile(image: Image.Image, button: Box) -> tuple[str, str]:
    """Hồ sơ: tên nằm ngay phía trên @username.

    Hồ sơ mở rất ngắn thường hơi nhòe — thử thêm crop phóng to/làm nét và
    ngưỡng tin cậy thấp hơn trước khi bỏ qua khung.
    """
    header = Box(
        max(0, button.x0 - int(0.08 * button.w)),
        max(0, button.y0 - int(3.9 * button.h)),
        min(image.width, button.x0 + int(1.7 * button.w)),
        max(0, button.y0 - int(0.12 * button.h)),
    )
    if header.w < 20 or header.h < 20:
        return "", ""
    raw = image.crop((header.x0, header.y0, header.x1, header.y1))
    # Không dùng _pad ở đây để tỉ lệ tọa độ crop → ảnh gốc còn đúng.
    passes: list[tuple[Image.Image, int, int]] = [
        (raw, 6, 20),
        (_enhance(raw, 2), 7, 12),
        (_upscale(raw, 2), 6, 12),
    ]
    lines: list[Line] = []
    handle_at = -1
    username = ""
    for crop, psm, min_conf in passes:
        words = _batch([crop], "vie+eng", psm)[0]
        scale_x = header.w / max(1, crop.width)
        scale_y = header.h / max(1, crop.height)
        shifted = []
        for word in words:
            if word.conf < min_conf:
                continue
            box = word.box
            mapped = Box(
                header.x0 + int(box.x0 * scale_x),
                header.y0 + int(box.y0 * scale_y),
                header.x0 + int(box.x1 * scale_x),
                header.y0 + int(box.y1 * scale_y),
            )
            shifted.append(Word(word.text, word.conf, mapped))
        shifted = [word for word in shifted if word.box.center_x() < button.x1 + 0.45 * button.w]
        lines = _lines(shifted)
        handle_at = -1
        username = ""
        for index, line in enumerate(lines):
            found = clean_username(_username_token(line.text))
            if found:
                handle_at = index
                username = found
                break
        if handle_at >= 0:
            break
    if handle_at < 0:
        return "", ""
    reread = _read_handle(image, lines[handle_at].box)
    if reread:
        username = reread
    handle_y = lines[handle_at].box.center_y()
    draft = ""
    name_box: Box | None = None
    # Tên gần nhất phía trên username (không lấy chữ dưới @).
    for line in reversed(lines[:handle_at]):
        if line.box.center_y() >= handle_y:
            continue
        text = clean_name(line.text)
        if not text or is_skipped(text) or phone_in_text(text):
            continue
        draft = text
        name_box = line.box
        break
    if name_box is None:
        return choose_name(draft), username
    name_crop = [_pad(_upscale(_crop(image, name_box, 8, 6), 2))]
    fast = _texts(_batch(name_crop, "vie", 7))[0]
    best = best_tessdata()
    sharp = _texts(_batch(name_crop, "vie", 7, tessdata=best))[0] if best else ""
    return choose_name(fast, sharp, draft), username


def _read_handle(image: Image.Image, box: Box) -> str:
    """Username chỉ có chữ tiếng Anh và số. Đọc bằng tiếng Việt dễ biến 6 thành ó."""
    crop = [_pad(_upscale(_crop(image, box, 8, 6), 2))]
    text = _texts(_batch(crop, "eng", 7, HANDLE_CHARS, best_tessdata()))[0]
    compact = "".join(text.split())
    if compact and not compact.startswith("@"):
        compact = "@" + compact
    return clean_username(_username_token(compact) or compact)


def _phone_and_name(words: list[Word]) -> tuple[str, str, Box | None, Box | None]:
    """Danh bạ: số điện thoại ở trên, tên kèm theo nằm ngay phía dưới số đó."""
    lines = _lines(words)
    phone = ""
    phone_at = -1
    for index, line in enumerate(lines):
        found = phone_in_text(line.text)
        if found or sum(ch.isdigit() for ch in line.text) >= 9:
            phone = found
            phone_at = index
            break
    if phone_at < 0:
        return "", "", None, None
    phone_box = lines[phone_at].box
    phone_y = phone_box.center_y()
    # Chỉ lấy tên gần nhất phía dưới số — không lấy chữ phía trên hoặc cùng dòng.
    for line in lines[phone_at + 1 :]:
        if line.box.center_y() <= phone_y:
            continue
        text = clean_name(line.text)
        if not text or is_skipped(text) or phone_in_text(text):
            continue
        return phone, text, line.box, phone_box
    return phone, "", None, phone_box


def _without_phone(text: str, phone: str) -> str:
    digits = []
    kept = []
    for ch in text:
        if ch.isdigit():
            digits.append(ch)
            if "".join(digits) == phone:
                digits = []
            continue
        if digits and not phone.startswith("".join(digits)):
            kept.extend(digits)
            digits = []
        if not ch.isdigit():
            kept.append(ch)
    return "".join(kept)


def _username_token(text: str) -> str:
    compact = "".join(str(text or "").split())
    start = compact.find("@")
    if start < 0:
        return ""
    token = ["@"]
    for ch in compact[start + 1 :]:
        if ch.isascii() and (ch.isalnum() or ch in "._"):
            token.append(ch)
            continue
        break
    return "".join(token)


def _choose_name(draft: str, refined: str) -> str:
    return choose_name(refined, draft)


def _lines(words: list[Word]) -> list[Line]:
    ordered = sorted(words, key=lambda word: (word.box.center_y(), word.box.x0))
    lines: list[Line] = []
    for word in ordered:
        if lines and abs(word.box.center_y() - lines[-1].center_y) <= max(14, word.box.h * 0.7):
            lines[-1].words.append(word)
            continue
        lines.append(Line([word]))
    return lines


def _selected(image: Image.Image, strip: Box) -> bool:
    """Dòng đang chọn có nền xám khoảng 240, dòng thường là trắng 255."""
    top = strip.y0 + int(strip.h * 0.62)
    if top >= strip.y1:
        return False
    crop = image.crop((strip.x0, top, strip.x1, strip.y1)).convert("L")
    pixels = np.asarray(crop).reshape(-1)
    background = pixels[pixels >= 200]
    if background.size < 40:
        return False
    return int(np.median(background)) <= 246


def _crop(image: Image.Image, box: Box, pad_x: int, pad_y: int) -> Image.Image:
    return image.crop(
        (
            max(0, box.x0 - pad_x),
            max(0, box.y0 - pad_y),
            min(image.width, box.x1 + pad_x),
            min(image.height, box.y1 + pad_y),
        )
    )


def _upscale(crop: Image.Image, scale: int) -> Image.Image:
    if scale <= 1 or crop.width == 0 or crop.height == 0:
        return crop
    return crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)


def _enhance(crop: Image.Image, scale: int) -> Image.Image:
    gray = ImageOps.autocontrast(crop.convert("L")).filter(ImageFilter.SHARPEN)
    return _upscale(gray, scale).convert("RGB")


def _pad(crop: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (crop.width + 24, crop.height + 24), "white")
    canvas.paste(crop, (12, 12))
    return canvas


def _texts(pages: list[list[Word]]) -> list[str]:
    return [" ".join(word.text for word in words) for words in pages]


def _tessdata_path(tessdata: str | None) -> str | None:
    if tessdata:
        return tessdata
    return system_tessdata()


def _tesserocr_api(lang: str, tessdata: str | None):
    from tesserocr import OEM, PyTessBaseAPI

    folder = _tessdata_path(tessdata) or ""
    key = (lang, folder)
    api = _TESS_APIS.get(key)
    if api is not None:
        return api
    kwargs: dict[str, object] = {"lang": lang, "oem": OEM.LSTM_ONLY}
    if folder:
        kwargs["path"] = folder
    api = PyTessBaseAPI(**kwargs)
    _TESS_APIS[key] = api
    return api


def _batch_tesserocr(
    images: list[Image.Image],
    lang: str,
    psm: int,
    whitelist: str = "",
    tessdata: str | None = None,
) -> list[list[Word]]:
    """OCR bằng API lâu dài — không ghi file, không spawn process."""
    from tesserocr import RIL

    pages: list[list[Word]] = [[] for _ in images]
    if not images:
        return pages
    api = _tesserocr_api(lang, tessdata)
    api.SetPageSegMode(psm)
    # Xóa whitelist cũ khi không dùng — biến này dính trên API tái sử dụng.
    api.SetVariable("tessedit_char_whitelist", whitelist or "")
    level = RIL.WORD
    for index, image in enumerate(images):
        api.SetImage(image.convert("RGB"))
        try:
            api.Recognize()
        except RuntimeError:
            continue
        iterator = api.GetIterator()
        if iterator is None:
            continue
        while True:
            try:
                token = (iterator.GetUTF8Text(level) or "").strip()
            except RuntimeError:
                token = ""
            if token:
                try:
                    conf = float(iterator.Confidence(level))
                    left, top, right, bottom = iterator.BoundingBox(level)
                except (RuntimeError, TypeError, ValueError):
                    pass
                else:
                    if conf >= 0 and right > left and bottom > top:
                        pages[index].append(Word(token, conf, Box(left, top, right, bottom)))
            if not iterator.Next(level):
                break
    return pages


def _batch_cli(
    images: list[Image.Image],
    lang: str,
    psm: int,
    whitelist: str = "",
    tessdata: str | None = None,
) -> list[list[Word]]:
    """Fallback: gọi binary tesseract (chậm hơn vì spawn + ghi BMP)."""
    pages: list[list[Word]] = [[] for _ in images]
    if not images:
        return pages
    root = _ocr_temp_root()
    with tempfile.TemporaryDirectory(prefix="ocr-", dir=root) as folder:
        paths = []
        for index, image in enumerate(images):
            path = Path(folder) / f"{index:04d}.bmp"
            image.convert("RGB").save(path, format="BMP")
            paths.append(str(path))
        listing = Path(folder) / "list.txt"
        listing.write_text("\n".join(paths) + "\n", encoding="utf-8")
        command = [
            "tesseract",
            str(listing),
            "stdout",
            "-l",
            lang,
            "--oem",
            "1",
            "--psm",
            str(psm),
            "-c",
            "tessedit_create_tsv=1",
            "-c",
            "tessedit_create_txt=0",
        ]
        folder_tess = _tessdata_path(tessdata)
        if folder_tess:
            command.extend(["--tessdata-dir", folder_tess])
        if whitelist:
            command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180, env=_ONE_THREAD)
    for raw in done.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        token = parts[11].strip()
        if not token:
            continue
        try:
            page = int(parts[1]) - 1
            conf = float(parts[10])
            left, top, width, height = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        if not 0 <= page < len(pages) or conf < 0 or width <= 0 or height <= 0:
            continue
        pages[page].append(Word(token, conf, Box(left, top, left + width, top + height)))
    return pages


def _batch(
    images: list[Image.Image],
    lang: str,
    psm: int,
    whitelist: str = "",
    tessdata: str | None = None,
) -> list[list[Word]]:
    """OCR một loạt ảnh. Ưu tiên tesserocr; fallback CLI cùng oem/psm/whitelist."""
    engine = resolve_ocr_engine()
    if engine == "tesserocr" and tesserocr_available():
        try:
            return _batch_tesserocr(images, lang, psm, whitelist, tessdata)
        except Exception as exc:
            print(f"tesserocr lỗi, fallback CLI: {exc}", flush=True)
    return _batch_cli(images, lang, psm, whitelist, tessdata)
