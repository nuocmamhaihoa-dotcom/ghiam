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


_STEP_KINDS = {"tap", "focus", "type", "key", "swipe"}


def events_to_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse a recording into steps a person can add, delete, and edit."""
    steps: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("kind")
        if kind == "value":
            _absorb_text(steps, event)
        elif kind == "key":
            if event.get("redacted") or not _printable_key(event):
                steps.append(_key_step(event))
        elif kind == "pointer" and event.get("phase") == "up":
            step = _pointer_step(event)
            if step is not None:
                steps.append(step)
    for step in steps:
        step["caption"] = step_caption(step)
    return steps


def steps_to_events(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn edited steps back into replayable events."""
    events: list[dict[str, Any]] = []
    clock = 0
    for step in sanitize_steps(steps):
        clock += 100
        kind = step["kind"]
        if kind == "type":
            events.append(_pointer_event(step, clock, "focus"))
            clock += 100
            value_event: dict[str, Any] = {
                "t": clock,
                "kind": "value",
                "target": step["target"],
                "value": step.get("value") or "",
            }
            _copy_signature(step, value_event)
            events.append(value_event)
        elif kind == "key":
            key_event: dict[str, Any] = {"t": clock, "kind": "key", "target": step["target"]}
            if step.get("redacted"):
                key_event["redacted"] = True
            else:
                key_event["key"] = step["key"]
                for flag in ("shift", "ctrl", "alt", "meta"):
                    if step.get(flag):
                        key_event[flag] = True
            events.append(key_event)
        else:
            events.append(_pointer_event(step, clock, kind))
    return events


def sanitize_steps(raw: list[Any]) -> list[dict[str, Any]]:
    """Keep only steps that can be shown and replayed."""
    cleaned: list[dict[str, Any]] = []
    for item in raw[:400]:
        step = _one_step(item)
        if step is not None:
            cleaned.append(step)
    return cleaned


