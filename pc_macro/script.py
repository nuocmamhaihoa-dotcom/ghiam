"""Collapse a desktop recording into editable script steps, then compile it back."""

from __future__ import annotations

from typing import Any

from pc_macro.events import sanitize_events

_STEP_KINDS = {"type", "number", "key", "click", "scroll", "wait"}
_BUTTONS = {"left", "right", "middle"}
_MAX_STEPS = 400
_MAX_WAIT = 30_000
_MAX_TEXT = 500
_MAX_COORD = 100_000
_STEP_GAP = 80
_CHAR_GAP = 30
_FOCUS_WAIT = 220
_CLEAR_KEYS = 12
_TYPE_SETTLE = 80
_ENTER_WAIT = 180


def events_to_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One human step per typed run, special key, click, or scroll."""
    cleaned = sanitize_events(events)
    steps: list[dict[str, Any]] = []
    index = 0
    last_t = 0
    while index < len(cleaned):
        event = cleaned[index]
        wait = _wait(event.get("t"), last_t)
        kind = event.get("kind")
        if kind == "key" and event.get("action") == "down":
            key = str(event.get("key") or "")
            if _char_key(key):
                text, index, last_t = _collect_text(cleaned, index)
                step_kind = "number" if text.isdigit() else "type"
                _append(steps, {"kind": step_kind, "value": text, "wait": wait})
                continue
            _append(steps, {"kind": "key", "key": key, "wait": wait})
            index += 1
            if index < len(cleaned) and _same_key_up(cleaned[index], key):
                last_t = int(cleaned[index]["t"])
                index += 1
            else:
                last_t = int(event["t"])
            continue
        if kind == "click":
            click, index, last_t = _collect_click(cleaned, index)
            if click is not None:
                click["wait"] = wait
                _append(steps, click)
            continue
        if kind == "scroll":
            _append(
                steps,
                {
                    "kind": "scroll",
                    "x": int(event["x"]),
                    "y": int(event["y"]),
                    "dx": int(event.get("dx") or 0),
                    "dy": int(event.get("dy") or 0),
                    "wait": wait,
                },
            )
            last_t = int(event["t"])
            index += 1
            continue
        index += 1
    return sanitize_steps(steps)


def steps_to_events(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn edited steps into replayable desktop events."""
    events: list[dict[str, Any]] = []
    clock = 0
    for step in sanitize_steps(steps):
        clock += max(0, int(step.get("wait") or 0))
        kind = step["kind"]
        if kind == "wait":
            continue
        if kind in {"type", "number"}:
            text = str(step.get("value") or "")
            for char in text:
                key = _typed_key(char)
                if key is None:
                    continue
                events.append({"t": clock, "kind": "key", "action": "down", "key": key})
                clock += _CHAR_GAP
                events.append({"t": clock, "kind": "key", "action": "up", "key": key})
                clock += _CHAR_GAP
            continue
        if kind == "key":
            key = str(step.get("key") or "")
            events.append({"t": clock, "kind": "key", "action": "down", "key": key})
            clock += _CHAR_GAP
            events.append({"t": clock, "kind": "key", "action": "up", "key": key})
            clock += _STEP_GAP
            continue
        if kind == "click":
            point = {"x": int(step["x"]), "y": int(step["y"]), "button": step.get("button") or "left"}
            events.append({"t": clock, "kind": "click", "action": "down", **point})
            clock += 40
            events.append({"t": clock, "kind": "click", "action": "up", **point})
            clock += _STEP_GAP
            continue
        if kind == "scroll":
            events.append(
                {
                    "t": clock,
                    "kind": "scroll",
                    "x": int(step["x"]),
                    "y": int(step["y"]),
                    "dx": int(step.get("dx") or 0),
                    "dy": int(step.get("dy") or 0),
                }
            )
            clock += _STEP_GAP
            continue
        # sanitize_steps already dropped unknown kinds
    return sanitize_events(events)


