"""Compare the screen after Enter, and keep the four recovery clicks."""

from __future__ import annotations

import sys
from typing import Any

from PIL import Image, ImageGrab

try:
    import ctypes
except ImportError:  # pragma: no cover
    ctypes = None  # type: ignore[assignment]

GRID_W = 48
GRID_H = 27
CELL_DELTA = 28
RECOVER_CLICKS = 4
DEFAULT_THRESHOLD = 80
_MAX_COORD = 100_000

Grid = tuple[tuple[int, int, int], ...]


def normalize_threshold(raw: Any, default: int = DEFAULT_THRESHOLD) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(100, value))


def click_order_labels(clicks: Any) -> list[str]:
    """Four lines: Nút 1 → … so the order stays obvious."""
    clean = sanitize_clicks(clicks)
    labels: list[str] = []
    for index in range(RECOVER_CLICKS):
        if index < len(clean):
            item = clean[index]
            labels.append(f"Nút {index + 1}  →  chuột trái  ·  {item['x']} , {item['y']}")
        else:
            labels.append(f"Nút {index + 1}  →  chưa ghi")
    return labels


def click_order_cards(clicks: Any) -> list[dict[str, str]]:
    """One card per button: big step number plus the saved point."""
    clean = sanitize_clicks(clicks)
    cards: list[dict[str, str]] = []
    for index in range(RECOVER_CLICKS):
        if index < len(clean):
            item = clean[index]
            cards.append(
                {
                    "step": str(index + 1),
                    "title": f"Bước {index + 1}",
                    "detail": _click_detail(item),
                    "empty": "",
                }
            )
        else:
            cards.append(
                {
                    "step": str(index + 1),
                    "title": f"Bước {index + 1}",
                    "detail": "chưa ghi",
                    "empty": "1",
                }
            )
    return cards


def append_screen_click(clicks: Any, x: Any, y: Any, sw: Any = 0, sh: Any = 0) -> list[dict[str, Any]]:
    """Remember the next left-click in order (max four), with the screen it was on."""
    return sanitize_clicks([*sanitize_clicks(clicks), {"x": x, "y": y, "sw": sw, "sh": sh}])


def overlay_banner(count: int, *, recording: bool, total: int = RECOVER_CLICKS, awaiting_save: bool = False) -> str:
    """Top bar while the user points at the real screen."""
    if awaiting_save:
        return "Đã chọn 4 nút. Bấm Lưu để giữ vị trí."
    if recording and 0 <= count < total:
        return f"Bấm trái nút {count + 1}/{total} trên màn hình. Esc hủy."
    return "Đã nhớ 4 nút trái theo thứ tự 1 → 2 → 3 → 4."


def current_screen_size(fallback: tuple[int, int] | None = None) -> tuple[int, int]:
    """Width and height of the desktop the clicks must land on."""
    try:
        image = ImageGrab.grab()
        width, height = int(image.size[0]), int(image.size[1])
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    if sys.platform == "win32" and ctypes is not None:
        try:
            user32 = ctypes.windll.user32
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            if width > 0 and height > 0:
                return width, height
        except Exception:
            pass
    if fallback and fallback[0] > 0 and fallback[1] > 0:
        return int(fallback[0]), int(fallback[1])
    return (0, 0)


def scale_clicks(clicks: Any, sw: int, sh: int) -> list[dict[str, Any]]:
    """Map saved points onto another screen size using the recorded ratio."""
    if sw <= 0 or sh <= 0:
        return sanitize_clicks(clicks)
    out: list[dict[str, Any]] = []
    for item in sanitize_clicks(clicks):
        ratio = _ratio(item)
        if ratio is None:
            x = max(0, min(sw - 1, int(item["x"])))
            y = max(0, min(sh - 1, int(item["y"])))
        else:
            x = max(0, min(sw - 1, int(round(ratio[0] * sw))))
            y = max(0, min(sh - 1, int(round(ratio[1] * sh))))
        out.append(_click_record(x, y, sw, sh, wait=int(item.get("wait") or 120)))
    return out


