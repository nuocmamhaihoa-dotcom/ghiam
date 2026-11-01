"""Compare the screen after Enter, and keep the four recovery clicks."""

from __future__ import annotations

import sys
from typing import Any

from PIL import Image, ImageGrab

from pc_macro.paint import paint_point
from pc_macro.script import compile_script

try:
    import ctypes
except ImportError:  # pragma: no cover
    ctypes = None  # type: ignore[assignment]

GRID_W = 48
GRID_H = 27
FRAME_GRID_W = 24
FRAME_GRID_H = 14
CELL_DELTA = 28
RECOVER_CLICKS = 4
DEFAULT_THRESHOLD = 80
LANE_PAUSE_THRESHOLD = 20
_MIN_FRAME = 16
CLICK_RETRIES = 2
CLICK_LAND_PERCENT = 3
PATCH_SIZE = 64
PATCH_GRID = 12
NAME_MAX = 40
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
    slots = click_slots(clicks)
    labels: list[str] = []
    for index in range(RECOVER_CLICKS):
        item = slots[index]
        if item is not None:
            labels.append(f"Nút {index + 1}  →  chuột trái  ·  {item['x']} , {item['y']}")
        else:
            labels.append(f"Nút {index + 1}  →  chưa ghi")
    return labels


def click_order_cards(clicks: Any) -> list[dict[str, str]]:
    """One card per button: big step number plus the saved point."""
    slots = click_slots(clicks)
    cards: list[dict[str, str]] = []
    for index in range(RECOVER_CLICKS):
        item = slots[index]
        if item is not None:
            cards.append(
                {
                    "step": str(index + 1),
                    "title": click_name(item, index),
                    "detail": _click_detail(item),
                    "empty": "",
                    "name": normalize_click_name(item.get("name")),
                }
            )
        else:
            cards.append(
                {
                    "step": str(index + 1),
                    "title": f"Bước {index + 1}",
                    "detail": "chưa ghi",
                    "empty": "1",
                    "name": "",
                }
            )
    return cards


def normalize_click_name(raw: Any) -> str:
    """Short label the user types for one recover click."""
    return " ".join(str(raw or "").split())[:NAME_MAX]


def click_name(click: Any, index: int = 0) -> str:
    name = normalize_click_name(click.get("name") if isinstance(click, dict) else "")
    return name or f"Bước {index + 1}"


def apply_click_names(clicks: Any, names: Any) -> list[dict[str, Any]]:
    """Write the four typed names onto the matching step slots."""
    slots = click_slots(clicks)
    labels = list(names or [])
    for index, click in enumerate(slots):
        if click is None or index >= len(labels):
            continue
        name = normalize_click_name(labels[index])
        if name:
            click["name"] = name
        else:
            click.pop("name", None)
    return [item for item in slots if item is not None]


def append_screen_click(clicks: Any, x: Any, y: Any, sw: Any = 0, sh: Any = 0, name: Any = "") -> list[dict[str, Any]]:
    """Remember the next empty slot, with the screen it was on."""
    slots = click_slots(clicks)
    for index, slot in enumerate(slots):
        if slot is None:
            return upsert_slot(clicks, index, x, y, sw, sh, name, via="point")
    return [item for item in slots if item is not None]


def upsert_slot(
    clicks: Any,
    step: Any,
    x: Any,
    y: Any,
    sw: Any = 0,
    sh: Any = 0,
    name: Any = "",
    *,
    via: str = "",
) -> list[dict[str, Any]]:
    """Set one of the four steps without dropping the others."""
    try:
        index = int(step)
    except (TypeError, ValueError):
        return sanitize_clicks(clicks)
    if not 0 <= index < RECOVER_CLICKS:
        return sanitize_clicks(clicks)
    slots = click_slots(clicks)
    try:
        px = int(round(float(x)))
        py = int(round(float(y)))
    except (TypeError, ValueError):
        return [item for item in slots if item is not None]
    try:
        screen_w = int(round(float(sw or 0)))
        screen_h = int(round(float(sh or 0)))
    except (TypeError, ValueError):
        screen_w, screen_h = 0, 0
    slots[index] = _click_record(px, py, screen_w, screen_h, name=name, step=index, via=via)
    return [item for item in slots if item is not None]


def click_slots(clicks: Any) -> list[dict[str, Any] | None]:
    """Always four slots so painting step 3 first still lands on step 3."""
    slots: list[dict[str, Any] | None] = [None] * RECOVER_CLICKS
    sequential: list[dict[str, Any]] = []
    for item in clicks or []:
        record = _parse_click(item)
        if record is None:
            continue
        step = record.get("step")
        if isinstance(step, int) and 0 <= step < RECOVER_CLICKS and slots[step] is None:
            slots[step] = record
        else:
            sequential.append(record)
    for record in sequential:
        for index in range(RECOVER_CLICKS):
            if slots[index] is None:
                slots[index] = {**record, "step": index}
                break
    return slots


