"""Đọc một khung hình TikTok: cắt đúng vùng chữ của từng dòng hoặc của hồ sơ.

Danh bạ nhận ra bằng cột nút Follow hồng bên phải. Mỗi nút là một dòng:
số ở trên, tên ngay dưới. Hồ sơ nhận ra bằng nút Follow rộng bên trái;
chỉ lấy tên lớn và dòng @ ngay dưới tên đó.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from control_plane.screen_table import (
    ContactHit,
    FrameObs,
    clean_name,
    clean_username,
    fold_marks,
    is_skipped,
    mark_count,
    name_key,
    normalize_phone,
    phone_in_text,
)


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


def tesseract_ready() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        done = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    return "vie" in (done.stdout + done.stderr)


def read_image(path: str | Path) -> FrameObs:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return read_pillow(image)


def read_pillow(image: Image.Image) -> FrameObs:
    if not tesseract_ready():
        raise RuntimeError("Cần cài tesseract-ocr và gói tiếng Việt tesseract-ocr-vie.")
    width, height = image.size
    buttons = _pink_boxes(image)
    listed = _list_buttons(buttons, width)
    if len(listed) >= 3:
        anchor_x, anchor_w = _column_anchor(listed)
        contacts = tuple(
            hit
            for button in listed
            if (hit := _read_row(image, button, anchor_x, anchor_w)) is not None
        )
        if contacts:
            return FrameObs("list", contacts)
        return FrameObs("unknown")
    profile = _profile_button(buttons, width, height)
    if profile is None:
        return FrameObs("unknown")
    name, username = _read_profile(image, profile)
    if not username:
        return FrameObs("unknown")
    return FrameObs("profile", (), name, username)


def _pink_boxes(image: Image.Image) -> list[Box]:
    array = np.asarray(image)
    red = array[:, :, 0].astype(np.int16)
    green = array[:, :, 1].astype(np.int16)
    blue = array[:, :, 2].astype(np.int16)
    mask = (red > 190) & (green < 110) & (blue < 150) & (red > green + 80) & (red > blue + 50)
    step = max(2, max(image.size) // 700)
    small = mask[::step, ::step]
    height, width = small.shape
    seen = np.zeros_like(small, dtype=bool)
    boxes: list[Box] = []
    for y in range(height):
        hits = np.flatnonzero(small[y] & ~seen[y])
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
                if cy > 0 and small[cy - 1, cx] and not seen[cy - 1, cx]:
                    seen[cy - 1, cx] = True
                    stack.append((cy - 1, cx))
                if cy + 1 < height and small[cy + 1, cx] and not seen[cy + 1, cx]:
                    seen[cy + 1, cx] = True
                    stack.append((cy + 1, cx))
                if cx > 0 and small[cy, cx - 1] and not seen[cy, cx - 1]:
                    seen[cy, cx - 1] = True
                    stack.append((cy, cx - 1))
                if cx + 1 < width and small[cy, cx + 1] and not seen[cy, cx + 1]:
                    seen[cy, cx + 1] = True
                    stack.append((cy, cx + 1))
            if count < 20:
                continue
            boxes.append(Box(min_x * step, min_y * step, (max_x + 1) * step, (max_y + 1) * step))
    return boxes


def _list_buttons(boxes: list[Box], width: int) -> list[Box]:
    candidates = []
    for box in boxes:
        if box.w <= 0 or box.h <= 0:
            continue
        aspect = box.w / box.h
        if not 2.15 <= aspect <= 3.7:
            continue
        if not 0.08 * width <= box.w <= 0.25 * width:
            continue
        if box.x0 <= 0.58 * width:
            continue
        candidates.append(box)
    if len(candidates) < 3:
        return []
    anchor = sorted(box.x0 for box in candidates)[len(candidates) // 2]
    column = [box for box in candidates if abs(box.x0 - anchor) <= max(24, int(0.04 * width))]
    column.sort(key=lambda box: box.y0)
    kept: list[Box] = []
    for box in column:
        if kept and box.y0 - kept[-1].y0 < box.h * 0.8:
            continue
        kept.append(box)
    return kept


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


def _read_row(image: Image.Image, button: Box, anchor_x: int, anchor_w: int) -> ContactHit | None:
    strip = Box(
        max(0, int(anchor_x - 2.72 * anchor_w)),
        max(0, button.y0 - int(0.12 * button.h)),
        max(0, anchor_x - 6),
        min(image.height, button.y1 + int(0.28 * button.h)),
    )
    if strip.w < 40 or strip.h < 20:
        return None
    words = _ocr_words(image, strip, "vie+eng", 6)
    words = [word for word in words if word.conf >= 20]
    phone, draft, name_box, phone_box = _phone_and_name(words)
    if not phone or not draft:
        return None
    reread = _ocr_phone(image, phone_box) if phone_box else ""
    if reread:
        phone = reread
    refined = _ocr_line(image, name_box) if name_box else ""
    name = _choose_name(draft, refined)
    if not name or is_skipped(name):
        return None
    return ContactHit(phone, name, _selected(image, strip))


def _read_profile(image: Image.Image, button: Box) -> tuple[str, str]:
    header = Box(
        max(0, button.x0 - int(0.08 * button.w)),
        max(0, button.y0 - int(3.9 * button.h)),
        min(image.width, button.x0 + int(1.7 * button.w)),
        max(0, button.y0 - int(0.12 * button.h)),
    )
    words = _ocr_words(image, header, "vie+eng", 6)
    words = [word for word in words if word.conf >= 20 and word.box.center_x() < button.x1 + 0.45 * button.w]
    lines = _lines(words)
    handle_at = -1
    username = ""
    for index, line in enumerate(lines):
        found = clean_username(_username_token(line.text))
        if found:
            handle_at = index
            username = found
            break
    if not username:
        return "", ""
    draft = ""
    name_box: Box | None = None
    for line in reversed(lines[:handle_at]):
        text = clean_name(line.text)
        if not text or is_skipped(text) or phone_in_text(text):
            continue
        draft = text
        name_box = line.box
        break
    refined = _ocr_line(image, name_box) if name_box else ""
    return _choose_name(draft, refined), username


def _phone_and_name(words: list[Word]) -> tuple[str, str, Box | None, Box | None]:
    lines = _lines(words)
    phone = ""
    phone_at = -1
    for index, line in enumerate(lines):
        found = phone_in_text(line.text)
        if found:
            phone = found
            phone_at = index
            break
    if not phone:
        return "", "", None, None
    phone_box = lines[phone_at].box
    for line in lines[phone_at + 1 :]:
        text = clean_name(line.text)
        if not text or is_skipped(text) or phone_in_text(text):
            continue
        return phone, text, line.box, phone_box
    same = clean_name(_without_phone(lines[phone_at].text, phone))
    if same and not is_skipped(same):
        return phone, same, lines[phone_at].box, phone_box
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
    draft = clean_name(draft)
    refined = clean_name(refined)
    if not refined or is_skipped(refined):
        return "" if is_skipped(draft) else draft
    if not draft:
        return refined
    if name_key(draft) == name_key(refined) or fold_marks(draft) == fold_marks(refined):
        return refined if mark_count(refined) >= mark_count(draft) else draft
    if refined.startswith(draft):
        extra = refined[len(draft) :].strip()
        if extra and extra.replace(" ", "").isdigit():
            return refined
    draft_tokens = draft.split()
    refined_tokens = refined.split()
    if len(refined_tokens) == len(draft_tokens) + 1 and len(refined_tokens[-1]) <= 1:
        return draft
    return draft


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
    background = [pixel for pixel in crop.getdata() if pixel >= 200]
    if len(background) < 40:
        return False
    background.sort()
    return background[len(background) // 2] <= 246


def _ocr_words(image: Image.Image, box: Box, lang: str, psm: int) -> list[Word]:
    crop = image.crop((box.x0, box.y0, box.x1, box.y1))
    text = _tesseract(crop, lang, psm, tsv=True)
    words: list[Word] = []
    for raw in text.splitlines()[1:]:
        parts = raw.split("\t")
        if len(parts) < 12:
            continue
        token = parts[11].strip()
        if not token:
            continue
        try:
            conf = float(parts[10])
            left, top, width, height = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        if conf < 0 or width <= 0 or height <= 0:
            continue
        words.append(Word(token, conf, Box(box.x0 + left, box.y0 + top, box.x0 + left + width, box.y0 + top + height)))
    return words


def _ocr_phone(image: Image.Image, box: Box) -> str:
    """Đọc lại riêng dòng số. Đọc chung với tên dễ nhầm 3 thành 5."""
    x0 = max(0, box.x0 - 6)
    y0 = max(0, box.y0 - 4)
    x1 = min(image.width, box.x1 + 6)
    y1 = min(image.height, box.y1 + 4)
    crop = image.crop((x0, y0, x1, y1))
    if crop.height < 48:
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (crop.width + 24, crop.height + 24), "white")
    canvas.paste(crop, (12, 12))
    return normalize_phone(_tesseract(canvas, "eng", 7, tsv=False, whitelist="0123456789"))


def _ocr_line(image: Image.Image, box: Box) -> str:
    x0 = max(0, box.x0 - 8)
    y0 = max(0, box.y0 - 6)
    x1 = min(image.width, box.x1 + 8)
    y1 = min(image.height, box.y1 + 6)
    crop = image.crop((x0, y0, x1, y1))
    if crop.height < 72:
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (crop.width + 24, crop.height + 24), "white")
    canvas.paste(crop, (12, 12))
    return _tesseract(canvas, "vie", 7, tsv=False)


def _tesseract(image: Image.Image, lang: str, psm: int, tsv: bool, whitelist: str = "") -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = handle.name
    try:
        image.save(path)
        command = ["tesseract", path, "stdout", "-l", lang, "--psm", str(psm)]
        if whitelist:
            command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        if tsv:
            command.append("tsv")
        done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=40)
    finally:
        Path(path).unlink(missing_ok=True)
    return done.stdout or ""
