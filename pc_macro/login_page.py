"""Recognize Evoshop's forced password-change page after a successful login.

That screen still counts as a login hit. The PC saves it as "đợi đổi mật khẩu",
clicks the in-page back arrow, then types the next customer code on the login form.
"""

from __future__ import annotations

from typing import Any, Callable

from pc_macro.script import click_focus_step, compile_script
from pc_macro.win_focus import window_box

try:
    from PIL import Image, ImageGrab
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageGrab = None  # type: ignore[assignment]

OUTCOME_OK = "ok"
OUTCOME_CHANGED = "changed"
OUTCOME_PASSWORD_WAIT = "password_wait"
PASSWORD_WAIT_LABEL = "Đợi đổi mật khẩu"
BOARD_LABELS = {
    "run": "Đang chạy",
    "changed": "Đổi giao diện",
    "password_wait": PASSWORD_WAIT_LABEL,
    "ok": "Giữ nguyên",
}
BOARD_COLORS = {
    "run": "#7dd3fc",
    "changed": "#fbbf24",
    "password_wait": "#fb7185",
    "ok": "#94a3b8",
}

GRID_W = 48
GRID_H = 80
_TEAL = (0, 191, 165)
_MAX_COORD = 100_000
SendFn = Callable[[dict[str, Any]], None]
SleepFn = Callable[[float], None]
CancelFn = Callable[[], bool]


def turn_outcome(changed: bool = False, outcome: str = "") -> str:
    """Normalize one Enter result. Password-wait is a successful login."""
    text = str(outcome or "").strip().lower()
    if text in {"password_wait", "password", "doi_mat_khau", "wait_password"}:
        return OUTCOME_PASSWORD_WAIT
    if text == OUTCOME_CHANGED or changed:
        return OUTCOME_CHANGED
    if text == OUTCOME_OK:
        return OUTCOME_OK
    return OUTCOME_OK


def board_state(item: dict[str, Any] | None) -> str:
    raw = item or {}
    outcome = turn_outcome(bool(raw.get("changed")), str(raw.get("outcome") or raw.get("state") or ""))
    if outcome == OUTCOME_PASSWORD_WAIT:
        return OUTCOME_PASSWORD_WAIT
    if outcome == OUTCOME_CHANGED or raw.get("changed"):
        return OUTCOME_CHANGED
    return OUTCOME_OK


def board_label(state: str) -> str:
    return BOARD_LABELS.get(str(state or ""), BOARD_LABELS["ok"])


def looks_like_password_change(pixels: Any, width: int, height: int) -> bool:
    """True when the grab matches the Evoshop 'ĐỔI MẬT KHẨU' form."""
    grid = _as_grid(pixels, width, height)
    if grid is None:
        return False
    cells, cols, rows = grid
    if _light_share(cells) < 0.42:
        return False
    if not _has_teal_band(cells, cols, rows):
        return False
    if _input_rows(cells, cols, rows) < 2:
        return False
    return find_back_arrow(cells, cols, rows) is not None


def resolve_enter_outcome(*, changed: bool, pixels: Any = None, width: int = 0, height: int = 0) -> str:
    """Password-change page wins over a generic UI flip."""
    if looks_like_password_change(pixels, width, height):
        return OUTCOME_PASSWORD_WAIT
    if changed:
        return OUTCOME_CHANGED
    return OUTCOME_OK


def find_back_arrow(pixels: Any, width: int, height: int) -> tuple[int, int] | None:
    """Pixel of the in-page left chevron, or None."""
    grid = _as_grid(pixels, width, height)
    if grid is None:
        return None
    cells, cols, rows = grid
    y_limit = max(4, int(rows * 0.28))
    x_limit = max(6, int(cols * 0.32))
    best = None
    best_score = 0.0
    for top in range(1, y_limit - 4):
        for left in range(1, x_limit - 4):
            score = _chevron_score(cells, cols, left, top)
            if score > best_score:
                best_score = score
                best = (left + 1, top + 2)
    if best is None or best_score < 2.4:
        return None
    return best


def view_box(
    click: Any,
    *,
    screen_w: int = 0,
    screen_h: int = 0,
    hwnd_rect: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int] | None:
    """Screen rectangle that should contain the Evoshop login / password form."""
    if hwnd_rect and len(hwnd_rect) == 4:
        left, top, right, bottom = (int(item) for item in hwnd_rect)
        if right - left >= 40 and bottom - top >= 40:
            return left, top, right, bottom
    if not isinstance(click, dict):
        return None
    try:
        x = int(click["x"])
        y = int(click["y"])
    except (TypeError, ValueError, KeyError):
        return None
    left = max(0, x - 220)
    top = max(0, y - 300)
    right = x + 220
    bottom = y + 380
    try:
        wx = int(click.get("wx"))
        wy = int(click.get("wy"))
    except (TypeError, ValueError):
        wx, wy = -1, -1
    if wx >= 0 and wy >= 0:
        left = min(left, max(0, wx - 40))
        top = min(top, max(0, wy - 40))
        right = max(right, wx + 40)
        bottom = max(bottom, wy + 40)
    if screen_w > 0:
        right = min(screen_w, right)
        left = min(left, max(0, screen_w - 40))
    if screen_h > 0:
        bottom = min(screen_h, bottom)
        top = min(top, max(0, screen_h - 40))
    if right - left < 40 or bottom - top < 40:
        return None
    return left, top, right, bottom