def click_from_paint(image: Any, name: Any = "", step: Any = None) -> dict[str, Any] | None:
    """Turn the painted blob into one recover click. The image itself is not stored."""
    hit = paint_point(image)
    if hit is None:
        return None
    return _click_record(hit["x"], hit["y"], hit["sw"], hit["sh"], name=name, step=step, via="paint")


def overlay_banner(
    count: int,
    *,
    recording: bool,
    total: int = RECOVER_CLICKS,
    awaiting_save: bool = False,
    step: int | None = None,
) -> str:
    """Top bar while the user points at the real screen."""
    if step is not None:
        if awaiting_save:
            return f"Đã chọn nút {step + 1}. Bấm Lưu để giữ vị trí."
        if recording:
            return f"Bấm trái nút {step + 1} trên màn hình. Esc hủy."
        return f"Đã nhớ nút {step + 1}."
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
        out.append(
            _click_record(
                x,
                y,
                sw,
                sh,
                wait=int(item.get("wait") or 120),
                name=item.get("name"),
                step=item.get("step"),
                via=str(item.get("via") or ""),
            )
        )
    return out


def _click_detail(item: dict[str, Any]) -> str:
    ratio = _ratio(item)
    painted = "  ·  bôi màu" if item.get("via") == "paint" else ""
    if ratio is None:
        return f"trái  {item['x']} , {item['y']}{painted}"
    return f"trái  {item['x']} , {item['y']}  ·  {round(ratio[0] * 100)}% × {round(ratio[1] * 100)}%{painted}"


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


def _click_record(
    x: int,
    y: int,
    sw: int,
    sh: int,
    *,
    wait: int = 120,
    name: Any = "",
    step: Any = None,
    via: str = "",
) -> dict[str, Any]:
    record: dict[str, Any] = {"kind": "click", "x": x, "y": y, "button": "left", "wait": wait}
    label = normalize_click_name(name)
    if label:
        record["name"] = label
    try:
        index = int(step) if step is not None and str(step) != "" else -1
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < RECOVER_CLICKS:
        record["step"] = index
    mark = str(via or "").strip().lower()
    if mark in {"paint", "point"}:
        record["via"] = mark
    if sw > 0 and sh > 0:
        record["sw"] = sw
        record["sh"] = sh
        record["rx"] = max(0.0, min(1.0, x / sw))
        record["ry"] = max(0.0, min(1.0, y / sh))
    return record


def _parse_click(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    try:
        x = int(round(float(item.get("x"))))
        y = int(round(float(item.get("y"))))
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= _MAX_COORD and 0 <= y <= _MAX_COORD):
        return None
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
    record = _click_record(
        x,
        y,
        sw,
        sh,
        wait=max(0, min(2000, wait)),
        name=item.get("name"),
        step=item.get("step"),
        via=str(item.get("via") or ""),
    )
    ratio = _ratio(item)
    if ratio is not None and sw > 0 and sh > 0:
        record["rx"], record["ry"] = ratio
    return record


def sanitize_clicks(raw: Any) -> list[dict[str, Any]]:
    """Keep up to four left-click points, in step order 1 → 4."""
    return [item for item in click_slots(raw) if item is not None]


def sanitize_frame(raw: Any) -> dict[str, Any] | None:
    """One rectangle the user dragged around the 5-digit area."""
    if not isinstance(raw, dict):
        return None
    try:
        x = int(round(float(raw.get("x"))))
        y = int(round(float(raw.get("y"))))
        width = int(round(float(raw.get("w") or raw.get("width") or 0)))
        height = int(round(float(raw.get("h") or raw.get("height") or 0)))
    except (TypeError, ValueError):
        return None
    if width < 0:
        x += width
        width = -width
    if height < 0:
        y += height
        height = -height
    if width < _MIN_FRAME or height < _MIN_FRAME:
        return None
    if not (0 <= x <= _MAX_COORD and 0 <= y <= _MAX_COORD):
        return None
    if width > _MAX_COORD or height > _MAX_COORD:
        return None
    try:
        sw = int(round(float(raw.get("sw") or 0)))
        sh = int(round(float(raw.get("sh") or 0)))
    except (TypeError, ValueError):
        sw, sh = 0, 0
    if not (2 <= sw <= _MAX_COORD and 2 <= sh <= _MAX_COORD):
        sw, sh = 0, 0
    record: dict[str, Any] = {"x": x, "y": y, "w": width, "h": height}
    if sw > 0 and sh > 0:
        record["sw"] = sw
        record["sh"] = sh
        record["rx"] = max(0.0, min(1.0, x / sw))
        record["ry"] = max(0.0, min(1.0, y / sh))
        record["rw"] = max(0.0, min(1.0, width / sw))
        record["rh"] = max(0.0, min(1.0, height / sh))
    for key in ("rx", "ry", "rw", "rh"):
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 1:
            record[key] = value
    return record


