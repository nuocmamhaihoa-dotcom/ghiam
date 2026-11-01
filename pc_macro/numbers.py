"""Turn typed digits into a list the user can walk and increment."""

from __future__ import annotations

import re
from typing import Any

_SPLIT = re.compile(r"[\n,;\s]+")
_MAX_ITEMS = 500
_MAX_DIGITS = 40
PREFIX_LEN = 5
DIGIT_LEN = 5


def normalize_prefix(raw: str) -> str:
    """Keep up to 5 letters/digits. The user can change this block anytime."""
    return "".join(ch for ch in raw if ch.isalnum())[:PREFIX_LEN]


def normalize_digits(raw: Any, default: str = "00000") -> str:
    """Always 5 digits, padded on the left."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        digits = "".join(ch for ch in default if ch.isdigit()) or "0"
    value = int(digits)
    if value < 0:
        value = 0
    if value > 10**DIGIT_LEN - 1:
        value = 10**DIGIT_LEN - 1
    return str(value).zfill(DIGIT_LEN)


def make_code(prefix: str, digits: str) -> str:
    return f"{normalize_prefix(prefix)}{normalize_digits(digits)}"


def digit_spans(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find each run of digit keystrokes. end is an exclusive index."""
    spans: list[dict[str, Any]] = []
    index = 0
    while index < len(events):
        if not _digit_event(events[index]):
            index += 1
            continue
        start = index
        digits: list[str] = []
        while index < len(events) and _digit_event(events[index]):
            event = events[index]
            if event.get("action") == "down":
                digits.append(str(event.get("key") or ""))
            index += 1
        if digits:
            spans.append({"start": start, "end": index, "value": "".join(digits)})
    return spans


def parse_number_list(text: str) -> list[str]:
    """One number per line, or several on a line split by comma / space."""
    items: list[str] = []
    for part in _SPLIT.split(text or ""):
        digits = "".join(ch for ch in part if ch.isdigit())[:_MAX_DIGITS]
        if digits:
            items.append(digits)
        if len(items) >= _MAX_ITEMS:
            break
    return items


