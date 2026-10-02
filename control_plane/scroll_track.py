"""Cuộn danh bạ và khung mờ lúc chuyển trang, trên đúng một định dạng video iPhone.

Danh bạ trượt gần như thẳng đứng. Khung đã là danh bạ thì dòng nằm giữa màn hình giữ nguyên chữ đã đọc,
chỉ nhận dạng dải mới ở mép. Trang hồ sơ đứng yên rồi mờ khoảng bốn phần mười giây: khung mờ hơn hẳn
các khung quanh nó, và không phải một nhịp cuộn, thì bỏ trước khi đọc chữ.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Protocol

from PIL import Image, ImageChops, ImageFilter, ImageStat

from control_plane.screen_people import TextLine

# Ảnh nhỏ cùng cách cắt với bước chọn khung đổi: bỏ 8% trên và 8% dưới, còn 80×130.
_THUMB = (80, 130)
# Tìm lệch tối đa 16 điểm trên ảnh nhỏ, khoảng 160 điểm trên ảnh đã làm nét. Lệch sát biên thì chưa chắc.
_SEARCH = 16
# Trung bình lệch điểm ảnh sau khi đã khớp. Cuộn thật thường dưới 8. Cắt cảnh và khung mờ trên 20.
_SCORE_LIMIT = 12.0
_SCORE_MARGIN = 2.0
# Dòng chạm mép trên 100 điểm hoặc mép dưới 60 điểm đọc sai. Đọc lại cho đến khi dòng vào trong vùng này.
EDGE_TOP = 100
EDGE_BOTTOM = 60
# Thêm một hàng phía trong mép, để dòng vừa ra khỏi vùng mép được đọc một lần khi chữ đã trọn.
_ROW_PAD = 80
# Khung mờ khi độ nét dưới 72% khung sắc nhất trong cửa sổ lân cận.
_FADE_RATIO = 0.72


class _Line(Protocol):
    text: str
    left: int
    top: int
    bottom: int


class Shift(NamedTuple):
    """dy dương: chữ trượt lên, hàng mới vào từ mép dưới. confident khi khớp một nhịp cuộn."""

    dy: int
    confident: bool


def thumb(path: Path) -> Image.Image | None:
    """Ảnh xám nhỏ để đo cuộn và độ nét. Cùng vùng với bước chọn khung đổi."""
    try:
        with Image.open(path) as full:
            gray = full.convert("L")
            width, height = gray.size
            small = gray.crop((0, int(height * 0.08), width, int(height * 0.92))).resize(_THUMB)
            small.load()
            return small
    except OSError:
        return None


def _motion_band(image: Image.Image) -> Image.Image:
    """Cột tên. Bỏ mép trên (tiêu đề đứng yên) và mép phải (nút Follow đứng yên)."""
    width, height = image.size
    return image.crop((0, int(height * 0.08), int(width * 0.78), int(height * 0.96)))


def _score(previous: Image.Image, current: Image.Image, dy: int) -> float:
    """Trung bình lệch khi dịch ảnh trước lên dy điểm. dy dương là chữ đi lên."""
    width, height = previous.size
    if current.size != previous.size:
        current = current.resize(previous.size)
    if dy >= 0:
        before = previous.crop((0, dy, width, height))
        after = current.crop((0, 0, width, height - dy))
    else:
        before = previous.crop((0, 0, width, height + dy))
        after = current.crop((0, -dy, width, height))
    if before.size != after.size or before.width < 2 or before.height < 4:
        return 255.0
    return float(ImageStat.Stat(ImageChops.difference(before, after)).mean[0])


def vertical_shift(previous: Image.Image, current: Image.Image) -> Shift:
    """Lệch dọc tốt nhất giữa hai khung liền. Không chắc thì dy = 0."""
    before = _motion_band(previous)
    after = _motion_band(current)
    scores = {dy: _score(before, after, dy) for dy in range(-_SEARCH, _SEARCH + 1)}
    best = min(scores, key=lambda dy: (scores[dy], abs(dy)))
    best_score = scores[best]
    sure = (
        best != 0
        and abs(best) < _SEARCH
        and best_score <= _SCORE_LIMIT
        and best_score + _SCORE_MARGIN <= scores[0]
    )
    if not sure:
        return Shift(0, False)
    return Shift(best, True)


def plan(paths: list[Path]) -> list[Shift]:
    """Một nhịp cuộn cho từng khung so với khung được đọc ngay trước. Khung đầu không cuộn."""
    thumbs = [thumb(path) for path in paths]
    shifts = [Shift(0, False)]
    for previous, current in zip(thumbs, thumbs[1:]):
        if previous is None or current is None:
            shifts.append(Shift(0, False))
            continue
        shifts.append(vertical_shift(previous, current))
    return shifts


def prepared_dy(thumb_dy: int, prepared_height: int, thumb_height: int = _THUMB[1]) -> int:
    """Đổi lệch trên ảnh nhỏ sang ảnh đã cắt làm nét.

    Ảnh nhỏ là 84% chiều cao gốc. Ảnh làm nét là 88% (cắt 4% trên và 8% dưới).
    """
    if thumb_height <= 0 or prepared_height <= 0 or thumb_dy == 0:
        return 0
    return int(round(thumb_dy * prepared_height * 0.84 / (thumb_height * 0.88)))


def strip_bounds(height: int, dy: int) -> tuple[int, int]:
    """Mép cần đọc lại: vùng chữ bị cắt cộng phần vừa trượt vào. Trả (top, bottom)."""
    if height <= 0:
        return 0, 0
    if dy >= 0:
        band = max(EDGE_BOTTOM + dy + _ROW_PAD, int(height * 0.12))
        return max(0, height - band), height
    band = max(EDGE_TOP + abs(dy) + _ROW_PAD, int(height * 0.12))
    return 0, min(height, band)


def in_safe(line: _Line, height: int) -> bool:
    """Dòng đã nằm trọn trong vùng đọc được, không chạm mép trên hay mép dưới."""
    if height <= 0:
        return False
    return line.top >= EDGE_TOP and line.bottom <= height - EDGE_BOTTOM


def outside_strip(line: _Line, top: int, bottom: int) -> bool:
    """Phần lớn dòng nằm ngoài dải vừa đọc thì giữ chữ cũ."""
    overlap = min(line.bottom, bottom) - max(line.top, top)
    return overlap <= 0.4 * max(1, line.bottom - line.top)


def shift_lines(lines: list[_Line], dy: int, height: int) -> list[TextLine]:
    """Dịch dòng theo nhịp cuộn. dy dương đẩy dòng lên. Dòng ra khỏi ảnh thì bỏ."""
    moved: list[TextLine] = []
    for line in lines:
        top = line.top - dy
        bottom = line.bottom - dy
        if bottom <= 2 or top >= height:
            continue
        moved.append(TextLine(line.text, line.left, top, bottom))
    return moved


def offset_tsv(tsv: str, top: int) -> str:
    """Cộng top vào cột tung độ. TSV của ảnh cắt có tung độ tính từ mép cắt."""
    if not tsv or top == 0:
        return tsv
    rows: list[str] = []
    for raw in tsv.splitlines():
        parts = raw.split("\t")
        if len(parts) >= 8 and parts[0].isdigit():
            try:
                parts[7] = str(int(float(parts[7])) + top)
            except ValueError:
                pass
            raw = "\t".join(parts)
        rows.append(raw)
    return "\n".join(rows)


def _sharpness(image: Image.Image) -> float:
    width, height = image.size
    band = image.crop((int(width * 0.15), int(height * 0.15), int(width * 0.85), int(height * 0.85)))
    edges = band.filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).mean[0])


def fade_indexes(thumbs: list[Image.Image | None]) -> list[int]:
    """Chỉ số khung mờ lúc chuyển trang. Khung đang cuộn danh bạ không bị bỏ."""
    scores = [0.0 if image is None else _sharpness(image) for image in thumbs]
    drop: list[int] = []
    total = len(thumbs)
    for index, score in enumerate(scores):
        window = scores[max(0, index - 2) : index + 3]
        peak = max(window) if window else 0.0
        if peak < 1.0 or score >= peak * _FADE_RATIO:
            continue
        current = thumbs[index]
        if current is None:
            continue
        scrolling = False
        if index > 0 and thumbs[index - 1] is not None:
            scrolling = vertical_shift(thumbs[index - 1], current).confident  # type: ignore[arg-type]
        if index + 1 < total and thumbs[index + 1] is not None and not scrolling:
            scrolling = vertical_shift(current, thumbs[index + 1]).confident  # type: ignore[arg-type]
        if scrolling:
            continue
        drop.append(index)
    if len(drop) > total * 0.4:
        return []
    return drop