def scale_frame(frame: Any, sw: int, sh: int) -> dict[str, Any] | None:
    """Map a saved number-frame onto the current screen size."""
    item = sanitize_frame(frame)
    if item is None:
        return None
    if sw <= 0 or sh <= 0:
        return item
    if "rw" in item and "rh" in item and "rx" in item and "ry" in item:
        x = max(0, min(sw - _MIN_FRAME, int(round(float(item["rx"]) * sw))))
        y = max(0, min(sh - _MIN_FRAME, int(round(float(item["ry"]) * sh))))
        width = max(_MIN_FRAME, min(sw - x, int(round(float(item["rw"]) * sw))))
        height = max(_MIN_FRAME, min(sh - y, int(round(float(item["rh"]) * sh))))
        return sanitize_frame({"x": x, "y": y, "w": width, "h": height, "sw": sw, "sh": sh})
    return sanitize_frame(
        {
            "x": max(0, min(sw - _MIN_FRAME, int(item["x"]))),
            "y": max(0, min(sh - _MIN_FRAME, int(item["y"]))),
            "w": max(_MIN_FRAME, min(sw, int(item["w"]))),
            "h": max(_MIN_FRAME, min(sh, int(item["h"]))),
            "sw": sw,
            "sh": sh,
        }
    )


def frame_label(frame: Any) -> str:
    item = sanitize_frame(frame)
    if item is None:
        return "Chưa chọn khung 5 số — sẽ so cả màn (≥20% dễ lệch)."
    return f"Khung 5 số: {item['x']},{item['y']}  {item['w']}×{item['h']}"


def capture_grid() -> Grid | None:
    """Downsample the screen so a later compare is cheap."""
    try:
        image = ImageGrab.grab()
    except Exception:
        return None
    return _grid_from_image(image, GRID_W, GRID_H)


def capture_region(frame: Any) -> Grid | None:
    """Downsample only the number frame. Falls back to the full screen."""
    size = current_screen_size()
    box = scale_frame(frame, size[0], size[1])
    if box is None:
        return capture_grid()
    left = int(box["x"])
    top = int(box["y"])
    right = left + int(box["w"])
    bottom = top + int(box["h"])
    try:
        image = ImageGrab.grab(bbox=(left, top, right, bottom))
    except Exception:
        try:
            full = ImageGrab.grab()
        except Exception:
            return None
        image = full.crop((left, top, min(full.size[0], right), min(full.size[1], bottom)))
    return _grid_from_image(image, FRAME_GRID_W, FRAME_GRID_H)


def capture_watch(frame: Any = None) -> Grid | None:
    """Prefer the 5-digit frame; otherwise the whole screen."""
    if sanitize_frame(frame) is None:
        return capture_grid()
    return capture_region(frame)


def watch_changed(before: Grid | None, after: Grid | None, threshold: int = LANE_PAUSE_THRESHOLD) -> tuple[bool, float]:
    """True when the watched frame changed at least `threshold` percent."""
    return should_recover(before, after, threshold)


def _grid_from_image(image: Image.Image, width: int, height: int) -> Grid | None:
    if image.size[0] < 2 or image.size[1] < 2:
        return None
    if image.mode != "RGB":
        image = image.convert("RGB")
    small = image.resize((width, height), Image.Resampling.BOX)
    pixels = _pixels(small)
    if len(pixels) != width * height:
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


def recover_after_enter(
    before: Grid | None,
    after: Grid | None,
    threshold: int,
    clicks: Any,
    *,
    forced: bool = False,
) -> tuple[bool, float, list[dict[str, Any]]]:
    """Start's second script: after Enter, run the four left-clicks if the screen changed enough."""
    percent = change_percent(before, after)
    changed = bool(forced) or percent >= normalize_threshold(threshold)
    if not changed:
        return False, percent, []
    ready = sanitize_clicks(clicks)
    if len(ready) < RECOVER_CLICKS:
        return True, percent, []
    return True, percent, ready