def sanitize_steps(raw: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in raw[:_MAX_STEPS]:
        step = _one_step(item)
        if step is not None:
            cleaned.append(step)
    return cleaned


def script_captions(steps: list[dict[str, Any]]) -> list[str]:
    return [step.get("caption") or step_caption(step) for step in sanitize_steps(steps)]


def compile_script(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (clean steps, replayable events). Steps are the source of truth."""
    cleaned = sanitize_steps(steps)
    return cleaned, steps_to_events(cleaned)


def number_type_steps(sample: str = "1") -> list[dict[str, Any]]:
    """Type the current code only. Enter is sent after the watch-frame snapshot."""
    text = "".join(ch for ch in sample if ch.isalnum()) or "1"
    typed = new_step("type", value=text)
    return [step for step in (typed,) if step]


def enter_key_steps() -> list[dict[str, Any]]:
    enter = new_step("key", key="enter", wait=80)
    return [step for step in (enter,) if step]


def number_enter_steps(sample: str = "1") -> list[dict[str, Any]]:
    """Type the current code (5 fixed chars + 5 digits), then Enter."""
    return [*number_type_steps(sample), *enter_key_steps()]


def field_clear_steps(*, wait: int = 20) -> list[dict[str, Any]]:
    """Wipe leftover text so the next code replaces, not appends.

    Home + Delete clears from the start; End + Backspace clears from the end.
    Together they empty both left-to-right and right-to-left fields.
    """
    steps: list[dict[str, Any]] = []
    home = new_step("key", key="home", wait=wait)
    if home is not None:
        steps.append(home)
    for _index in range(_CLEAR_KEYS):
        forward = new_step("key", key="delete", wait=0)
        if forward is not None:
            steps.append(forward)
    end = new_step("key", key="end", wait=0)
    if end is not None:
        steps.append(end)
    for _index in range(_CLEAR_KEYS):
        wipe = new_step("key", key="backspace", wait=0)
        if wipe is not None:
            steps.append(wipe)
    return steps


def one_code_enter_steps(sample: str = "1", click: Any = None) -> list[dict[str, Any]]:
    """One increment: focus the chosen point, wipe, type the full code, Enter once."""
    steps: list[dict[str, Any]] = []
    steps.extend(click_focus_steps(click, wait=_FOCUS_WAIT))
    steps.extend(field_clear_steps(wait=40 if steps else 20))
    typed = number_type_steps(sample)
    if typed:
        first = dict(typed[0])
        first["wait"] = max(int(first.get("wait") or 0), _TYPE_SETTLE)
        typed = [first, *typed[1:]]
    steps.extend(typed)
    enter = new_step("key", key="enter", wait=_ENTER_WAIT)
    if enter is not None:
        steps.append(enter)
    return steps


def click_focus_step(click: Any, *, wait: int = 40) -> dict[str, Any] | None:
    """One left-click so that process's input gets the following keys."""
    if not isinstance(click, dict):
        return None
    try:
        x = int(click["x"])
        y = int(click["y"])
    except (TypeError, ValueError, KeyError):
        return None
    return new_step("click", x=x, y=y, button=str(click.get("button") or "left"), wait=wait)


def click_focus_steps(click: Any, *, wait: int = 40) -> list[dict[str, Any]]:
    """Bring the bound browser forward, then click the 5-digit field."""
    steps: list[dict[str, Any]] = []
    if not isinstance(click, dict):
        return steps
    try:
        field_x = int(click["x"])
        field_y = int(click["y"])
    except (TypeError, ValueError, KeyError):
        return steps
    try:
        window_x = int(click.get("wx"))
        window_y = int(click.get("wy"))
    except (TypeError, ValueError):
        window_x, window_y = -1, -1
    if window_x >= 0 and window_y >= 0 and (window_x, window_y) != (field_x, field_y):
        window = click_focus_step(
            {"x": window_x, "y": window_y, "button": click.get("button")},
            wait=wait,
        )
        if window is not None:
            steps.append(window)
        wait = max(int(wait or 0), 40)
    focus = click_focus_step(click, wait=wait)
    if focus is not None:
        steps.append(focus)
    return steps


def four_lane_type_steps(codes: list[str] | tuple[str, ...], clicks: Any = None) -> list[dict[str, Any]]:
    """Focus each bound field, clear it, then type that lane's code."""
    steps: list[dict[str, Any]] = []
    slots = list(clicks or [])
    for index, raw in enumerate(codes or []):
        focus_wait = _FOCUS_WAIT if index else 40
        if index < len(slots):
            steps.extend(click_focus_steps(slots[index], wait=focus_wait))
        steps.extend(field_clear_steps(wait=20 if index or steps else 0))
        typed = number_type_steps(str(raw))
        if typed and index and (index >= len(slots) or not isinstance(slots[index], dict)):
            first = dict(typed[0])
            first["wait"] = max(int(first.get("wait") or 0), 40)
            typed = [first, *typed[1:]]
        steps.extend(typed)
    return steps


def four_lane_enter_burst_steps(clicks: Any = None, count: int = 4) -> list[dict[str, Any]]:
    """Send Enter to every process after refocusing, with delays the OS can keep."""
    slots = list(clicks or [])
    total = max(1, int(count or 0))
    if slots:
        total = max(total, len(slots))
    steps: list[dict[str, Any]] = []
    for index in range(total):
        if index < len(slots):
            steps.extend(click_focus_steps(slots[index], wait=_FOCUS_WAIT))
        enter = enter_key_steps()
        if enter:
            first = dict(enter[0])
            first["wait"] = 40
            steps.append(first)
    return steps


def four_lane_enter_steps(codes: list[str] | tuple[str, ...], clicks: Any = None) -> list[dict[str, Any]]:
    """Hướng 2: type all four codes, then Enter all four together."""
    typed = four_lane_type_steps(codes, clicks)
    return [*typed, *four_lane_enter_burst_steps(clicks, count=len(list(codes or [])))]


def new_step(kind: str, **fields: Any) -> dict[str, Any] | None:
    """Build one valid step, filling defaults so the editor can insert a blank row."""
    base = dict(_DEFAULTS.get(kind) or {})
    if not base:
        return None
    for key, value in fields.items():
        if value is not None:
            base[key] = value
    return _one_step(base)


def step_caption(step: dict[str, Any]) -> str:
    kind = step.get("kind")
    wait = int(step.get("wait") or 0)
    prefix = f"Chờ {wait} ms · " if wait >= 200 and kind != "wait" else ""
    if kind == "type":
        text = f"Gõ {step.get('value') or ''}"
    elif kind == "number":
        text = f"Gõ số {step.get('value') or ''}"
    elif kind == "key":
        text = f"Phím {step.get('key') or ''}"
    elif kind == "click":
        label = {"left": "trái", "right": "phải", "middle": "giữa"}.get(str(step.get("button")), "trái")
        text = f"Bấm {label} tại {step.get('x')},{step.get('y')}"
    elif kind == "scroll":
        text = f"Cuộn tại {step.get('x')},{step.get('y')}"
    elif kind == "wait":
        text = f"Chờ {int(step.get('wait') or 0)} ms"
    else:
        text = "Bước"
    return prefix + text


def move_step(steps: list[dict[str, Any]], index: int, delta: int) -> list[dict[str, Any]]:
    """Swap one step up or down. Out-of-range moves leave the list unchanged."""
    cleaned = sanitize_steps(steps)
    dest = index + delta
    if index < 0 or index >= len(cleaned) or dest < 0 or dest >= len(cleaned):
        return cleaned
    cleaned[index], cleaned[dest] = cleaned[dest], cleaned[index]
    return cleaned


def insert_steps(steps: list[dict[str, Any]], at: int, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert more steps at `at`. Clamps to the ends of the script."""
    cleaned = sanitize_steps(steps)
    extra = sanitize_steps(incoming)
    point = max(0, min(at, len(cleaned)))
    return sanitize_steps([*cleaned[:point], *extra, *cleaned[point:]])


def replace_step(steps: list[dict[str, Any]], index: int, incoming: dict[str, Any]) -> list[dict[str, Any]]:
    cleaned = sanitize_steps(steps)
    if index < 0 or index >= len(cleaned):
        return cleaned
    fresh = _one_step(incoming)
    if fresh is None:
        return cleaned
    cleaned[index] = fresh
    return cleaned


def delete_step(steps: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    cleaned = sanitize_steps(steps)
    if index < 0 or index >= len(cleaned):
        return cleaned
    return [*cleaned[:index], *cleaned[index + 1 :]]


def merge_recording(
    script: list[dict[str, Any]],
    recorded: list[dict[str, Any]],
    *,
    appending: bool,
    at: int | None = None,
) -> list[dict[str, Any]]:
    """Turn a just-stopped recording into steps. Append after `at`, or replace."""
    incoming = events_to_steps(recorded)
    if appending:
        if not incoming:
            return sanitize_steps(script)
        point = len(sanitize_steps(script)) if at is None else at + 1
        return insert_steps(script, point, incoming)
    return incoming


def _collect_text(events: list[dict[str, Any]], start: int) -> tuple[str, int, int]:
    chars: list[str] = []
    index = start
    last_t = int(events[start]["t"])
    while index < len(events):
        event = events[index]
        if event.get("kind") != "key":
            break
        key = str(event.get("key") or "")
        if not _char_key(key):
            break
        if event.get("action") == "down":
            chars.append(key)
        last_t = int(event["t"])
        index += 1
    return "".join(chars), index, last_t


def _collect_click(
    events: list[dict[str, Any]], start: int
) -> tuple[dict[str, Any] | None, int, int]:
    event = events[start]
    last_t = int(event["t"])
    if event.get("action") == "down":
        index = start + 1
        while index < len(events):
            nxt = events[index]
            if nxt.get("kind") == "click" and nxt.get("action") == "up":
                last_t = int(nxt["t"])
                return _click_step(nxt), index + 1, last_t
            if nxt.get("kind") != "click":
                break
            index += 1
        return _click_step(event), start + 1, last_t
    if event.get("action") == "up":
        return _click_step(event), start + 1, last_t
    return None, start + 1, last_t


def _click_step(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "click",
        "button": str(event.get("button") or "left"),
        "x": int(event["x"]),
        "y": int(event["y"]),
    }


def _one_step(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    if kind not in _STEP_KINDS:
        return None
    try:
        wait = int(item.get("wait") or 0)
    except (TypeError, ValueError):
        wait = 0
    wait = max(0, min(_MAX_WAIT, wait))
    step: dict[str, Any] = {"kind": kind, "wait": wait}
    if kind in {"type", "number"}:
        value = str(item.get("value") or "")[:_MAX_TEXT]
        if kind == "number":
            value = "".join(ch for ch in value if ch.isdigit())
        if not value:
            return None
        if kind == "type" and value.isdigit():
            step["kind"] = "number"
        step["value"] = value
    elif kind == "key":
        key = str(item.get("key") or "").strip().lower()[:32]
        if not key:
            return None
        step["key"] = key
    elif kind == "click":
        point = _point(item)
        if point is None:
            return None
        button = str(item.get("button") or "left")
        if button not in _BUTTONS:
            button = "left"
        step["x"], step["y"] = point
        step["button"] = button
    elif kind == "scroll":
        point = _point(item)
        if point is None:
            return None
        step["x"], step["y"] = point
        try:
            step["dx"] = max(-20, min(20, int(item.get("dx") or 0)))
            step["dy"] = max(-20, min(20, int(item.get("dy") or 0)))
        except (TypeError, ValueError):
            return None
        if step["dx"] == 0 and step["dy"] == 0:
            return None
    elif kind == "wait":
        if wait <= 0:
            return None
    else:
        return None
    step["caption"] = step_caption(step)
    return step


def _append(steps: list[dict[str, Any]], raw: dict[str, Any]) -> None:
    step = _one_step(raw)
    if step is not None:
        steps.append(step)


def _typed_key(char: str) -> str | None:
    if char == " ":
        return "space"
    if char in {"\n", "\r"}:
        return "enter"
    if len(char) == 1 and char.isprintable() and not char.isspace():
        return char
    return None


_DEFAULTS: dict[str, dict[str, Any]] = {
    "type": {"kind": "type", "value": "text", "wait": 0},
    "number": {"kind": "number", "value": "1", "wait": 0},
    "key": {"kind": "key", "key": "enter", "wait": 0},
    "click": {"kind": "click", "x": 200, "y": 200, "button": "left", "wait": 0},
    "scroll": {"kind": "scroll", "x": 200, "y": 200, "dx": 0, "dy": -1, "wait": 0},
    "wait": {"kind": "wait", "wait": 300},
}


def _point(item: dict[str, Any]) -> tuple[int, int] | None:
    try:
        x = int(round(float(item.get("x"))))
        y = int(round(float(item.get("y"))))
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= _MAX_COORD and 0 <= y <= _MAX_COORD):
        return None
    return x, y


def _wait(current: Any, previous: int) -> int:
    try:
        elapsed = int(current)
    except (TypeError, ValueError):
        return 0
    return max(0, min(2000, elapsed - previous))


def _char_key(key: str) -> bool:
    return len(key) == 1 and key.isprintable() and not key.isspace()


def _same_key_up(event: dict[str, Any], key: str) -> bool:
    return event.get("kind") == "key" and event.get("action") == "up" and event.get("key") == key
