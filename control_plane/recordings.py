"""Validate keyboard and pointer recordings so they can be replayed exactly."""

from __future__ import annotations

import re
from typing import Any

_TARGET = re.compile(r"^#[A-Za-z][A-Za-z0-9_-]{0,40}$")
_KINDS = {"key", "pointer", "value"}
_PHASES = {"down", "move", "up"}
_POINTER_TYPES = {"mouse", "touch", "pen"}
_SECRET_TARGETS = {"#token"}
_MAX_EVENTS = 3000


def sanitize_events(raw: list[Any]) -> list[dict[str, Any]]:
    """Keep only replayable events. Never store password keystrokes or values."""
    cleaned: list[dict[str, Any]] = []
    for item in raw[:_MAX_EVENTS]:
        event = _one(item)
        if event is not None:
            cleaned.append(event)
    return cleaned


def script_lines(events: list[dict[str, Any]]) -> list[str]:
    """Human steps. Consecutive typing into the same field keeps the latest text."""
    lines: list[str] = []
    for event in events:
        kind = event.get("kind")
        target = str(event.get("target") or "")
        if kind == "value":
            text = f"Gõ vào {target}: {event.get('value') or ''}"
            prefix = f"Gõ vào {target}:"
            if lines and lines[-1].startswith(prefix):
                lines[-1] = text
            else:
                lines.append(text)
            continue
        if kind == "key" and event.get("redacted"):
            note = f"Bấm phím trong ô mật khẩu {target} (không lưu ký tự)"
            if not lines or lines[-1] != note:
                lines.append(note)
            continue
        if kind == "key":
            key = str(event.get("key") or "")
            printable = len(key) == 1 and not event.get("ctrl") and not event.get("meta") and not event.get("alt")
            if printable:
                continue
            lines.append(f"Phím {key} tại {target}")
            continue
        if kind == "pointer" and event.get("click"):
            verb = "Chạm" if event.get("pointerType") == "touch" else "Bấm"
            lines.append(f"{verb} {target}")
    return lines


def default_title(events: list[dict[str, Any]]) -> str:
    clicks = 0
    touches = 0
    keys = 0
    for event in events:
        if event.get("kind") == "key" and not event.get("redacted"):
            keys += 1
        if event.get("kind") == "pointer" and event.get("phase") == "up":
            if event.get("pointerType") == "touch":
                touches += 1
            elif event.get("click"):
                clicks += 1
    parts: list[str] = []
    if clicks:
        parts.append(f"{clicks} lần bấm")
    if touches:
        parts.append(f"{touches} lần chạm")
    if keys:
        parts.append(f"{keys} phím")
    return " · ".join(parts) or "Bản ghi thao tác"


def _one(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    target = str(item.get("target") or "")
    if kind not in _KINDS:
        return None
    if target != "body" and _TARGET.match(target) is None:
        return None
    try:
        elapsed = int(item.get("t", 0))
    except (TypeError, ValueError):
        return None
    if elapsed < 0 or elapsed > 3_600_000:
        return None
    secret = bool(item.get("redacted")) or target in _SECRET_TARGETS
    event: dict[str, Any] = {"t": elapsed, "kind": kind, "target": target}
    if kind == "key":
        return _key(event, item, secret)
    if kind == "value":
        if secret:
            return None
        event["value"] = str(item.get("value") or "")[:500]
        return event
    return _pointer(event, item)


def _key(event: dict[str, Any], item: dict[str, Any], secret: bool) -> dict[str, Any] | None:
    if secret:
        event["redacted"] = True
        return event
    key = str(item.get("key") or "")[:32]
    if not key:
        return None
    event["key"] = key
    code = item.get("code")
    if code:
        event["code"] = str(code)[:32]
    for flag in ("shift", "ctrl", "alt", "meta"):
        if item.get(flag):
            event[flag] = True
    return event


def _pointer(event: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    phase = str(item.get("phase") or "up")
    if phase not in _PHASES:
        return None
    pointer_type = str(item.get("pointerType") or "mouse")
    if pointer_type not in _POINTER_TYPES:
        pointer_type = "mouse"
    event["phase"] = phase
    event["pointerType"] = pointer_type
    event["click"] = bool(item.get("click")) and phase == "up"
    for axis in ("x", "y"):
        if item.get(axis) is None:
            continue
        try:
            event[axis] = round(float(item[axis]), 1)
        except (TypeError, ValueError):
            continue
    for axis in ("nx", "ny"):
        if item.get(axis) is None:
            continue
        try:
            fraction = float(item[axis])
        except (TypeError, ValueError):
            continue
        if 0 <= fraction <= 1:
            event[axis] = round(fraction, 4)
    return event