def start_both_scripts_status(clicks: Any, threshold: int = DEFAULT_THRESHOLD) -> str:
    """What Start will do: type+Enter, and auto 4 clicks when the screen flips."""
    bar = normalize_threshold(threshold)
    if len(sanitize_clicks(clicks)) >= RECOVER_CLICKS:
        return f"Bắt đầu cả hai kịch bản: gõ mã + Enter; màn đổi ≥{bar}% thì tự bấm 4 nút trái."
    return f"Bắt đầu gõ mã + Enter. Chưa đủ 4 nút — khi màn đổi ≥{bar}% sẽ chưa bấm được."


def capture_patch(x: int, y: int, size: int = PATCH_SIZE) -> Grid | None:
    """Small crop around a click, used to see whether the press landed."""
    left = max(0, int(x) - size // 2)
    top = max(0, int(y) - size // 2)
    box = (left, top, left + size, top + size)
    try:
        image = ImageGrab.grab(bbox=box)
    except Exception:
        try:
            full = ImageGrab.grab()
        except Exception:
            return None
        image = full.crop((left, top, min(full.size[0], left + size), min(full.size[1], top + size)))
    if image.size[0] < 2 or image.size[1] < 2:
        return None
    if image.mode != "RGB":
        image = image.convert("RGB")
    small = image.resize((PATCH_GRID, PATCH_GRID), Image.Resampling.BOX)
    pixels = _pixels(small)
    if len(pixels) != PATCH_GRID * PATCH_GRID:
        return None
    return pixels


def click_landed(before: Grid | None, after: Grid | None, threshold: int = CLICK_LAND_PERCENT) -> tuple[bool, float]:
    """True when the patch around the click changed enough to count as received."""
    if before is None or after is None:
        return True, 0.0
    percent = change_percent(before, after)
    return percent >= max(1, threshold), percent


def play_click_until_landed(
    send: Any,
    sleep: Any,
    click: dict[str, Any],
    *,
    retries: int = CLICK_RETRIES,
    index: int = 0,
    cancelled: Any = None,
    capture: Any = None,
    compile_events: Any = None,
) -> dict[str, Any]:
    """Click a named point; if the computer did not take it, click that same name again."""
    take = capture or capture_patch
    to_events = compile_events or (lambda step: compile_script([step])[1])
    name = click_name(click, index)
    attempts = 0
    limit = max(0, int(retries))
    last_percent = 0.0
    while attempts <= limit:
        if cancelled and cancelled():
            return {"landed": False, "attempts": attempts, "name": name, "percent": last_percent}
        before = take(int(click["x"]), int(click["y"]))
        try:
            for event in to_events(click):
                if cancelled and cancelled():
                    return {"landed": False, "attempts": attempts, "name": name, "percent": last_percent}
                send(event)
        except Exception:
            attempts += 1
            continue
        sleep(0.28)
        after = take(int(click["x"]), int(click["y"]))
        attempts += 1
        landed, last_percent = click_landed(before, after)
        if landed:
            return {"landed": True, "attempts": attempts, "name": name, "percent": last_percent}
    return {"landed": False, "attempts": attempts, "name": name, "percent": last_percent}


def play_named_clicks(
    clicks: Any,
    send: Any,
    sleep: Any,
    *,
    retries: int = CLICK_RETRIES,
    cancelled: Any = None,
    capture: Any = None,
    compile_events: Any = None,
) -> list[dict[str, Any]]:
    """Play the four recover clicks, retrying any named point that did not land."""
    results: list[dict[str, Any]] = []
    for index, click in enumerate(sanitize_clicks(clicks)):
        try:
            results.append(
                play_click_until_landed(
                    send,
                    sleep,
                    click,
                    retries=retries,
                    index=index,
                    cancelled=cancelled,
                    capture=capture,
                    compile_events=compile_events,
                )
            )
        except Exception:
            results.append({"landed": False, "attempts": 0, "name": click_name(click, index), "percent": 0.0})
        if cancelled and cancelled():
            break
    return results


def make_grid(fill: tuple[int, int, int], *, flip: int = 0, color: tuple[int, int, int] = (255, 0, 0)) -> Grid:
    """Build a test grid. `flip` is how many leading cells use `color`."""
    cells: list[tuple[int, int, int]] = []
    total = GRID_W * GRID_H
    changed = max(0, min(total, flip))
    for index in range(total):
        cells.append(color if index < changed else fill)
    return tuple(cells)


def _pixels(image: Image.Image) -> Grid:
    """Read RGB cells without Image.getdata(), which Pillow now warns about."""
    width, height = image.size
    loaded = image.load()
    cells: list[tuple[int, int, int]] = []
    for top in range(height):
        for left in range(width):
            pixel = loaded[left, top]
            cells.append((int(pixel[0]), int(pixel[1]), int(pixel[2])))
    return tuple(cells)
