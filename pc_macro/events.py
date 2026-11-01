"""Validate desktop input events so a recording can be replayed on this computer."""

from __future__ import annotations

import re
from typing import Any, Iterator

_KINDS = {"key", "click", "scroll"}
_ACTIONS = {"down", "up"}
_BUTTONS = {"left", "right", "middle"}
_STOP_KEYS = {"f8"}
_MAX_EVENTS = 8000
_MAX_T_MS = 3_600_000
_MAX_GAP_MS = 2000
_MAX_COORD = 100_000
_KEY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def sanitize_events(raw: list[Any]) -> list[dict[str, Any]]:
    """Keep only replayable desktop events. Drop the stop hotkey."""
    cleaned: list[dict[str, Any]] = []
    for item in raw[:_MAX_EVENTS]:
        event = _one(item)
        if event is not None:
            cleaned.append(event)
    return cleaned


def script_lines(events: list[dict[str, Any]]) -> list[str]:
    """Human steps. Consecutive printable keys collapse into one typed string."""
    lines: list[str] = []
    typed = ""
    for event in events:
        kind = event.get("kind")
        if kind == "key" and _printable(event.get("key")):
            if event.get("action") == "down":
                typed += str(event.get("key") or "")
            continue
        if typed:
            lines.append(f"Gõ {typed}")
            typed = ""
        line = _line(event)
        if line:
            lines.append(line)
    if typed:
        lines.append(f"Gõ {typed}")
    return lines


def default_title(events: list[dict[str, Any]]) -> str:
    keys = 0
    clicks = 0
    for event in events:
        if event.get("kind") == "key" and event.get("action") == "down":
            keys += 1
        if event.get("kind") == "click" and event.get("action") == "up":
            clicks += 1
    parts: list[str] = []
    if keys:
        parts.append(f"{keys} phím")
    if clicks:
        parts.append(f"{clicks} lần bấm")
    return " · ".join(parts) or "Bản ghi thao tác"


def schedule(events: list[dict[str, Any]], speed: float = 1.0) -> Iterator[tuple[float, dict[str, Any]]]:
    """Yield (wait_ms, event) using recorded timing, with long gaps shortened."""
    pace = speed if speed > 0 else 1.0
    previous = 0
    for event in events:
        elapsed = int(event["t"])
        gap = max(0, elapsed - previous)
        wait = min(_MAX_GAP_MS, gap) / pace
        yield wait, event
        previous = elapsed


def point_in_box(x: float, y: float, box: tuple[float, float, float, float], pad: float = 8) -> bool:
    """True when a screen point sits on the recorder window (plus a small border)."""
    left, top, right, bottom = box
    return (left - pad) <= x <= (right + pad) and (top - pad) <= y <= (bottom + pad)


def _one(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    if kind not in _KINDS:
        return None
    try:
        elapsed = int(item.get("t", 0))
    except (TypeError, ValueError):
        return None
    if elapsed < 0 or elapsed > _MAX_T_MS:
        return None
    event: dict[str, Any] = {"t": elapsed, "kind": kind}
    if kind == "key":
        return _key(event, item)
    if kind == "click":
        return _click(event, item)
    if kind == "scroll":
        return _scroll(event, item)
    return None


def _key(event: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    action = str(item.get("action") or "down")
    if action not in _ACTIONS:
        return None
    key = _key_name(item.get("key"))
    if key is None or key in _STOP_KEYS:
        return None
    event["action"] = action
    event["key"] = key
    return event


def _click(event: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    action = str(item.get("action") or "up")
    if action not in _ACTIONS:
        return None
    button = str(item.get("button") or "left")
    if button not in _BUTTONS:
        return None
    point = _point(item)
    if point is None:
        return None
    event["action"] = action
    event["button"] = button
    event["x"] = point[0]
    event["y"] = point[1]
    return event


def _scroll(event: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    point = _point(item)
    if point is None:
        return None
    try:
        dx = int(item.get("dx", 0))
        dy = int(item.get("dy", 0))
    except (TypeError, ValueError):
        return None
    if dx == 0 and dy == 0:
        return None
    event["x"] = point[0]
    event["y"] = point[1]
    event["dx"] = max(-20, min(20, dx))
    event["dy"] = max(-20, min(20, dy))
    return event


def _point(item: dict[str, Any]) -> tuple[int, int] | None:
    try:
        x = int(round(float(item.get("x"))))
        y = int(round(float(item.get("y"))))
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= _MAX_COORD and 0 <= y <= _MAX_COORD):
        return None
    return x, y


def _key_name(value: Any) -> str | None:
    text = str(value or "")
    if len(text) == 1 and text.isprintable() and not text.isspace():
        return text
    lowered = text.strip().lower()
    if lowered == "escape":
        lowered = "esc"
    if _KEY_NAME.match(lowered) is None:
        return None
    return lowered


def _printable(key: Any) -> bool:
    text = str(key or "")
    return len(text) == 1 and text.isprintable() and not text.isspace()


def _line(event: dict[str, Any]) -> str | None:
    kind = event.get("kind")
    if kind == "key" and event.get("action") == "down":
        return f"Phím {event.get('key')}"
    if kind == "click" and event.get("action") == "up":
        label = {"left": "trái", "right": "phải", "middle": "giữa"}.get(str(event.get("button")), "trái")
        return f"Bấm {label} tại {event.get('x')},{event.get('y')}"
    if kind == "scroll":
        return f"Cuộn tại {event.get('x')},{event.get('y')}"
    return None