def capture_login_view(click: Any, *, grab: Any = None) -> dict[str, Any] | None:
    """Grab the bound Evoshop window (or a box around the field)."""
    take = grab if grab is not None else _grab
    box = view_box(click, hwnd_rect=_hwnd_rect(click))
    if box is None:
        return None
    image = take(box)
    if image is None:
        return None
    pixels, width, height = _image_cells(image)
    if not pixels:
        return None
    return {
        "pixels": pixels,
        "width": width,
        "height": height,
        "left": box[0],
        "top": box[1],
        "box_w": box[2] - box[0],
        "box_h": box[3] - box[1],
    }


def back_click_for(
    bound: Any,
    view: dict[str, Any] | None = None,
    *,
    saved_back: Any = None,
) -> dict[str, Any] | None:
    """Click the in-page back arrow so the login form returns."""
    saved = _point_click(saved_back)
    if saved is not None:
        return saved
    if not view:
        return _fallback_back_click(bound)
    try:
        width = int(view["width"])
        height = int(view["height"])
        origin_x = int(view.get("left") or 0)
        origin_y = int(view.get("top") or 0)
    except (TypeError, ValueError, KeyError):
        return _fallback_back_click(bound)
    found = find_back_arrow(view.get("pixels"), width, height)
    if found is None:
        return _fallback_back_click(bound)
    fx, fy = found
    src_w = int(view.get("box_w") or width)
    src_h = int(view.get("box_h") or height)
    x = origin_x + int(round((fx + 0.5) * src_w / max(1, width)))
    y = origin_y + int(round((fy + 0.5) * src_h / max(1, height)))
    return {"kind": "click", "x": max(0, x), "y": max(0, y), "button": "left", "wait": 80}


