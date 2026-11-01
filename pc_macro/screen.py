"""Compare the screen after Enter, and keep the four recovery clicks."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageGrab

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
                    "detail": f"trái  {item['x']} , {item['y']}",
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


def append_screen_click(clicks: Any, x: Any, y: Any) -> list[dict[str, Any]]:
    """Remember the next left-click in order (max four)."""
    return sanitize_clicks([*sanitize_clicks(clicks), {"x": x, "y": y}])


def overlay_banner(count: int, *, recording: bool, total: int = RECOVER_CLICKS) -> str:
    """Top bar while the user points at the real screen."""
    if recording and 0 <= count < total:
        return f"Bấm trái nút {count + 1}/{total} trên màn hình. Esc hủy."
    return "Đã nhớ 4 nút trái theo thứ tự 1 → 2 → 3 → 4."


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
        clicks.append({"kind": "click", "x": x, "y": y, "button": "left", "wait": 120})
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
