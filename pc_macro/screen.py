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