def recover_login_after_password(
    send: SendFn,
    sleep: SleepFn,
    bound: Any,
    view: dict[str, Any] | None,
    *,
    saved_back: Any = None,
    cancelled: CancelFn | None = None,
    recapture: Callable[[], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Click back once (retry if still on the password form), then continue."""
    click = back_click_for(bound, view, saved_back=saved_back)
    if click is None:
        return {"clicked": False, "retries": 0}
    played = _emit_click(send, click)
    if cancelled and cancelled():
        return {"clicked": played > 0, "retries": 0, "stopped": True}
    sleep(0.42)
    retries = 0
    if recapture is not None:
        again = recapture()
        if again and looks_like_password_change(again.get("pixels"), int(again.get("width") or 0), int(again.get("height") or 0)):
            retries = 1
            extra = back_click_for(bound, again, saved_back=saved_back) or click
            _emit_click(send, extra)
            sleep(0.35)
    return {"clicked": played > 0, "retries": retries, "x": click["x"], "y": click["y"]}


def _emit_click(send: SendFn, click: dict[str, Any]) -> int:
    step = click_focus_step(click, wait=int(click.get("wait") or 80))
    if step is None:
        return 0
    _script, events = compile_script([step])
    played = 0
    for event in events:
        send(event)
        played += 1
    return played


def _hwnd_rect(click: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(click, dict):
        return None
    try:
        handle = int(click.get("hwnd") or 0)
    except (TypeError, ValueError):
        handle = 0
    if handle <= 0:
        return None
    return window_box(handle)


def _grab(box: tuple[int, int, int, int]) -> Any:
    if ImageGrab is None:
        return None
    left, top, right, bottom = box
    try:
        return ImageGrab.grab(bbox=(left, top, right, bottom))
    except Exception:
        try:
            full = ImageGrab.grab()
        except Exception:
            return None
        return full.crop((left, top, min(full.size[0], right), min(full.size[1], bottom)))


def _image_cells(image: Any) -> tuple[tuple[tuple[int, int, int], ...], int, int]:
    if image is None or Image is None:
        return (), 0, 0
    if image.mode != "RGB":
        image = image.convert("RGB")
    small = image.resize((GRID_W, GRID_H), Image.Resampling.BOX)
    loaded = small.load()
    cells: list[tuple[int, int, int]] = []
    for y in range(GRID_H):
        for x in range(GRID_W):
            pixel = loaded[x, y]
            cells.append((int(pixel[0]), int(pixel[1]), int(pixel[2])))
    return tuple(cells), GRID_W, GRID_H


def _as_grid(pixels: Any, width: int, height: int) -> tuple[tuple[tuple[int, int, int], ...], int, int] | None:
    try:
        cols = int(width)
        rows = int(height)
    except (TypeError, ValueError):
        return None
    if cols < 8 or rows < 12:
        return None
    cells = list(pixels or [])
    if len(cells) < cols * rows:
        return None
    cleaned: list[tuple[int, int, int]] = []
    for item in cells[: cols * rows]:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            return None
        cleaned.append((int(item[0]), int(item[1]), int(item[2])))
    return tuple(cleaned), cols, rows


def _light_share(cells: tuple[tuple[int, int, int], ...]) -> float:
    if not cells:
        return 0.0
    hits = 0
    for red, green, blue in cells:
        _hue, sat, val = _hsv(red, green, blue)
        if sat < 0.22 and val > 0.72:
            hits += 1
    return hits / len(cells)


def _has_teal_band(cells: tuple[tuple[int, int, int], ...], cols: int, rows: int) -> bool:
    start = int(rows * 0.42)
    for row in range(start, rows):
        teal = 0
        for col in range(cols):
            if _is_teal(cells[row * cols + col]):
                teal += 1
        if teal >= max(8, int(cols * 0.34)):
            return True
    return False


def _input_rows(cells: tuple[tuple[int, int, int], ...], cols: int, rows: int) -> int:
    """Count stacked pale field bands above the teal button."""
    bands = 0
    in_band = False
    top = int(rows * 0.18)
    bottom = int(rows * 0.78)
    for row in range(top, bottom):
        pale = 0
        darkish = 0
        teal = 0
        for col in range(cols):
            pixel = cells[row * cols + col]
            if _is_teal(pixel):
                teal += 1
                continue
            _hue, sat, val = _hsv(*pixel)
            if sat < 0.2 and 0.78 < val < 0.98:
                pale += 1
            if val < 0.55:
                darkish += 1
        is_field = pale >= int(cols * 0.45) and teal < 3 and darkish <= int(cols * 0.2)
        if is_field and not in_band:
            bands += 1
            in_band = True
        elif not is_field:
            in_band = False
    return bands


def _chevron_score(cells: tuple[tuple[int, int, int], ...], cols: int, left: int, top: int) -> float:
    """Left-pointing chevron: dark pixels on a light header."""
    marks = ((2, 0), (1, 1), (0, 2), (1, 3), (2, 4))
    opposite = ((0, 0), (1, 1), (2, 2), (1, 3), (0, 4))
    light = 0.0
    dark = 0.0
    for dx, dy in marks:
        dark += 1.0 - _value(cells[(top + dy) * cols + (left + dx)])
    anti = 0.0
    for dx, dy in opposite:
        anti += 1.0 - _value(cells[(top + dy) * cols + (left + dx)])
    for dx in range(5):
        for dy in range(5):
            if (dx, dy) in marks:
                continue
            light += _value(cells[(top + dy) * cols + (left + dx)])
    return dark - 0.55 * anti + 0.08 * light


def _is_teal(pixel: tuple[int, int, int]) -> bool:
    hue, sat, val = _hsv(*pixel)
    return 150 <= hue <= 188 and sat >= 0.28 and val >= 0.32


def _value(pixel: tuple[int, int, int]) -> float:
    return max(pixel) / 255.0


def _hsv(red: int, green: int, blue: int) -> tuple[float, float, float]:
    rf, gf, bf = red / 255.0, green / 255.0, blue / 255.0
    mx = max(rf, gf, bf)
    mn = min(rf, gf, bf)
    delta = mx - mn
    if delta == 0:
        hue = 0.0
    elif mx == rf:
        hue = (60 * ((gf - bf) / delta) + 360) % 360
    elif mx == gf:
        hue = 60 * ((bf - rf) / delta) + 120
    else:
        hue = 60 * ((rf - gf) / delta) + 240
    sat = 0.0 if mx == 0 else delta / mx
    return hue, sat, mx


def _point_click(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        x = int(raw["x"])
        y = int(raw["y"])
    except (TypeError, ValueError, KeyError):
        return None
    if not (0 <= x <= _MAX_COORD and 0 <= y <= _MAX_COORD):
        return None
    return {"kind": "click", "x": x, "y": y, "button": str(raw.get("button") or "left"), "wait": 80}


def _fallback_back_click(bound: Any) -> dict[str, Any] | None:
    """Guess the header back arrow from the bound field / window point."""
    if not isinstance(bound, dict):
        return None
    try:
        field_x = int(bound["x"])
        field_y = int(bound["y"])
    except (TypeError, ValueError, KeyError):
        return None
    try:
        window_x = int(bound.get("wx"))
        window_y = int(bound.get("wy"))
    except (TypeError, ValueError):
        window_x, window_y = -1, -1
    if window_x >= 0 and window_y >= 0:
        x = window_x + 28
        y = window_y + 58
    else:
        x = max(8, field_x - 150)
        y = max(8, field_y - 240)
    return {"kind": "click", "x": x, "y": y, "button": "left", "wait": 80}