def splice_steps(
    steps: list[dict[str, Any]],
    start: int,
    end: int,
    replacement: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace the inclusive range with another list of steps."""
    if start < 0 or end < start or end >= len(steps):
        raise ValueError("step range is outside the script")
    return [*steps[:start], *replacement, *steps[end + 1 :]]


def step_caption(step: dict[str, Any]) -> str:
    """One detailed line for a step in the editor."""
    name = str(step.get("label") or step.get("target") or "")
    touch = step.get("pointerType", "touch") != "mouse"
    kind = step.get("kind")
    if kind == "type":
        text = f"Gõ vào {name}: {step.get('value') or ''}"
    elif kind == "key":
        if step.get("redacted"):
            text = f"Bấm phím trong ô mật khẩu {name} (không lưu ký tự)"
        else:
            text = f"Phím {step.get('key') or ''} tại {name}"
    elif kind == "swipe":
        text = f"Vuốt tại {name}"
    else:
        if kind == "focus":
            verb = "Chạm vào ô" if touch else "Bấm vào ô"
        else:
            verb = "Chạm" if touch else "Bấm"
        if step.get("snapped"):
            text = f"{verb} lệch, hiểu là {name}"
        else:
            text = f"{verb} {name}"
    notes: list[str] = []
    if step.get("valueBlank"):
        notes.append(f"chỗ trống chữ «{step['valueBlank']}»")
    if step.get("targetBlank"):
        notes.append(f"chỗ trống control «{step['targetBlank']}»")
    if notes:
        return text + " · " + ", ".join(notes)
    return text


def blanks_of(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Named holes in a clip. The same name is filled once for every step that uses it."""
    found: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for step in sanitize_steps(steps):
        for kind, field in (("text", "valueBlank"), ("control", "targetBlank")):
            name = step.get(field)
            if not name:
                continue
            current = found.get(str(name))
            if current and current["kind"] != kind:
                raise ValueError("blank used for text and control")
            if current:
                continue
            item: dict[str, Any] = {"name": name, "kind": kind}
            if kind == "text":
                item["sample"] = step.get("value") or ""
            else:
                sample: dict[str, Any] = {"target": step.get("target") or ""}
                for key in ("label", "role", "hint", "index", "pointerType"):
                    if step.get(key) not in (None, ""):
                        sample[key] = step[key]
                item["sample"] = sample
            found[str(name)] = item
            order.append(str(name))
    return [found[name] for name in order]


def prepare_clip_steps(raw: list[Any]) -> list[dict[str, Any]]:
    """Sanitize clip steps and reject a blank name used as both text and control."""
    steps = sanitize_steps(raw)
    blanks_of(steps)
    return steps


def apply_fills(steps: list[dict[str, Any]], fills: dict[str, Any]) -> list[dict[str, Any]]:
    """Overlay one scenario's answers onto a clip. Missing answers keep the sample."""
    safe = fills if isinstance(fills, dict) else {}
    edited: list[dict[str, Any]] = []
    for source in sanitize_steps(steps):
        step = dict(source)
        value_name = step.get("valueBlank")
        if value_name and isinstance(safe.get(value_name), str):
            step["value"] = safe[value_name][:500]
        target_name = step.get("targetBlank")
        control = _control_fill(safe.get(target_name)) if target_name else None
        if control:
            for key in ("label", "role", "hint", "index", "pointerType", "snapped"):
                step.pop(key, None)
            step.update(control)
        edited.append(step)
    return sanitize_steps(edited)


def resolve_scenario(clips: dict[int, list[dict[str, Any]]], parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join named clips in order, using the live clip body plus each part's fills."""
    merged: list[dict[str, Any]] = []
    for part in parts:
        try:
            clip_id = int(part.get("clipId") or 0)
        except (TypeError, ValueError) as exc:
            raise KeyError(0) from exc
        steps = clips.get(clip_id)
        if steps is None:
            raise KeyError(clip_id)
        merged.extend(apply_fills(steps, part.get("fills") or {}))
    return merged


def sanitize_parts(raw: list[Any]) -> list[dict[str, Any]]:
    """Keep scenario parts that point at a clip and carry only safe fills."""
    parts: list[dict[str, Any]] = []
    for item in raw[:40]:
        if not isinstance(item, dict):
            continue
        try:
            clip_id = int(item.get("clipId") or 0)
        except (TypeError, ValueError):
            continue
        if clip_id <= 0:
            continue
        fills = item.get("fills") if isinstance(item.get("fills"), dict) else {}
        parts.append({"clipId": clip_id, "fills": _sanitize_fills(fills)})
    return parts


def _blank_name(value: Any) -> str:
    return _label(value)[:40]


def _control_fill(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    target = str(value.get("target") or "")
    if target != "body" and _TARGET.match(target) is None:
        return None
    fill: dict[str, Any] = {"target": target}
    label = _label(value.get("label"))
    if label:
        fill["label"] = label
    role = str(value.get("role") or "")
    if role in _ROLES:
        fill["role"] = role
    hint = _label(value.get("hint"))
    if hint:
        fill["hint"] = hint
    if value.get("index") is not None:
        try:
            index = int(value.get("index"))
        except (TypeError, ValueError):
            index = -1
        if 0 <= index <= 40:
            fill["index"] = index
    pointer_type = str(value.get("pointerType") or "")
    if pointer_type in _POINTER_TYPES:
        fill["pointerType"] = pointer_type
    return fill


def _sanitize_fills(fills: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in list(fills.items())[:40]:
        name = _blank_name(key)
        if not name:
            continue
        if isinstance(value, str):
            cleaned[name] = value[:500]
        else:
            control = _control_fill(value)
            if control:
                cleaned[name] = control
    return cleaned


def _absorb_text(steps: list[dict[str, Any]], event: dict[str, Any]) -> None:
    target = str(event.get("target") or "")
    if steps and steps[-1].get("kind") == "type" and steps[-1].get("target") == target:
        steps[-1]["value"] = str(event.get("value") or "")
        return
    if steps and steps[-1].get("kind") == "focus" and steps[-1].get("target") == target:
        steps[-1]["kind"] = "type"
        steps[-1]["value"] = str(event.get("value") or "")
        _copy_signature(event, steps[-1])
        return
    step: dict[str, Any] = {"kind": "type", "target": target, "value": str(event.get("value") or "")}
    _copy_signature(event, step)
    steps.append(step)


def _printable_key(event: dict[str, Any]) -> bool:
    key = str(event.get("key") or "")
    return len(key) == 1 and not event.get("ctrl") and not event.get("meta") and not event.get("alt")


def _key_step(event: dict[str, Any]) -> dict[str, Any]:
    step: dict[str, Any] = {"kind": "key", "target": str(event.get("target") or "")}
    if event.get("redacted"):
        step["redacted"] = True
        return step
    step["key"] = str(event.get("key") or "")
    for flag in ("shift", "ctrl", "alt", "meta"):
        if event.get(flag):
            step[flag] = True
    _copy_signature(event, step)
    return step


def _pointer_step(event: dict[str, Any]) -> dict[str, Any] | None:
    intent = str(event.get("intent") or "")
    if intent == "swipe":
        kind = "swipe"
    elif intent == "focus":
        kind = "focus"
    elif intent == "tap" or event.get("click"):
        kind = "tap"
    else:
        return None
    step: dict[str, Any] = {
        "kind": kind,
        "target": str(event.get("target") or ""),
        "pointerType": str(event.get("pointerType") or "touch"),
    }
    if event.get("snapped"):
        step["snapped"] = True
    _copy_signature(event, step)
    return step


def _copy_signature(source: dict[str, Any], dest: dict[str, Any]) -> None:
    for key in ("label", "role", "hint"):
        if source.get(key):
            dest[key] = source[key]
    if source.get("index") is not None and "index" not in dest:
        dest["index"] = source["index"]
    if source.get("pointerType") and "pointerType" not in dest:
        dest["pointerType"] = source["pointerType"]


def _pointer_event(step: dict[str, Any], clock: int, intent: str) -> dict[str, Any]:
    event: dict[str, Any] = {
        "t": clock,
        "kind": "pointer",
        "phase": "up",
        "target": step["target"],
        "pointerType": step.get("pointerType") or "touch",
        "click": intent in {"tap", "focus"},
        "intent": intent,
    }
    _copy_signature(step, event)
    if step.get("snapped"):
        event["snapped"] = True
    return event


def _one_step(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    target = str(item.get("target") or "")
    if kind not in _STEP_KINDS:
        return None
    if target != "body" and _TARGET.match(target) is None:
        return None
    step: dict[str, Any] = {"kind": kind, "target": target}
    pointer_type = str(item.get("pointerType") or "")
    if pointer_type in _POINTER_TYPES:
        step["pointerType"] = pointer_type
    label = _label(item.get("label"))
    if label:
        step["label"] = label
    role = str(item.get("role") or "")
    if role in _ROLES:
        step["role"] = role
    hint = _label(item.get("hint"))
    if hint:
        step["hint"] = hint
    if item.get("index") is not None:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            index = -1
        if 0 <= index <= 40:
            step["index"] = index
    if item.get("snapped"):
        step["snapped"] = True
    if kind == "type" and target in _SECRET_TARGETS:
        step["kind"] = "key"
        step["redacted"] = True
        step["caption"] = step_caption(step)
        return step
    if kind == "type":
        step["value"] = str(item.get("value") or "")[:500]
        value_blank = _blank_name(item.get("valueBlank"))
        if value_blank:
            step["valueBlank"] = value_blank
    elif kind == "key":
        if item.get("redacted"):
            step["redacted"] = True
        else:
            key = str(item.get("key") or "")[:32]
            if not key:
                return None
            step["key"] = key
            for flag in ("shift", "ctrl", "alt", "meta"):
                if item.get(flag):
                    step[flag] = True
    target_blank = _blank_name(item.get("targetBlank"))
    if target_blank:
        step["targetBlank"] = target_blank
    step["caption"] = step_caption(step)
    return step