def bump_number(value: str, step: int) -> str:
    """Add step. Keep leading zeros when the original value had them."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return value
    next_value = int(digits) + int(step)
    if next_value < 0:
        next_value = 0
    text = str(next_value)
    if len(digits) > 1 and digits.startswith("0") and len(text) <= len(digits):
        return text.zfill(len(digits))
    return text


def pick_span(spans: list[dict[str, Any]]) -> int | None:
    """Choose the longest typed number — usually the id the user cares about."""
    if not spans:
        return None
    best = 0
    for index, span in enumerate(spans):
        if len(str(span.get("value") or "")) > len(str(spans[best].get("value") or "")):
            best = index
    return best


def replace_span(events: list[dict[str, Any]], span: dict[str, Any], value: str) -> list[dict[str, Any]]:
    """Swap one recorded number for another, keeping the rest of the macro."""
    start = int(span["start"])
    end = int(span["end"])
    if start < 0 or end < start or end > len(events):
        return list(events)
    t0 = int(events[start]["t"]) if start < len(events) else 0
    t1 = int(events[end - 1]["t"]) if end > start else t0
    return [*events[:start], *digit_events(value, t0, t1), *events[end:]]


def digit_events(value: str, t0: int, t1: int) -> list[dict[str, Any]]:
    digits = "".join(ch for ch in value if ch.isdigit())[:_MAX_DIGITS]
    if not digits:
        return []
    span = max(1, int(t1) - int(t0))
    step = span / (len(digits) * 2)
    clock = float(t0)
    events: list[dict[str, Any]] = []
    for digit in digits:
        events.append({"t": int(clock), "kind": "key", "action": "down", "key": digit})
        clock += step
        events.append({"t": int(clock), "kind": "key", "action": "up", "key": digit})
        clock += step
    return events


def apply_turn(
    events: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    queue: NumberQueue,
) -> tuple[list[dict[str, Any]], str | None]:
    """Build one replay pass. Always rewrite from the original recording."""
    if queue.span is None or not spans:
        return list(events), None
    if queue.span < 0 or queue.span >= len(spans):
        return list(events), None
    span = spans[queue.span]
    fallback = str(span.get("value") or "1")
    value = queue.take(fallback)
    return replace_span(events, span, value), value


class NumberQueue:
    """Walk a list of numbers. After each use, optionally add `step` to that line."""

    def __init__(
        self,
        items: list[str] | None = None,
        *,
        index: int = 0,
        step: int = 1,
        bump: bool = True,
        span: int | None = None,
        flagged: list[str] | None = None,
        last_used: str = "",
        prefix: str = "",
        start: str = "",
        end: str = "",
        current: str = "",
        range_mode: bool = False,
        exhausted: bool = False,
    ) -> None:
        self.items = [item for item in (items or []) if item][:_MAX_ITEMS]
        self.index = index if self.items and 0 <= index < len(self.items) else 0
        self.step = step if step else 1
        self.bump = bump
        self.span = span
        self.flagged = _clean_codes(flagged)
        self.last_used = _clean_code(last_used)
        self.range_mode = bool(range_mode or prefix or start or end)
        self.prefix = normalize_prefix(prefix)
        start_digits = normalize_digits(start or "00000")
        end_digits = normalize_digits(end or start_digits)
        if int(end_digits) < int(start_digits):
            start_digits, end_digits = end_digits, start_digits
        self.start = start_digits
        self.end = end_digits
        now = normalize_digits(current or start_digits)
        if int(now) < int(self.start):
            now = self.start
        self.current = now
        past_end = int(self.current) > int(self.end)
        finished_last = bool(exhausted) and int(self.current) == int(self.end)
        self.exhausted = self.range_mode and (past_end or finished_last)

    @classmethod
    def from_dict(cls, raw: Any) -> NumberQueue:
        data = raw if isinstance(raw, dict) else {}
        items = parse_number_list("\n".join(str(item) for item in (data.get("items") or [])))
        if not items and isinstance(data.get("text"), str):
            items = parse_number_list(data["text"])
        try:
            index = int(data.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        try:
            step = int(data.get("step") if data.get("step") is not None else 1)
        except (TypeError, ValueError):
            step = 1
        span = data.get("span")
        try:
            span_index: int | None = int(span) if span is not None else None
        except (TypeError, ValueError):
            span_index = None
        if span_index is not None and span_index < 0:
            span_index = None
        return cls(
            items,
            index=index,
            step=step,
            bump=bool(data.get("bump", True)),
            span=span_index,
            flagged=data.get("flagged") or [],
            last_used=str(data.get("last_used") or ""),
            prefix=str(data.get("prefix") or ""),
            start=str(data.get("start") or ""),
            end=str(data.get("end") or ""),
            current=str(data.get("current") or ""),
            range_mode=bool(data.get("range_mode") or data.get("prefix") or data.get("start") or data.get("end")),
            exhausted=bool(data.get("exhausted")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "items": list(self.items),
            "index": self.index,
            "step": self.step,
            "bump": self.bump,
            "flagged": list(self.flagged),
            "last_used": self.last_used,
            "prefix": self.prefix,
            "start": self.start,
            "end": self.end,
            "current": self.current,
            "range_mode": self.range_mode,
            "exhausted": self.exhausted,
        }
        if self.span is not None:
            payload["span"] = self.span
        return payload

    def as_text(self) -> str:
        return "\n".join(self.items)

    def peek(self, fallback: str = "") -> str:
        if self.range_mode:
            if self.exhausted or len(self.prefix) != PREFIX_LEN:
                return ""
            return make_code(self.prefix, self.current)
        if not self.items:
            return "".join(ch for ch in fallback if ch.isdigit()) or fallback
        return self.items[self.index]

    def take(self, fallback: str = "") -> str:
        if self.range_mode:
            value = self.peek()
            if not value:
                self.exhausted = True
                return ""
            self.last_used = value
            nxt = int(self.current) + self.step
            if nxt > 10**DIGIT_LEN - 1:
                self.exhausted = True
                return value
            self.current = str(nxt).zfill(DIGIT_LEN)
            if nxt > int(self.end):
                self.exhausted = True
            return value
        if not self.items:
            seed = "".join(ch for ch in fallback if ch.isdigit()) or fallback
            if not seed:
                return fallback
            self.items = [seed]
            self.index = 0
        value = self.items[self.index]
        self.last_used = value
        if self.bump:
            self.items[self.index] = bump_number(value, self.step)
        self.index = (self.index + 1) % len(self.items)
        return value

    def flag(self, value: str | None = None) -> str | None:
        """Keep the code that was on screen when the layout changed."""
        chosen = _clean_code(value or self.last_used or self.peek())
        if not chosen:
            return None
        if chosen not in self.flagged:
            self.flagged.append(chosen)
            if len(self.flagged) > _MAX_ITEMS:
                self.flagged = self.flagged[-_MAX_ITEMS:]
        return chosen


def _clean_code(raw: Any) -> str:
    return "".join(ch for ch in str(raw or "") if ch.isalnum())[: PREFIX_LEN + DIGIT_LEN]


def _clean_codes(raw: Any) -> list[str]:
    items: list[str] = []
    for item in raw or []:
        code = _clean_code(item)
        if code and code not in items:
            items.append(code)
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _digit_event(event: dict[str, Any]) -> bool:
    return event.get("kind") == "key" and str(event.get("key") or "").isdigit()
