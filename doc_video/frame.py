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
from doc_video.side import screen_title, select_left_profile, select_right_pairs
from doc_video.text import (
    SCREEN_WORDS,
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
    # False với cặp bổ sung (số và tên ngay dưới số mà bước quét chính bỏ sót).
    # Có mặt để ghép theo tên, không chen vào thứ tự các dòng đã quét.
    in_order: bool = True


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
    # Hướng bổ sung, không đưa vào contacts: cặp cột phải (số, tên) và cặp bên trái (tên, @id).
    side_contacts: list[Contact] = field(default_factory=list)
    side_profile_name: str = ""
    side_handle: str = ""
    # Luồng đọc toàn bộ: tên và @id đọc kỹ trên các khung luồng chính chưa đọc.
    # Không đưa vào contacts, nên thứ tự mở hồ sơ và các hàng đã ghép không đổi.
    full_contacts: list[Contact] = field(default_factory=list)
    full_profile_name: str = ""
    full_handle: str = ""


@dataclass(frozen=True)
class ListRow:
    """Một dòng danh bạ sau bước quét. clear: dòng số và dòng tên không bị vòng tròn chạm màn hình đè."""

    phone: str
    name_line: ocr.TextLine
    clear: bool
    top: int = 0
    # False khi tên chỉ gồm biểu tượng cảm xúc: không đọc, để tên trống thay vì chữ rác.
    readable: bool = True
    # False với cặp bổ sung: không dùng cho thứ tự mở dòng và dấu chạm.
    in_order: bool = True


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
    # Ô chữ của hướng bổ sung. Không nằm trong rows, nên thứ tự dòng và dấu chạm không đổi.
    side_pairs: list[tuple[ocr.TextLine, ocr.TextLine]] = field(default_factory=list)
    side_name_line: ocr.TextLine | None = None
    side_handle_line: ocr.TextLine | None = None


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


def _touch_blobs(mask: np.ndarray) -> list[tuple[float, float, int, int, int]]:
    """Mỗi mảng liền của vòng tròn: tâm x, tâm y, diện tích, rộng, cao.

    Nút Back, ảnh đại diện và vòng chạm thật cùng là hình tròn xám. Tách từng
    mảng để bỏ những cái nằm ở cột trái, giữ vòng nằm trên dòng vừa bấm.
    """
    if not mask.any():
        return []
    padded = np.pad(mask.astype(np.uint8), ((0, 0), (1, 1)))
    diff = np.diff(padded.astype(np.int8), axis=1)
    ys, starts = np.nonzero(diff == 1)
    _, ends = np.nonzero(diff == -1)
    count = int(ys.size)
    if count == 0 or ends.size != count:
        return []
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    by_row: dict[int, list[int]] = {}
    for index, y in enumerate(ys.tolist()):
        by_row.setdefault(int(y), []).append(index)
    previous: list[int] = []
    previous_y = -2
    for y in sorted(by_row):
        current = by_row[y]
        if previous and y == previous_y + 1:
            probe = 0
            for index in current:
                while probe < len(previous) and ends[previous[probe]] <= starts[index]:
                    probe += 1
                overlap = probe
                while overlap < len(previous) and starts[previous[overlap]] < ends[index]:
                    union(index, previous[overlap])
                    overlap += 1
        previous = current
        previous_y = y

    boxes: dict[int, list[int]] = {}
    for index in range(count):
        root = find(index)
        y = int(ys[index])
        x0 = int(starts[index])
        x1 = int(ends[index])
        box = boxes.get(root)
        area = x1 - x0
        if box is None:
            boxes[root] = [x0, y, x1, y + 1, area, area * (x0 + x1) / 2, area * (y + 0.5)]
            continue
        box[0] = min(box[0], x0)
        box[1] = min(box[1], y)
        box[2] = max(box[2], x1)
        box[3] = max(box[3], y + 1)
        box[4] += area
        box[5] += area * (x0 + x1) / 2
        box[6] += area * (y + 0.5)
    blobs = []
    for x0, y0, x1, y1, area, moment_x, moment_y in boxes.values():
        if area <= 0:
            continue
        blobs.append((moment_x / area, moment_y / area, area, x1 - x0, y1 - y0))
    return blobs


def _tap_cluster(blobs: list[tuple[float, float, int]], width: int, pitch: float) -> float:
    """Độ cao một cụm vòng tròn. Vòng bị nút Follow cắn có thể tách thành hai mảnh kề nhau."""
    groups: list[list[tuple[float, float, int]]] = []
    for blob in blobs:
        placed = False
        for group in groups:
            if all(abs(blob[0] - other[0]) <= 0.18 * width and abs(blob[1] - other[1]) <= 0.55 * pitch for other in group):
                group.append(blob)
                placed = True
                break
        if not placed:
            groups.append([blob])
    if len(groups) != 1:
        return -1.0
    group = groups[0]
    weight = sum(blob[2] for blob in group)
    return sum(blob[1] * blob[2] for blob in group) / weight if weight else -1.0


def _tap_center(touched: np.ndarray, pairs: list[tuple[ocr.TextLine, ocr.TextLine]], pitch: float) -> float:
    """Độ cao vòng tròn chạm nằm trên danh bạ.

    Nút Back góc trên trái và ảnh đại diện xám cũng là hình tròn. Bỏ cột trái.
    Còn một vòng, hoặc một vòng bên cột Follow khi còn vòng khác ở giữa, thì
    lấy vòng đó. Nhiều vòng không tách được thì trả -1, để khỏi gán nhầm dòng.
    """
    if not touched.any() or pitch <= 0:
        return -1.0
    height, width = touched.shape
    low = min(phone_line.y0 for phone_line, _ in pairs) - pitch
    high = max(name_line.y1 for _, name_line in pairs) + pitch
    minimum = max(200, int(0.0015 * width * width))
    kept: list[tuple[float, float, int]] = []
    for cx, cy, area, wide, tall in _touch_blobs(touched):
        if not low <= cy <= high or area < minimum or wide < 1 or tall < 1:
            continue
        if wide > 3 * tall or tall > 3 * wide:
            continue
        if cx < 0.20 * width:
            continue
        if cy < 0.12 * height and cx < 0.40 * width:
            continue
        kept.append((cx, cy, area))
    if not kept:
        return -1.0
    single = _tap_cluster(kept, width, pitch)
    if single >= 0:
        return single
    right = [blob for blob in kept if blob[0] >= 0.60 * width]
    return _tap_cluster(right, width, pitch)


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
        if not row.in_order:
            continue
        if row.top - 0.25 * pitch <= y < row.top + 0.75 * pitch:
            return row.phone
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


def _title_margin(title: ocr.TextLine) -> float:
    return title.y1 + 0.9 * title.h


def _name_box_usable(phone_line: ocr.TextLine, name_line: ocr.TextLine) -> bool:
    """Ô ngay dưới số có thể là tên khách: không phải nút trên màn hình, và đủ rộng để chứa tên."""
    folded = " ".join(fold(clean_name(name_line.text)).casefold().split())
    if folded in SCREEN_WORDS or looks_like_phone(name_line.text):
        return False
    if is_name(clean_name(name_line.text)):
        return True
    return name_line.x1 - name_line.x0 >= 2 * max(name_line.text_h, phone_line.text_h)


def _supplement_pairs(
    kept: list[tuple[ocr.TextLine, ocr.TextLine]],
    geometric: list[tuple[ocr.TextLine, ocr.TextLine]],
    accepted: set[str],
    title: ocr.TextLine | None,
) -> list[tuple[ocr.TextLine, ocr.TextLine, str]]:
    """Cặp số và tên ngay bên dưới mà bước quét chính đã bỏ.

    Giữ lại khi tên đọc thô chưa thành tên (ô vẫn đủ rộng, bước đọc kỹ sẽ đọc lại),
    hoặc khi dòng số nằm sát dưới tiêu đề nhưng chính dòng đó đã là đủ 10 số.
    Số trả về khác rỗng thì dùng đúng số đó, không đọc lại ô dễ bị cắt nửa chữ.
    Dòng đè lên chữ "Danh bạ" vẫn bỏ: nửa trên chữ số không còn trên ảnh.
    """
    kept_ids = {id(phone_line) for phone_line, _ in kept}
    margin = _title_margin(title) if title is not None else None
    found: list[tuple[ocr.TextLine, ocr.TextLine, str]] = []
    seen = set(accepted)
    for phone_line, name_line in geometric:
        if id(phone_line) in kept_ids or not _name_box_usable(phone_line, name_line):
            continue
        if title is not None and phone_line.y0 < title.y1:
            continue
        under_title = margin is not None and phone_line.y0 < margin
        line_phone = normalize_phone(phone_line.text)
        if under_title:
            if not line_phone or line_phone in seen:
                continue
            seen.add(line_phone)
            found.append((phone_line, name_line, line_phone))
            continue
        if is_name(clean_name(name_line.text)):
            continue
        if line_phone and line_phone in seen:
            continue
        if line_phone:
            seen.add(line_phone)
        found.append((phone_line, name_line, ""))
    return found


def _supplement_rows(
    screen: Image.Image,
    touched: np.ndarray,
    emoji: np.ndarray,
    kept: list[tuple[ocr.TextLine, ocr.TextLine]],
    geometric: list[tuple[ocr.TextLine, ocr.TextLine]],
    rows: list[ListRow],
    title: ocr.TextLine | None,
) -> list[ListRow]:
    """Đọc các cặp bổ sung. Số đã chắc trên dòng thì không đọc lại."""
    accepted = {row.phone for row in rows}
    extra = _supplement_pairs(kept, geometric, accepted, title)
    if not extra:
        return []
    pending = [(phone_line, name_line) for phone_line, name_line, forced in extra if not forced]
    read = _read_phones(screen, pending) if pending else []
    read_at = 0
    added: list[ListRow] = []
    for phone_line, name_line, forced in extra:
        if forced:
            phone = forced
        else:
            phone = read[read_at] or normalize_phone(phone_line.text)
            read_at += 1
        if not phone or phone in accepted:
            continue
        accepted.add(phone)
        added.append(
            ListRow(
                phone,
                name_line,
                not _covered(touched, phone_line, name_line),
                phone_line.y0,
                bool(name_line.text) and not _only_emoji(emoji, name_line),
                in_order=False,
            )
        )
    return added


def scan_frame(image: Image.Image, at: float = -1.0) -> Layout:
    """Bước quét: loại màn hình, vị trí các dòng, số điện thoại. Không đọc kỹ tên và @id."""
    screen, touched, emoji = _prepare(image)
    lines = ocr.find_lines(screen)
    geometric = [
        pair for pair in _list_pairs(lines, screen) if pair[1].y1 + pair[1].h < screen.height
    ]
    named = [pair for pair in geometric if is_name(clean_name(pair[1].text))]
    if len(named) >= 2:
        pairs = sorted(named + _phones_without_name(lines, named, screen.height), key=lambda pair: pair[0].y0)
        # Dòng trên cùng trượt dưới thanh tiêu đề bị che nửa trên chữ số (3 thành 5). Bỏ dòng số
        # nằm sát dưới chữ "Danh bạ"; các khung khác thấy dòng đó đủ.
        title = next(
            (line for line in lines if line.y0 < 0.15 * screen.height and "danh ba" in fold(line.text).casefold()),
            None,
        )
        if title is not None:
            pairs = [pair for pair in pairs if pair[0].y0 >= _title_margin(title)]
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
        if pairs:
            pitch = _row_pitch(pairs)
            tap_y = _tap_center(touched, pairs, pitch)
            tapped = _highlighted_row(screen, rows, pitch) or (row_at(rows, tap_y, pitch) if tap_y >= 0 else "")
        else:
            pitch = 0.0
            tap_y = -1.0
            tapped = ""
        rows.extend(_supplement_rows(screen, touched, emoji, pairs, geometric, rows, title))
        rows.sort(key=lambda row: row.top)
        layout = Layout("list", rows=rows, at=at, tapped=tapped, tap_y=tap_y, row_pitch=pitch)
        _attach_side(layout, lines, screen, geometric)
        return layout
    profile = _profile_lines(lines, screen.height)
    if profile is not None:
        name_line, handle_line = profile
        lines_to_check = [handle_line] + ([name_line] if name_line is not None else [])
        name_area = name_line or _band_line(screen, handle_line)
        layout = Layout(
            "profile",
            name_line=name_line,
            handle_line=handle_line,
            handle_hint=handle_from_read(handle_line.text),
            clear=not _covered(touched, *lines_to_check),
            name_readable=not _only_emoji(emoji, name_area),
            at=at,
        )
        _attach_side(layout, lines, screen, geometric)
        return layout
    layout = Layout(at=at)
    _attach_side(layout, lines, screen, geometric)
    return layout


def _attach_side(
    layout: Layout,
    lines: list[ocr.TextLine],
    screen: Image.Image,
    geometric: list[tuple[ocr.TextLine, ocr.TextLine]],
) -> None:
    """Gắn ô bốn dãy ký tự. Không thêm vào rows."""
    title = screen_title(lines, screen.height)
    if layout.kind == "list":
        layout.side_pairs = select_right_pairs(geometric, screen.width, title)
        return
    if layout.kind == "profile":
        layout.side_name_line, layout.side_handle_line = select_left_profile(lines, screen.height, screen.width)
        return
    right = select_right_pairs(geometric, screen.width, title)
    # Màn chưa đủ hai tên để thành danh bạ: chỉ nhận khi có từ hai cặp cột phải, kẻo một số
    # tình cờ trên màn khác bị kéo vào.
    if len(right) >= 2:
        layout.side_pairs = right
    layout.side_name_line, layout.side_handle_line = select_left_profile(lines, screen.height, screen.width)


def _same_box(left: ocr.TextLine, right: ocr.TextLine) -> bool:
    return abs(left.x0 - right.x0) <= 4 and abs(left.y0 - right.y0) <= 4 and abs(left.x1 - right.x1) <= 8


def fill_side(image: Image.Image, layout: Layout, frame: FrameRead) -> None:
    """Đọc kỹ bốn dãy ký tự vào frame.side_*. Không đổi contacts, tên hồ sơ hay @id của hướng cũ.

    Cặp đã có tên ở lần đọc kỹ thì dùng lại tên đó. Cặp cột phải mà hướng cũ bỏ sót thì đọc lại ô.
    """
    screen = phone_screen(image)
    known = {contact.phone: contact.name for contact in frame.contacts}
    side: list[Contact] = []
    seen: set[str] = set()
    pending: list[tuple[ocr.TextLine, ocr.TextLine, str]] = []
    for phone_line, name_line in layout.side_pairs:
        primary = next((row.phone for row in layout.rows if row.top == phone_line.y0), "")
        if primary and known.get(primary):
            if primary not in seen:
                seen.add(primary)
                side.append(Contact(primary, known[primary], False))
            continue
        if primary and primary in seen:
            continue
        pending.append((phone_line, name_line, primary))
    if pending:
        read_phones = _read_phones(screen, [(phone_line, name_line) for phone_line, name_line, _ in pending])
        name_lines = [name_line for _, name_line, _ in pending]
        read_names = _read_names(
            [_crop(screen, line, NAME_SMALL) for line in name_lines],
            [_crop(screen, line) for line in name_lines],
        )
        for (phone_line, name_line, primary), read_phone, raw_name in zip(pending, read_phones, read_names):
            phone = primary or read_phone or normalize_phone(phone_line.text)
            if not phone or phone in seen:
                continue
            name = raw_name if is_name(raw_name) else ""
            if not name:
                rough = clean_name(name_line.text)
                name = rough if is_name(rough) else ""
            if not name:
                continue
            seen.add(phone)
            side.append(Contact(phone, name, False))
    frame.side_contacts = side
    handle_line = layout.side_handle_line
    if handle_line is None:
        return
    if (
        layout.handle_line is not None
        and frame.handle
        and _same_box(layout.handle_line, handle_line)
    ):
        frame.side_profile_name = frame.profile_name
        frame.side_handle = frame.handle
        return
    name, handle = _read_profile(screen, layout.side_name_line, handle_line, True)
    if handle:
        frame.side_profile_name = name
        frame.side_handle = handle


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
        contacts = [Contact(row.phone, names.get(index, ""), row.in_order) for index, row in enumerate(layout.rows)]
        return FrameRead("list", contacts, at=layout.at, tapped=layout.tapped)
    if layout.kind == "profile" and layout.handle_line is not None:
        name, handle = _read_profile(phone_screen(image), layout.name_line, layout.handle_line, layout.name_readable)
        # Giữ tên dù chưa ra @id. @id lúc quét được điền sau nếu lần đọc kỹ trượt hết.
        if handle or name:
            return FrameRead("profile", profile_name=name, handle=handle, at=layout.at)
    return FrameRead(at=layout.at)


def read_frame(image: Image.Image, at: float = -1.0) -> FrameRead:
    """Quét và đọc kỹ mọi dòng của một ảnh. Video dùng hai bước riêng để chỉ đọc kỹ vài khung."""
    layout = scan_frame(image, at)
    read = read_layout(image, layout)
    if read.kind == "list":
        read.contacts = [contact for contact in read.contacts if contact.name]
    return read


def _list_pairs(lines: list[ocr.TextLine], screen: Image.Image | None = None) -> list[tuple[ocr.TextLine, ocr.TextLine]]:
    """Mỗi dòng số đi với dòng chữ ngay bên dưới, thẳng mép trái."""
    pixels = np.asarray(screen) if screen is not None else None
    pairs = []
    for index, line in enumerate(lines):
        if not looks_like_phone(line.text):
            continue
        row: list[ocr.TextLine] = []
        for below in lines[index + 1 :]:
            if below.y0 > line.y1 + 1.8 * line.h:
                break
            if below.y0 < line.y1 - 0.3 * line.h:
                continue
            if looks_like_phone(below.text):
                break
            if row and abs((below.y0 + below.y1) - (row[0].y0 + row[0].y1)) > 1.2 * max(below.h, row[0].h):
                continue
            if not row and abs(below.x0 - line.x0) > 2.2 * line.h:
                continue
            if below.x0 < line.x0 - 0.5 * line.h or below.x1 > line.x0 + 16 * line.h:
                continue
            row.append(below)
        if not row:
            continue
        # Gạch dưới đầu tên (_hieu.phuong_) nằm thẳng mép trái với dòng số nhưng bước dò dòng
        # hay bỏ sót; nới cả mép phải khi chữ cuối cách xa (như "Ý" trong "Hồ Hữu Ý") bị bỏ sót.
        row.sort(key=lambda item: item.x0)
        x0 = min(line.x0, min(item.x0 for item in row))
        y0 = min(item.y0 for item in row)
        x1 = max(item.x1 for item in row)
        y1 = max(item.y1 for item in row)
        text_h = max(item.text_h for item in row)
        if pixels is not None:
            band = pixels[max(0, y0 - 4) : min(pixels.shape[0], y1 + 4), x0 : min(pixels.shape[1], x1 + 2 * text_h)]
            dark_cols = np.flatnonzero((band < 180).any(axis=0))
            if dark_cols.size:
                x1 = max(x1, x0 + int(dark_cols[-1]) + 1)
        text = " ".join(item.text.strip() for item in row if item.text.strip())
        pairs.append((line, ocr.TextLine(text, x0, y0, x1, y1, text_h)))
    return pairs


def _looks_like_handle_line(text: str) -> bool:
    """Lần dò dòng đôi khi chèn một ký tự lạ ngay sau @ (@†17pbgif6o)."""
    compact = "".join(str(text or "").split())
    return bool(find_handle(compact) or LOOSE_HANDLE.search(compact))


def _merge_handle_fragments(lines: list[ocr.TextLine]) -> list[ocr.TextLine]:
    """Gộp các mảnh của cùng một dòng @id bị tách ngang (do dấu @, gạch dưới hoặc vòng chạm)."""
    merged = list(lines)
    for index, line in enumerate(lines):
        compact = "".join(line.text.split())
        if not ("@" in compact or "®" in compact or _looks_like_handle_line(compact)):
            continue
        pieces = [
            other
            for other in lines
            if other.h <= 1.6 * line.h
            and abs((other.y0 + other.y1) - (line.y0 + line.y1)) <= 1.2 * line.h
            and other.x0 >= line.x0 - 20
            and other.x1 <= line.x0 + 480
            and sum(ch in "@®._" or ch.isalnum() for ch in "".join(other.text.split()))
            >= 0.7 * max(1, len("".join(other.text.split())))
        ]
        if len(pieces) > 1:
            pieces.sort(key=lambda item: item.x0)
            merged[index] = ocr.TextLine(
                "".join(item.text.strip() for item in pieces),
                min(item.x0 for item in pieces),
                min(item.y0 for item in pieces),
                max(item.x1 for item in pieces),
                max(item.y1 for item in pieces),
                max(item.text_h for item in pieces),
            )
    return merged


def _profile_lines(lines: list[ocr.TextLine], height: int) -> tuple[ocr.TextLine | None, ocr.TextLine] | None:
    """Dòng @id ở phần trên màn hình và dòng tên ngay trên nó.

    Ảnh đại diện cao đẩy @id xuống quá nửa màn hình. Lấy @id trên cùng còn
    trong vùng này, không lấy @id của tài khoản gợi ý ở phía dưới. Lần dò
    dòng không thấy dấu @ thì dựa vào hàng Đã follow / Follower / Thích: @id
    là dòng chữ ngay trên hàng số đếm.
    """
    lines = _merge_handle_fragments(lines)
    stats_handle = _handle_above_stats(lines, height)
    candidates = [
        index
        for index, line in enumerate(lines)
        if 0.05 * height <= line.y0 <= 0.62 * height and _looks_like_handle_line(line.text)
    ]
    # Tên hồ sơ cũng có thể chứa @ (Hải@12345123456). Dòng @id thật bắt đầu bằng @.
    starts = [index for index in candidates if "@" in "".join(lines[index].text.split())[:2]]
    for index in starts or candidates:
        line = lines[index]
        # Khi vòng chạm làm mất dấu @ hoặc cắt ngắn dòng @id, nới ô dòng theo hàng Follower bên dưới.
        if (
            (index not in starts or line.x1 - line.x0 < 4.5 * line.h)
            and stats_handle is not None
            and abs(line.y0 - stats_handle.y0) <= 1.5 * max(line.h, stats_handle.h)
        ):
            line = stats_handle
        return _name_above(lines, index), line
    if stats_handle is not None:
        above = [index for index, item in enumerate(lines) if item.y1 <= stats_handle.y0 + 0.3 * stats_handle.h]
        anchor = max(above, key=lambda i: lines[i].y0) if above else len(lines)
        lines_with_handle = lines[:anchor] + [stats_handle]
        return _name_above(lines_with_handle, len(lines_with_handle) - 1), stats_handle
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
        if above.h >= 0.7 * line.h and is_name(above.text):
            return above
    return None


def _handle_above_stats(lines: list[ocr.TextLine], height: int) -> ocr.TextLine | None:
    """Ô dòng @id ước lượng từ hàng Follower và Thích của màn hồ sơ."""
    anchors = [
        line
        for line in lines
        if line.y0 < 0.75 * height
        and any(tag in fold(line.text).casefold() for tag in ("follower", "thich", "foll", "da follow"))
    ]
    for anchor in anchors:
        row = [
            line
            for line in lines
            if abs(line.y0 - anchor.y0) <= 1.2 * max(line.h, anchor.h)
            and any(tag in fold(line.text).casefold() for tag in ("follower", "thich", "foll", "da follow"))
        ]
        numbers = [
            item
            for item in lines
            if anchor.y0 - 3.6 * anchor.h <= item.y0 < anchor.y0 - 0.5 * anchor.h
            and sum(ch.isdigit() for ch in item.text) >= max(1, len("".join(item.text.split())))
        ]
        if len(row) < 2 and len(numbers) < 2:
            continue
        left = min((item.x0 for item in row + numbers if item.x0 < 240), default=58)
        stat_top = min((item.y0 for item in numbers), default=int(anchor.y0 - 2.2 * anchor.h))
        h_est = max(28, anchor.text_h, max((item.text_h for item in numbers), default=28))
        band_top = int(stat_top - 3.2 * h_est)
        band_bottom = int(stat_top - 0.5 * h_est)
        pieces = [
            item
            for item in lines
            if band_top <= item.y0 <= band_bottom
            and item.h <= 1.8 * h_est
            and left - 2 * h_est <= item.x0 <= left + 14 * h_est
            and not fold(item.text).casefold().strip().startswith("follow")
        ]
        if pieces:
            pieces.sort(key=lambda item: item.x0)
            y1 = max(item.y1 for item in pieces)
            h = max(h_est, max(item.text_h for item in pieces))
            x0 = min(58, left, min(item.x0 for item in pieces))
            x1 = max(max(item.x1 for item in pieces), x0 + 13 * h)
            text = "".join(item.text.strip() for item in pieces)
            return ocr.TextLine(text, max(0, x0), max(0, y1 - h), x1, y1, h)
        y1 = int(stat_top - 1.1 * h_est)
        y0 = max(0, y1 - h_est)
        x0 = min(58, left)
        return ocr.TextLine("", max(0, x0), y0, x0 + int(14 * h_est), y1, h_est)
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
    # Gạch dưới ở cuối tên/@id (_hieu.phuong_, @_lantv._) hay bị bước dò dòng
    # bỏ sót; nới bên phải nửa chiều cao chữ để không cắt cụt gạch dưới thành dấu chấm.
    pad_left = max(6, line.text_h // 3)
    pad_right = max(12, line.text_h // 2)
    pad_y = max(6, line.text_h // 3)
    piece = screen.crop(
        (
            max(0, line.x0 - pad_left),
            max(0, line.y0 - pad_y),
            min(screen.width, line.x1 + pad_right),
            min(screen.height, line.y1 + pad_y),
        )
    )
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


def _read_phones(screen: Image.Image, pairs: list[tuple[ocr.TextLine, ocr.TextLine]]) -> list[str]:
    phone_crops = [_crop(screen, phone) for phone, _ in pairs]
    best_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS)
    fast_digits = ocr.read_texts(phone_crops, "eng", ocr.DIGITS, best=False)
    return [
        vote([normalize_phone(best_digits[index]), normalize_phone(fast_digits[index]), normalize_phone(phone_line.text)])
        for index, (phone_line, _) in enumerate(pairs)
    ]


def _read_handle(screen: Image.Image, line: ocr.TextLine) -> str:
    """Đọc dòng @id ở nhiều cỡ, bằng nhiều bộ, rồi bỏ phiếu theo từng ký tự.

    Bộ tiếng Việt đọc chữ cái trong @id tốt, nhưng hay đọc 3 thành 5; chữ số
    của nó được tin ít hơn chữ số của bộ tiếng Anh.
    """
    # @id TikTok không kết thúc bằng dấu chấm; khi lần dò dòng dừng ở dấu chấm cuối
    # (@_tamshop.) thì ngay sau đó có thể còn gạch dưới bị cắt sót.
    if line.text.rstrip().endswith("."):
        line = ocr.TextLine(line.text, line.x0, line.y0, line.x1 + line.text_h // 2, line.y1, line.text_h)
    reads: list[tuple[str, float, float]] = [(handle_from_read(line.text), 1.0, VIE_DIGIT_WEIGHT)]
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
    handle = find_handle("@" + vote_chars(bodies, loose=False)) if bodies else ""
    if not handle:
        return ""
    if not handle.endswith("_") and sum(h.endswith("._") for h, _, _ in reads) >= 3:
        handle = handle.rstrip(".") + "._"
    handle = find_handle(fix_underscores(handle, _underscore_strokes(screen, line, len(handle))))
    return _fix_handle_lookalikes(screen, line, handle, reads) if handle else ""


def _fix_handle_lookalikes(
    screen: Image.Image,
    line: ocr.TextLine,
    handle: str,
    reads: list[tuple[str, float, float]],
) -> str:
    """Phân biệt 0 với o và 1 với l, i trong @id bằng chiều cao và bề rộng nét chữ trên ảnh.

    Trong phông chữ TikTok, số 0 cao bằng chữ số và chữ có nét cao (1..9, b, d, f, h, k, l, t),
    còn chữ o thấp bằng các chữ thường không nét cao (a, c, e, m, n, r, s, u, v, w, x, z).
    Chữ i có khe hở giữa dấu chấm và thân; số 1 có mỏ đầu nên rộng hơn hẳn chữ l (nét thẳng mảnh).
    """
    pad = max(4, line.text_h // 4)
    box = (
        max(0, line.x0 - pad),
        max(0, line.y0 - pad),
        min(screen.width, line.x1 + pad),
        min(screen.height, line.y1 + pad),
    )
    pixels = np.asarray(screen.crop(box)).astype(np.int16)
    if pixels.size == 0:
        return handle
    background = float(np.percentile(pixels, 90))
    ink = float(np.percentile(pixels, 2))
    if background - ink < 40:
        return handle
    dark = pixels < (background + ink) / 2
    cols = np.flatnonzero(dark.any(axis=0))
    if cols.size == 0:
        return handle
    runs: list[tuple[int, int]] = []
    for col in cols:
        if runs and int(col) - runs[-1][1] <= 1:
            runs[-1] = (runs[-1][0], int(col))
        else:
            runs.append((int(col), int(col)))
    runs = [(c0, c1) for c0, c1 in runs if c1 - c0 + 1 >= 2]
    if len(runs) != len(handle):
        return handle
    glyphs: list[tuple[int, int, int, int, bool]] = []
    for c0, c1 in runs:
        rows = np.flatnonzero(dark[:, c0 : c1 + 1].any(axis=1))
        y0, y1 = int(rows[0]), int(rows[-1])
        has_gap = any(int(b) - int(a) > 2 for a, b in zip(rows, rows[1:]))
        glyphs.append((y0, y1, y1 - y0 + 1, c1 - c0 + 1, has_gap))
    if any(glyphs[i][2] < 10 for i, ch in enumerate(handle) if ch not in "._1li"):
        return handle
    out = list(handle)
    tall_chars = set("123456789bdfhklt")
    short_chars = set("aceimnrsuvwxz")
    tall_tops = [glyphs[i][0] for i, ch in enumerate(out) if i > 0 and ch in tall_chars]
    short_tops = [glyphs[i][0] for i, ch in enumerate(out) if i > 0 and ch in short_chars]
    tall_hs = [glyphs[i][2] for i, ch in enumerate(out) if i > 0 and ch in tall_chars]
    short_hs = [glyphs[i][2] for i, ch in enumerate(out) if i > 0 and ch in short_chars]
    if tall_tops and short_tops:
        t_top, s_top = float(np.median(tall_tops)), float(np.median(short_tops))
        t_h, s_h = float(np.median(tall_hs)), float(np.median(short_hs))
        if s_top - t_top >= 4.0 and t_h - s_h >= 4.0:
            mid_top = (t_top + s_top) / 2.0
            mid_h = (t_h + s_h) / 2.0
            for i in range(1, len(out)):
                if out[i] not in "0o":
                    continue
                if len({h[i] for h, _, _ in reads if len(h) == len(out) and h[i] in "0o"}) < 2:
                    continue
                if out[i] == "o" and glyphs[i][0] < mid_top and glyphs[i][2] >= mid_h:
                    out[i] = "0"
                elif out[i] == "0" and glyphs[i][0] > mid_top and glyphs[i][2] < mid_h:
                    out[i] = "o"
    for i in range(1, len(out)):
        if out[i] not in "1li":
            continue
        pos_chars = {h[i] for h, _, _ in reads if len(h) == len(out) and h[i] in "1li"}
        if i == 1:
            pos_chars |= {h[1] for h, _, _ in reads if len(h) >= 2 and h[1] in "1li"}
        if len(pos_chars) < 2 and i != 1:
            continue
        _, _, h_i, w_i, gap_i = glyphs[i]
        if gap_i:
            out[i] = "i"
        elif w_i >= max(8, round(0.30 * h_i)):
            out[i] = "1"
        elif w_i <= max(6, round(0.22 * h_i)):
            out[i] = "l"
    return find_handle("".join(out)) or handle


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
            candidates.append(_read_names([_crop(screen, band, NAME_SMALL)], [_crop(screen, band)])[0])
    for raw in candidates:
        name = clean_name(raw)
        if is_name(name):
            return name, handle
    return "", handle


def _band_line(screen: Image.Image, line: ocr.TextLine) -> ocr.TextLine | None:
    """Ô dải chữ ngay trên @id, dùng khi lần dò dòng không tách được tên."""
    top = max(0, int(line.y0 - 4.0 * line.h))
    bottom = min(screen.height, int(line.y0 - 0.8 * line.h))
    left = max(0, line.x0 - line.h // 2)
    right = min(int(0.68 * screen.width), max(line.x1, line.x0 + 10 * line.h))
    if bottom - top < 8 or right - left < 8:
        return None
    pixels = np.asarray(screen.crop((left, top, right, bottom)))
    dark = pixels < 180
    rows = np.flatnonzero(dark.any(axis=1))
    cols = np.flatnonzero(dark.any(axis=0))
    if rows.size >= 8 and cols.size >= 8:
        y0, y1 = top + int(rows[0]), top + int(rows[-1]) + 1
        x0, x1 = left + int(cols[0]), left + int(cols[-1]) + 1
        return ocr.TextLine("", x0, y0, x1, y1, max(16, y1 - y0))
    return ocr.TextLine("", left, top, right, bottom, max(16, int(1.6 * line.text_h)))
