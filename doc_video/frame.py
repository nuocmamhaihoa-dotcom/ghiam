"""Đọc một khung hình: màn hình danh bạ (số + tên) hoặc màn hình hồ sơ (tên + @id).

Bước quét (scan_frame) đọc cả màn hình để biết dòng nào ở đâu, và đọc số
điện thoại. Nhận dạng theo nội dung, không theo vị trí nút: dòng toàn chữ số
là số điện thoại, tên là dòng ngay dưới nó; dòng bắt đầu bằng @ là id, tên là
dòng ngay trên nó. Vòng tròn chạm màn hình được tô thành màu nền, dòng bị nó
đè được đánh dấu là bị che.

Bước đọc kỹ (read_layout) cắt riêng từng dòng tên hoặc @id, đọc bằng nhiều
bộ đọc ở nhiều cỡ phóng, rồi bỏ phiếu theo từng ký tự (text.vote_chars). Mỗi
bộ đọc sai một kiểu: tesseract tiếng Việt hay mất chữ (Hữu thành Hữ) và đọc
chữ thành số (Ngô thành Ng6); bộ chỉ chữ không dấu giữ đúng khoảng trắng và
ký tự đặc biệt nhưng làm hỏng dấu; VietOCR đọc dấu tốt nhất nhưng hay chèn
khoảng trắng. Bỏ phiếu giữ phần đúng của từng bộ.

Video chỉ đọc kỹ vài khung rõ nhất cho mỗi người (xem video.read_inputs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

from doc_video import ocr, viet
from doc_video.digits import read_digit_line
from doc_video.text import (
    clean_name,
    find_handle,
    fix_underscores,
    fold,
    handle_from_read,
    handle_from_viet,
    is_name,
    looks_like_phone,
    normalize_phone,
    prefer_plain_digits_and_case,
    vote_chars,
    vote_digits,
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
    # Danh bạ: số điện thoại của dòng đang có vòng tròn chạm màn hình, tức dòng vừa được bấm.
    tapped: str = ""
    # Hồ sơ: các khung của một lượt mở @id hoà phiếu từng ký tự, chưa chọn một @id.
    handle_uncertain: bool = False


@dataclass(frozen=True)
class ListRow:
    """Một dòng danh bạ sau bước quét. clear: dòng số và dòng tên không bị vòng tròn chạm màn hình đè."""

    phone: str
    name_line: ocr.TextLine
    clear: bool
    top: int = 0
    # False khi tên chỉ gồm biểu tượng cảm xúc: không đọc, để tên trống thay vì chữ rác.
    readable: bool = True


@dataclass
class Layout:
    """Kết quả bước quét một khung. Bước đọc kỹ dùng lại đúng các ô dòng này."""

    kind: str = "other"
    rows: list[ListRow] = field(default_factory=list)
    name_line: ocr.TextLine | None = None
    handle_line: ocr.TextLine | None = None
    handle_hint: str = ""
    clear: bool = True
    name_readable: bool = True
    at: float = -1.0
    tapped: str = ""
    # Độ cao tâm vòng tròn chạm màn hình trên vùng danh bạ, -1 nếu không có hoặc có nhiều vòng.
    tap_y: float = -1.0
    row_pitch: float = 0.0


def _window_sum(mask: np.ndarray, radius: int) -> np.ndarray:
    """Số điểm bật trong ô vuông cạnh 2*radius+1 quanh mỗi điểm, tính bằng ảnh tích luỹ."""
    padded = np.pad(mask.astype(np.int32), radius)
    total = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.int64)
    total[1:, 1:] = padded.cumsum(axis=0).cumsum(axis=1)
    size = 2 * radius + 1
    return total[size:, size:] - total[:-size, size:] - total[size:, :-size] + total[:-size, :-size]


def touch_mask(gray: np.ndarray, chroma: np.ndarray | None = None) -> np.ndarray:
    """Vùng vòng tròn chạm màn hình: hình tròn xám đặc (khoảng 95 trên thang 255), không màu.

    Chữ đen đậm hơn nhiều, chữ xám nhạt hơn, nên chỉ một mảng xám đặc đủ to mới
    bị coi là vòng tròn. chroma (độ chênh giữa các kênh màu) loại nút Follow và
    thanh trạng thái màu đỏ, vốn có độ sáng xám gần bằng vòng tròn. Trả về vùng
    đã nới ra phủ cả viền mờ của vòng tròn.
    """
    width = gray.shape[1]
    core_radius = max(3, round(0.006 * width))
    # Mức xám của vòng tròn đổi theo máy quay: 95 trên ảnh mẫu, 114 trên video thật.
    solid = (gray > 62) & (gray < 140)
    if chroma is not None:
        solid &= chroma < 30
    core = _window_sum(solid, core_radius) >= (2 * core_radius + 1) ** 2
    if not core.any():
        return core
    return _window_sum(core, 3 * core_radius) > 0


def _prepare(image: Image.Image) -> tuple[Image.Image, np.ndarray, np.ndarray]:
    """Màn hình đã cắt, phóng, xoá vòng tròn chạm và biểu tượng cảm xúc, đảo màu nếu giao diện tối.

    Kèm vùng vòng tròn chạm và vùng quanh biểu tượng cảm xúc.
    """
    upright = ImageOps.exif_transpose(image)
    gray = upright.convert("L")
    channels = np.asarray(upright.convert("RGB"))
    chroma = Image.fromarray((channels.max(axis=2) - channels.min(axis=2)).astype(np.uint8))
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
            chroma = chroma.crop(box)
    if gray.width < WORK_WIDTH:
        scale = WORK_WIDTH / gray.width
        size = (WORK_WIDTH, round(gray.height * scale))
        gray = gray.resize(size, Image.Resampling.LANCZOS)
        chroma = chroma.resize(size, Image.Resampling.BILINEAR)
    pixels = np.asarray(gray).copy()
    colors = np.asarray(chroma)
    touched = touch_mask(pixels, colors)
    # Chữ trong tên và @id chỉ đen hoặc xám. Biểu tượng cảm xúc có màu và bị bộ đọc
    # đọc thành chữ rác (😴 thành 5), nên tô thành nền. Nút Follow đỏ và ảnh đại diện cũng mất, không sao.
    colorful = colors > 60
    near = np.zeros_like(colorful)
    if colorful.any():
        # Nới ra để phủ cả viền mờ quanh biểu tượng, vốn gần như không màu. Quanh đó,
        # nét nhạt còn sót cũng tô thành nền; nét chữ thật đậm hơn nhiều nên vẫn giữ.
        span = pixels.shape[1]
        near = _window_sum(colorful, max(4, round(0.012 * span))) > 0
        colorful = _window_sum(colorful, max(2, round(0.004 * span))) > 0
        if dark:
            pixels[colorful | (near & (pixels < 55))] = 0
        else:
            pixels[colorful | (near & (pixels > 200))] = 255
    if touched.any():
        if dark:
            pixels[touched] = 0
        else:
            # Vòng tròn trong suốt một phần: chữ nằm dưới vẫn tối hơn hẳn nền xám của vòng tròn.
            # Chỉ đổi phần xám (và viền mờ) thành nền trắng, giữ lại nét chữ.
            gray_part = touched & (pixels > 62) & (pixels < 140)
            level = float(np.median(pixels[gray_part])) if gray_part.any() else 110.0
            lift = touched & (pixels >= level - 30)
            pixels[lift] = 255
    gray = Image.fromarray(pixels)
    # Giao diện tối: đảo màu để mọi bước sau chỉ phải đọc chữ tối trên nền sáng.
    if dark:
        gray = ImageOps.invert(gray)
    return ImageOps.autocontrast(gray), touched, near


def phone_screen(image: Image.Image) -> Image.Image:
    """Bỏ dải đen quanh màn hình điện thoại, xoá vòng tròn chạm màn hình, phóng lên đủ lớn cho bộ đọc."""
    return _prepare(image)[0]


def _only_emoji(emoji: np.ndarray, line: ocr.TextLine | None) -> bool:
    """Dòng gần như nằm trọn trong vùng biểu tượng cảm xúc: tên chỉ gồm biểu tượng, không có chữ để đọc."""
    if line is None or not emoji.any():
        return False
    columns = emoji[line.y0 : line.y1, line.x0 : line.x1].any(axis=0)
    return bool(columns.size) and float(columns.mean()) >= 0.6


def _covered(touched: np.ndarray, *lines: ocr.TextLine) -> bool:
    for line in lines:
        margin = max(2, line.text_h // 4)
        box = touched[max(0, line.y0 - margin) : line.y1 + margin, max(0, line.x0 - margin) : line.x1 + margin]
        if box.size and box.any():
            return True
    return False


def _row_pitch(pairs: list[tuple[ocr.TextLine, ocr.TextLine]]) -> float:
    tops = sorted(phone_line.y0 for phone_line, _ in pairs)
    gaps = sorted(b - a for a, b in zip(tops, tops[1:]) if b > a)
    return float(gaps[len(gaps) // 2]) if gaps else 0.0


def _tap_center(touched: np.ndarray, pairs: list[tuple[ocr.TextLine, ocr.TextLine]], pitch: float) -> float:
    """Độ cao tâm vòng tròn chạm duy nhất nằm trong vùng các dòng danh bạ, -1 nếu không có hoặc có nhiều."""
    if not touched.any() or pitch <= 0:
        return -1.0
    low = min(phone_line.y0 for phone_line, _ in pairs) - pitch
    high = max(name_line.y1 for _, name_line in pairs) + pitch
    rows = np.flatnonzero(touched.any(axis=1))
    runs: list[tuple[int, int]] = []
    for y in rows:
        if runs and y <= runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], int(y))
        else:
            runs.append((int(y), int(y)))
    centers = [(a + b) / 2 for a, b in runs if low <= (a + b) / 2 <= high]
    return centers[0] if len(centers) == 1 else -1.0


def _highlighted_row(screen: Image.Image, rows: list[ListRow], pitch: float) -> str:
    """Dòng đang được bấm: TikTok tô nền dòng đó xám nhạt (khoảng 244 so với nền trắng 254).

    Đo độ sáng giữa của vùng giữa dòng (bỏ ảnh đại diện và nút Follow). Chỉ nhận
    khi đúng một dòng tối hơn hẳn các dòng còn lại.
    """
    if pitch <= 0 or len(rows) < 3:
        return ""
    pixels = np.asarray(screen)
    width = pixels.shape[1]
    left, right = int(0.18 * width), int(0.70 * width)
    levels = []
    for row in rows:
        top = max(0, int(row.top - 0.2 * pitch))
        bottom = min(pixels.shape[0], int(row.top + 0.7 * pitch))
        band = pixels[top:bottom, left:right]
        levels.append(float(np.median(band)) if band.size else 255.0)
    usual = float(np.median(levels))
    darker = [index for index, level in enumerate(levels) if level <= usual - 5]
    return rows[darker[0]].phone if len(darker) == 1 else ""


def row_at(rows: list[ListRow], y: float, pitch: float) -> str:
    """Số điện thoại của dòng chứa độ cao y (dòng chiếm từ trên dòng số một chút tới hết dòng tên)."""
    for row in rows:
        if row.top - 0.25 * pitch <= y < row.top + 0.75 * pitch:
            return row.phone
    return ""


def chosen_tap(rows: list[ListRow], highlighted: str, tap_y: float, pitch: float) -> str:
    """Dòng được bấm: dòng tô sáng, rồi tâm vòng tròn, rồi đúng một dòng bị ngón tay che.

    Nhiều dòng bị che thì không đoán. Không có tín hiệu thì trả về chuỗi rỗng.
    """
    if highlighted:
        return highlighted
    if tap_y >= 0:
        found = row_at(rows, tap_y, pitch)
        if found:
            return found
    covered = [row.phone for row in rows if row.phone and not row.clear]
    if len(covered) == 1:
        return covered[0]
    return ""


def _phones_without_name(
    lines: list[ocr.TextLine], pairs: list[tuple[ocr.TextLine, ocr.TextLine]], height: int
) -> list[tuple[ocr.TextLine, ocr.TextLine]]:
    """Dòng số mà tên chỉ gồm biểu tượng cảm xúc (đã xoá trước khi đọc), nên không có dòng tên bên dưới.

    Vẫn giữ số đó, kèm một ô tên trống ngay dưới dòng số, thẳng mép trái với các dòng số khác.
    """
    known = {id(phone_line) for phone_line, _ in pairs}
    left = sorted(phone_line.x0 for phone_line, _ in pairs)[len(pairs) // 2]
    extra = []
    for line in lines:
        if id(line) in known or not looks_like_phone(line.text):
            continue
        if abs(line.x0 - left) > 1.5 * line.text_h or line.y1 + 2.5 * line.h >= height:
            continue
        top = line.y1 + max(2, line.h // 3)
        blank = ocr.TextLine("", line.x0, top, line.x1, top + line.h, line.text_h)
        extra.append((line, blank))
    return extra


def scan_frame(image: Image.Image, at: float = -1.0) -> Layout:
    """Bước quét: loại màn hình, vị trí các dòng, số điện thoại. Không đọc kỹ tên và @id."""
    screen, touched, emoji = _prepare(image)
    lines = ocr.find_lines(screen)
    pairs = [
        pair
        for pair in _list_pairs(lines)
        if pair[1].y1 + pair[1].h < screen.height and is_name(clean_name(pair[1].text))
    ]
    if len(pairs) >= 2:
        pairs = sorted(pairs + _phones_without_name(lines, pairs, screen.height), key=lambda pair: pair[0].y0)
        # Dòng trên cùng trượt dưới thanh tiêu đề bị che nửa trên chữ số (3 thành 5). Bỏ dòng số
        # nằm sát dưới chữ "Danh bạ"; các khung khác thấy dòng đó đủ.
        title = next(
            (line for line in lines if line.y0 < 0.15 * screen.height and "danh ba" in fold(line.text).casefold()),
            None,
        )
        if title is not None:
            pairs = [pair for pair in pairs if pair[0].y0 >= title.y1 + 0.9 * title.h]
        phones = _read_phones(screen, pairs)
        rows = [
            ListRow(
                phone,
                name_line,
                not _covered(touched, phone_line, name_line),
                phone_line.y0,
                bool(name_line.text) and not _only_emoji(emoji, name_line),
            )
            for phone, (phone_line, name_line) in zip(phones, pairs)
            if phone
        ]
        pitch = _row_pitch(pairs)
        tap_y = _tap_center(touched, pairs, pitch)
        tapped = chosen_tap(rows, _highlighted_row(screen, rows, pitch), tap_y, pitch)
        return Layout("list", rows=rows, at=at, tapped=tapped, tap_y=tap_y, row_pitch=pitch)
    profile = _profile_lines(lines, screen.height)
    if profile is not None:
        name_line, handle_line = profile
        lines_to_check = [handle_line] + ([name_line] if name_line is not None else [])
        name_area = name_line or _band_line(screen, handle_line)
        return Layout(
            "profile",
            name_line=name_line,
            handle_line=handle_line,
            handle_hint=handle_from_read(handle_line.text),
            clear=not _covered(touched, *lines_to_check),
            name_readable=not _only_emoji(emoji, name_area),
            at=at,
        )
    return Layout(at=at)


def read_layout(image: Image.Image, layout: Layout, rows: list[int] | None = None) -> FrameRead:
    """Bước đọc kỹ trên đúng các ô dòng của bước quét. rows: chỉ đọc tên các dòng này (None là mọi dòng).

    Dòng không được chọn vẫn có mặt với số điện thoại, tên để trống.
    """
    if layout.kind == "list":
        chosen = [index for index in (range(len(layout.rows)) if rows is None else rows) if layout.rows[index].readable]
        names: dict[int, str] = {}
        if chosen:
            screen = phone_screen(image)
            lines = [layout.rows[index].name_line for index in chosen]
            read = _read_names([_crop(screen, line, NAME_SMALL) for line in lines], [_crop(screen, line) for line in lines])
            names = {index: name for index, name in zip(chosen, read) if is_name(name)}
        contacts = [Contact(row.phone, names.get(index, "")) for index, row in enumerate(layout.rows)]
        return FrameRead("list", contacts, at=layout.at, tapped=layout.tapped)
    if layout.kind == "profile" and layout.handle_line is not None:
        name, handle = _read_profile(phone_screen(image), layout.name_line, layout.handle_line, layout.name_readable)
        if handle:
            return FrameRead("profile", profile_name=name, handle=handle, at=layout.at)
    return FrameRead(at=layout.at)


def read_frame(image: Image.Image, at: float = -1.0) -> FrameRead:
    """Quét và đọc kỹ mọi dòng của một ảnh. Video dùng hai bước riêng để chỉ đọc kỹ vài khung."""
    layout = scan_frame(image, at)
    read = read_layout(image, layout)
    if read.kind == "list":
        read.contacts = [contact for contact in read.contacts if contact.name]
    return read


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
    candidates = [
        index
        for index, line in enumerate(lines)
        if 0.05 * height <= line.y0 <= 0.62 * height and _looks_like_handle_line(line.text)
    ]
    # Tên hồ sơ cũng có thể chứa @ (Hải@12345123456). Dòng @id thật bắt đầu bằng @.
    starts = [index for index in candidates if "@" in "".join(lines[index].text.split())[:2]]
    for index in starts or candidates:
        return _name_above(lines, index), lines[index]
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
    """Đọc tên bằng mọi bộ đọc rồi bỏ phiếu. small và large là cùng các dòng ở hai cỡ phóng.

    Hai lần tiếng Việt đầu đã ra cùng một tên thì dừng, không gọi bộ không dấu
    và VietOCR cho dòng đó. Khác dấu, dù chỉ một chữ, thì vẫn đọc tiếp.
    """
    best = ocr.read_texts(small, "vie")
    fast = ocr.read_texts(small, "vie", best=False)
    names = [""] * len(small)
    pending: list[int] = []
    for index in range(len(small)):
        agreed = clean_name(best[index])
        if agreed and agreed == clean_name(fast[index]) and is_name(agreed):
            names[index] = agreed
        else:
            pending.append(index)
    if not pending:
        return names
    pending_large = [large[index] for index in pending]
    plain = ocr.read_texts(pending_large, "eng", ocr.ASCII)
    if viet.available():
        extra, extra_weight = viet.read_lines(pending_large), 1.5
    else:
        extra, extra_weight = ocr.read_texts(pending_large, "vie"), 0.6
    for slot, index in enumerate(pending):
        reads = [
            (clean_name(extra[slot]), extra_weight),
            (clean_name(best[index]), 1.0),
            (clean_name(fast[index]), 1.0),
            (clean_name(plain[slot]), 0.7),
        ]
        voted = clean_name(vote_chars(reads, loose=True))
        names[index] = prefer_plain_digits_and_case(voted, clean_name(plain[slot]))
    return names


def _read_phones(screen: Image.Image, pairs: list[tuple[ocr.TextLine, ocr.TextLine]]) -> list[str]:
    phone_crops = [_crop(screen, phone) for phone, _ in pairs]
    best_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS)
    fast_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS, best=False)
    glyphs = [read_digit_line(crop) for crop in phone_crops]
    return [
        vote_digits(
            [
                normalize_phone(best_digits[index]),
                normalize_phone(fast_digits[index]),
                normalize_phone(phone_line.text),
                normalize_phone(glyphs[index]),
            ]
        )
        for index, (phone_line, _) in enumerate(pairs)
    ]


def _read_handle(screen: Image.Image, line: ocr.TextLine) -> str:
    """Đọc dòng @id ở nhiều cỡ, bằng nhiều bộ, rồi bỏ phiếu theo từng ký tự.

    Bộ tiếng Việt đọc chữ cái trong @id tốt, nhưng hay đọc 3 thành 5; chữ số
    của nó được tin ít hơn chữ số của bộ tiếng Anh. Lần quét và lần cắt 28 điểm
    đã ra cùng một @id không có chữ số thì dừng các cỡ còn lại. Có chữ số thì
    vẫn đọc bộ tiếng Anh, vì hai lần tiếng Việt có thể cùng nhầm 3 thành 5.
    Nét gạch dưới vẫn đo sau đó.
    """
    first = handle_from_read(line.text)
    second = handle_from_read(ocr.read_texts([_crop(screen, line, 28)], "vie", "", False)[0])
    if first and second and first == second and not any(ch.isdigit() for ch in first):
        return find_handle(fix_underscores(first, _underscore_strokes(screen, line, len(first))))
    reads: list[tuple[str, float, float]] = [
        (first, 1.0, VIE_DIGIT_WEIGHT),
        (second, 1.0, VIE_DIGIT_WEIGHT),
    ]
    for target, lang, whitelist, best in (
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
    handle = find_handle("@" + vote_chars(bodies, loose=False)) if bodies else ""
    if not handle:
        return ""
    return find_handle(fix_underscores(handle, _underscore_strokes(screen, line, len(handle))))


def _underscore_strokes(screen: Image.Image, line: ocr.TextLine, chars: int) -> list[tuple[float, float]]:
    """Các nét ngang nằm dưới chân chữ của dòng @id: (vị trí bắt đầu, chiều dài), đo bằng bề rộng một ký tự.

    Bộ đọc thấy hai gạch dưới liền nhau như một, và hay bỏ gạch dưới đứng ngay
    sau @. Trên ảnh, gạch dưới là nét ngang mảnh nằm dưới chân các chữ khác;
    chỉ chân chữ g, j, p, q, y cũng xuống tới đó nhưng ngắn hơn nhiều.
    """
    if chars < 2:
        return []
    pad = max(4, line.text_h // 4)
    box = (max(0, line.x0 - pad), max(0, line.y0 - pad), min(screen.width, line.x1 + pad), min(screen.height, line.y1 + pad))
    pixels = np.asarray(screen.crop(box)).astype(np.int16)
    if pixels.size == 0:
        return []
    background = float(np.percentile(pixels, 90))
    ink = float(np.percentile(pixels, 2))
    if background - ink < 40:
        return []
    dark = pixels < (background + ink) / 2
    rows = np.flatnonzero(dark.any(axis=1))
    cols = np.flatnonzero(dark.any(axis=0))
    if rows.size == 0:
        return []
    top, bottom, left, right = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    char_width = (right - left + 1) / chars
    lowest = [int(np.flatnonzero(dark[:, col])[-1]) for col in range(left, right + 1) if dark[:, col].any()]
    baseline = int(np.median(lowest))
    best: list[tuple[float, float]] = []
    for row_index in range(baseline + max(1, int(0.04 * (bottom - top + 1))), bottom + 1):
        row = dark[row_index, left : right + 1]
        strokes = []
        x = 0
        while x < row.size:
            if not row[x]:
                x += 1
                continue
            start = x
            while x < row.size and row[x]:
                x += 1
            if x - start >= 0.6 * char_width:
                strokes.append((start / char_width, (x - start) / char_width))
        if sum(length for _, length in strokes) > sum(length for _, length in best):
            best = strokes
    return best


def _read_profile(
    screen: Image.Image, name_line: ocr.TextLine | None, handle_line: ocr.TextLine, name_readable: bool = True
) -> tuple[str, str]:
    handle = _read_handle(screen, handle_line)
    if not handle or not name_readable:
        return "", handle
    candidates: list[str] = []
    if name_line is not None:
        candidates.append(_read_names([_crop(screen, name_line, NAME_SMALL)], [_crop(screen, name_line)])[0])
        candidates.append(name_line.text)
    else:
        band = _band_line(screen, handle_line)
        if band is not None:
            piece = screen.crop((band.x0, band.y0, band.x1, band.y1))
            candidates.append(_read_names([_fit(piece, band.h, NAME_SMALL)], [_fit(piece, band.h, NAME_LARGE)])[0])
    for raw in candidates:
        name = clean_name(raw)
        if is_name(name):
            return name, handle
    return "", handle


def _band_line(screen: Image.Image, line: ocr.TextLine) -> ocr.TextLine | None:
    """Ô dải chữ ngay trên @id, dùng khi lần dò dòng không tách được tên."""
    top = max(0, int(line.y0 - 2.2 * line.h))
    bottom = min(screen.height, int(line.y0 - 0.15 * line.h))
    left = max(0, line.x0 - line.h)
    right = min(screen.width, max(line.x1, line.x0 + 6 * line.h))
    if bottom - top < 8 or right - left < 8:
        return None
    return ocr.TextLine("", left, top, right, bottom)
