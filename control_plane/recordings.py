"""Validate keyboard and pointer recordings so they can be replayed exactly."""

from __future__ import annotations

import re
from typing import Any

_TARGET = re.compile(r"^#[A-Za-z][A-Za-z0-9_-]{0,40}$")
_KINDS = {"key", "pointer", "value"}
_PHASES = {"down", "move", "up"}
_POINTER_TYPES = {"mouse", "touch", "pen"}
_INTENTS = {"tap", "focus", "swipe"}
_ROLES = {"button", "link", "textbox", "search", "select", "checkbox", "radio"}
_SECRET_TARGETS = {"#token"}
_MAX_EVENTS = 3000
_AMBIGUOUS_GAP = 15
# A finger can miss a control by this many CSS pixels and still mean that control.
TOUCH_SLOP_PX = 36


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
        if kind == "pointer":
            line = _pointer_line(event)
            if line:
                lines.append(line)
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
        _signature(event, item)
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
    _signature(event, item)
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
    _intent(event, item)
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


def _intent(event: dict[str, Any], item: dict[str, Any]) -> None:
    """Keep what the gesture meant: tap a control, focus a field, or swipe."""
    if event.get("phase") != "up":
        return
    intent = str(item.get("intent") or "")
    if intent not in _INTENTS:
        return
    event["intent"] = intent
    if intent == "swipe":
        event["click"] = False
        return
    label = _label(item.get("label"))
    if label:
        event["label"] = label
    if item.get("snapped"):
        event["snapped"] = True
    _signature(event, item)


def _signature(event: dict[str, Any], item: dict[str, Any]) -> None:
    """Role, nearby text, and position let replay find a control after its id changes."""
    role = str(item.get("role") or "")
    if role in _ROLES:
        event["role"] = role
    hint = _label(item.get("hint"))
    if hint:
        event["hint"] = hint
    if item.get("index") is None:
        return
    try:
        index = int(item.get("index"))
    except (TypeError, ValueError):
        return
    if 0 <= index <= 40:
        event["index"] = index


def score_match(wanted: dict[str, Any], candidate: dict[str, Any]) -> int:
    """How well a live control matches a recorded gesture. Higher is closer."""
    score = 0
    target = str(wanted.get("target") or "")
    if target and target != "body" and target == candidate.get("target"):
        score += 50
    label = str(wanted.get("label") or "")
    candidate_label = str(candidate.get("label") or "")
    if label and label == candidate_label:
        score += 40
    elif len(label) >= 3 and label in candidate_label:
        score += 15
    role = str(wanted.get("role") or "")
    if role and role == candidate.get("role"):
        score += 20
    hint = str(wanted.get("hint") or "")
    if hint and hint == candidate.get("hint"):
        score += 15
    index = wanted.get("index")
    if isinstance(index, int) and index == candidate.get("index") and role and role == candidate.get("role"):
        score += 10
    return score


def pick_match(wanted: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the best control. A close second is ambiguous and must be asked."""
    ranked = sorted(
        ((score_match(wanted, candidate), position, candidate) for position, candidate in enumerate(candidates)),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = [item for item in ranked if item[0] > 0]
    if not ranked:
        return {"choice": None, "alternatives": [], "ambiguous": False}
    best = ranked[0][0]
    alternatives = [item[2] for item in ranked[1:] if best - item[0] < _AMBIGUOUS_GAP][:2]
    return {"choice": ranked[0][2], "alternatives": alternatives, "ambiguous": bool(alternatives)}


def _label(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:80]


def _pointer_line(event: dict[str, Any]) -> str | None:
    intent = event.get("intent")
    if intent == "swipe" or not (event.get("click") or intent in {"tap", "focus"}):
        return None
    touch = event.get("pointerType") == "touch"
    name = str(event.get("label") or event.get("target") or "")
    if intent == "focus":
        verb = "Chạm vào ô" if touch else "Bấm vào ô"
    else:
        verb = "Chạm" if touch else "Bấm"
    if event.get("snapped"):
        return f"{verb} lệch, hiểu là {name}"
    return f"{verb} {name}"