def _click_detail(item: dict[str, Any]) -> str:
    ratio = _ratio(item)
    if ratio is None:
        return f"trái  {item['x']} , {item['y']}"
    return f"trái  {item['x']} , {item['y']}  ·  {round(ratio[0] * 100)}% × {round(ratio[1] * 100)}%"


def _ratio(item: dict[str, Any]) -> tuple[float, float] | None:
    try:
        rx = float(item.get("rx"))
        ry = float(item.get("ry"))
        if 0 <= rx <= 1 and 0 <= ry <= 1:
            return rx, ry
    except (TypeError, ValueError):
        pass
    try:
        sw = float(item.get("sw") or 0)
        sh = float(item.get("sh") or 0)
        if sw > 0 and sh > 0:
            return max(0.0, min(1.0, float(item["x"]) / sw)), max(0.0, min(1.0, float(item["y"]) / sh))
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _click_record(x: int, y: int, sw: int, sh: int, *, wait: int = 120) -> dict[str, Any]:
    record: dict[str, Any] = {"kind": "click", "x": x, "y": y, "button": "left", "wait": wait}
    if sw > 0 and sh > 0:
        record["sw"] = sw
        record["sh"] = sh
        record["rx"] = max(0.0, min(1.0, x / sw))
        record["ry"] = max(0.0, min(1.0, y / sh))
    return record


def sanitize_clicks(raw: Any) -> list[dict[str, Any]]:
    """Keep up to four left-click points the user pointed at."""
    clicks: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            x = int(round(float(item.get("x"))))
            y = int(round(float(item.get("y"))))
        except (TypeError, ValueError):
            continue
        if not (0 <= x <= _MAX_COORD and 0 <= y <= _MAX_COORD):
            continue
        try:
            sw = int(round(float(item.get("sw") or 0)))
            sh = int(round(float(item.get("sh") or 0)))
        except (TypeError, ValueError):
            sw, sh = 0, 0
        if not (2 <= sw <= _MAX_COORD and 2 <= sh <= _MAX_COORD):
            sw, sh = 0, 0
        try:
            wait = int(item.get("wait") or 120)
        except (TypeError, ValueError):
            wait = 120
        record = _click_record(x, y, sw, sh, wait=max(0, min(2000, wait)))
        ratio = _ratio(item)
        if ratio is not None and sw > 0 and sh > 0:
            record["rx"], record["ry"] = ratio
        clicks.append(record)
        if len(clicks) >= RECOVER_CLICKS:
            break
    return clicks


def capture_grid() -> Grid | None:
    """Downsample the screen so a later compare is cheap."""
    try:
        image = ImageGrab.grab()
    except Exception:
        return None
    if image.mode != "RGB":
        image = image.convert("RGB")
    small = image.resize((GRID_W, GRID_H), Image.Resampling.BOX)
    pixels = tuple((int(r), int(g), int(b)) for r, g, b in small.getdata())
    if len(pixels) != GRID_W * GRID_H:
        return None
    return pixels


def change_percent(before: Grid | None, after: Grid | None, cell_delta: int = CELL_DELTA) -> float:
    """How much of the downsampled screen changed, 0–100."""
    if not before or not after or len(before) != len(after):
        return 0.0
    changed = 0
    for old, new in zip(before, after):
        if abs(old[0] - new[0]) + abs(old[1] - new[1]) + abs(old[2] - new[2]) > cell_delta:
            changed += 1
    return 100.0 * changed / len(before)


def should_recover(before: Grid | None, after: Grid | None, threshold: int) -> tuple[bool, float]:
    """True when the screen changed at least `threshold` percent."""
    percent = change_percent(before, after)
    return percent >= normalize_threshold(threshold), percent


def make_grid(fill: tuple[int, int, int], *, flip: int = 0, color: tuple[int, int, int] = (255, 0, 0)) -> Grid:
    """Build a test grid. `flip` is how many leading cells use `color`."""
    cells: list[tuple[int, int, int]] = []
    total = GRID_W * GRID_H
    changed = max(0, min(total, flip))
    for index in range(total):
        cells.append(color if index < changed else fill)
    return tuple(cells)
