"""Turn typed digits into a list the user can walk and increment."""

from __future__ import annotations

import re
from typing import Any

from pc_macro.screen import (
    DEFAULT_THRESHOLD,
    LANE_PAUSE_THRESHOLD,
    normalize_threshold,
    sanitize_clicks,
    sanitize_frame,
    sanitize_frames,
)

_SPLIT = re.compile(r"[\n,;\s]+")
_MAX_ITEMS = 500
_MAX_DIGITS = 40
PREFIX_LEN = 5
DIGIT_LEN = 5
LANE_COUNT = 1


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


def code_digits(code: Any) -> str:
    """The trailing 5 digits of a full code, or empty if there is no number."""
    cleaned = "".join(ch for ch in str(code or "") if ch.isalnum())
    if not cleaned:
        return ""
    tail = "".join(ch for ch in cleaned if ch.isdigit())
    if not tail:
        return ""
    return normalize_digits(tail[-DIGIT_LEN:] if len(tail) >= DIGIT_LEN else tail)


def sanitize_turns(raw: Any) -> list[dict[str, Any]]:
    """Keep each Enter as {digits, code, changed} so the board can replay it."""
    rows: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, dict):
            code = "".join(ch for ch in str(item.get("code") or "") if ch.isalnum())[: PREFIX_LEN + DIGIT_LEN]
            digits = code_digits(item.get("digits") or code)
            changed = bool(item.get("changed"))
        else:
            code = "".join(ch for ch in str(item or "") if ch.isalnum())[: PREFIX_LEN + DIGIT_LEN]
            digits = code_digits(code)
            changed = False
        if not digits:
            continue
        rows.append({"digits": digits, "code": code or digits, "changed": changed})
        if len(rows) >= _MAX_ITEMS:
            break
    return rows


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
        recover_clicks: list[dict[str, Any]] | None = None,
        change_threshold: int = DEFAULT_THRESHOLD,
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
        self.recover_clicks = sanitize_clicks(recover_clicks)
        self.change_threshold = normalize_threshold(change_threshold)

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
            recover_clicks=data.get("recover_clicks") or [],
            change_threshold=data.get("change_threshold") if data.get("change_threshold") is not None else DEFAULT_THRESHOLD,
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
            "recover_clicks": list(self.recover_clicks),
            "change_threshold": self.change_threshold,
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


class FourLaneQueue:
    """Hướng 2: one independent number sequence per browser window."""

    def __init__(
        self,
        *,
        prefix: str = "",
        start: str = "00001",
        end: str = "00010",
        current: str = "",
        currents: Any = None,
        step: int = 1,
        exhausted: bool = False,
        last_used: Any = None,
        flagged: list[str] | None = None,
        paused: str = "",
        paused_codes: Any = None,
        paused_lanes: Any = None,
        waiting: bool = False,
        waiting_lanes: Any = None,
        watch_frame: Any = None,
        watch_frames: Any = None,
        lane_clicks: Any = None,
        flagged_lanes: Any = None,
        lane_titles: Any = None,
        change_threshold: int = LANE_PAUSE_THRESHOLD,
        turns: Any = None,
        running: str = "",
    ) -> None:
        self.prefix = normalize_prefix(prefix)
        start_digits = normalize_digits(start or "00001")
        end_digits = normalize_digits(end or start_digits)
        if int(end_digits) < int(start_digits):
            start_digits, end_digits = end_digits, start_digits
        self.start = start_digits
        self.end = end_digits
        try:
            self.step = int(step)
        except (TypeError, ValueError):
            self.step = 1
        if self.step < 1:
            self.step = 1
        self.currents = self._init_currents(currents, current)
        self.current = self.currents[0]
        self.last_used = _clean_codes(last_used if isinstance(last_used, (list, tuple)) else [last_used] if last_used else [])
        self.flagged = _clean_codes(flagged)
        self.flagged_lanes = self._init_flagged_lanes(flagged_lanes)
        self.lane_titles = _pad_titles(lane_titles)
        self.paused_lanes = self._init_paused_lanes(paused_lanes, paused_codes, paused)
        self.waiting_lanes = self._init_waiting_lanes(waiting_lanes, waiting)
        self.paused_codes = [code for code in self.paused_lanes if code]
        self.paused = self.paused_codes[0] if self.paused_codes else _clean_code(paused)
        self.waiting = any(self.waiting_lanes)
        self.watch_frames = self._init_watch_frames(watch_frames, watch_frame)
        self.watch_frame = self.watch_frames[0]
        self.lane_clicks = sanitize_clicks(lane_clicks)
        self.change_threshold = normalize_threshold(change_threshold, LANE_PAUSE_THRESHOLD)
        self.turns = sanitize_turns(turns)
        self.running = _clean_code(running)
        self.exhausted = False
        if not self.peek() and not any(self.waiting_lanes):
            if bool(exhausted) or all(int(item) > int(self.end) for item in self.currents):
                self.exhausted = True

    def _init_currents(self, currents: Any, current: Any) -> list[str]:
        if isinstance(currents, (list, tuple)) and any(str(item or "") for item in currents):
            out: list[str] = []
            for index in range(LANE_COUNT):
                raw = currents[index] if index < len(currents) else ""
                now = normalize_digits(raw or self._lane_origin(index))
                if int(now) < int(self.start):
                    now = self._lane_origin(index)
                out.append(now)
            return out
        shared = normalize_digits(current or self.start)
        if int(shared) < int(self.start):
            shared = self.start
        out = []
        for index in range(LANE_COUNT):
            value = int(shared) + index * self.step
            if value > 10**DIGIT_LEN - 1:
                value = 10**DIGIT_LEN - 1
            out.append(str(value).zfill(DIGIT_LEN))
        return out

    def _init_paused_lanes(self, paused_lanes: Any, paused_codes: Any, paused: Any) -> list[str]:
        slots = _pad_codes(paused_lanes)
        extras = _clean_codes(paused_codes if paused_codes is not None else ([paused] if paused else []))
        if not any(slots):
            for code in extras:
                index = self._lane_for_code(code)
                if index is not None and not slots[index]:
                    slots[index] = code
                    self.currents[index] = normalize_digits(code[-DIGIT_LEN:] if len(code) >= DIGIT_LEN else code)
        return slots

    def _init_waiting_lanes(self, waiting_lanes: Any, waiting: bool) -> list[bool]:
        if waiting_lanes is not None:
            flags = _pad_bools(waiting_lanes)
        else:
            flags = [bool(code) for code in self.paused_lanes]
            if waiting and self.paused_lanes and not any(flags):
                flags = [True] * LANE_COUNT
        for index, flag in enumerate(flags):
            if flag and not self.paused_lanes[index]:
                code = self._code_at(index)
                if code:
                    self.paused_lanes[index] = code
        return flags

    def _init_flagged_lanes(self, flagged_lanes: Any) -> list[list[str]]:
        slots: list[list[str]] = [[] for _ in range(LANE_COUNT)]
        raw = flagged_lanes if isinstance(flagged_lanes, (list, tuple)) else []
        for index in range(LANE_COUNT):
            if index < len(raw):
                slots[index] = _clean_codes(raw[index] if isinstance(raw[index], (list, tuple)) else [raw[index]])
        if not any(slots) and self.flagged:
            for code in self.flagged:
                found = self._lane_for_code(code)
                if found is not None and code not in slots[found]:
                    slots[found].append(code)
        return slots

    def _init_watch_frames(self, watch_frames: Any, watch_frame: Any) -> list[dict[str, Any] | None]:
        if isinstance(watch_frames, (list, tuple)):
            return sanitize_frames(watch_frames, LANE_COUNT)
        first = sanitize_frame(watch_frame)
        return [first] + [None] * (LANE_COUNT - 1)

    def _lane_origin(self, index: int) -> str:
        value = int(self.start) + index * self.step
        if value > 10**DIGIT_LEN - 1:
            value = 10**DIGIT_LEN - 1
        return str(value).zfill(DIGIT_LEN)

    def _code_at(self, index: int) -> str:
        if not (0 <= index < LANE_COUNT) or len(self.prefix) != PREFIX_LEN:
            return ""
        if int(self.currents[index]) > int(self.end):
            return ""
        return make_code(self.prefix, self.currents[index])

    def _lane_for_code(self, code: str) -> int | None:
        cleaned = _clean_code(code)
        if not cleaned:
            return None
        slots = getattr(self, "paused_lanes", [""] * LANE_COUNT)
        for index in range(LANE_COUNT):
            if slots[index] == cleaned or self._code_at(index) == cleaned:
                return index
        digits = normalize_digits(cleaned[-DIGIT_LEN:] if len(cleaned) >= DIGIT_LEN else cleaned)
        offset = int(digits) - int(self.start)
        if offset < 0 or self.step < 1 or offset % self.step != 0:
            return None
        return (offset // self.step) % LANE_COUNT

    def _sync_legacy(self) -> None:
        self.current = self.currents[0]
        self.paused_codes = [code for code in self.paused_lanes if code]
        self.paused = self.paused_codes[0] if self.paused_codes else ""
        self.waiting = any(self.waiting_lanes)
        self.watch_frame = self.watch_frames[0]
        if not self.peek() and not any(self.waiting_lanes):
            self.exhausted = True
        else:
            self.exhausted = False

    @classmethod
    def from_dict(cls, raw: Any) -> FourLaneQueue:
        data = raw if isinstance(raw, dict) else {}
        return cls(
            prefix=str(data.get("prefix") or ""),
            start=str(data.get("start") or "00001"),
            end=str(data.get("end") or "00010"),
            current=str(data.get("current") or ""),
            currents=data.get("currents") or [],
            step=data.get("step") if data.get("step") is not None else 1,
            exhausted=bool(data.get("exhausted")),
            last_used=data.get("last_used") or data.get("last_used_lanes") or [],
            flagged=data.get("flagged") or [],
            paused=str(data.get("paused") or ""),
            paused_codes=data.get("paused_codes") or [],
            paused_lanes=data.get("paused_lanes") or [],
            waiting=bool(data.get("waiting")),
            waiting_lanes=data.get("waiting_lanes"),
            watch_frame=data.get("watch_frame") or data.get("frame"),
            watch_frames=data.get("watch_frames") or [],
            lane_clicks=data.get("lane_clicks") or data.get("field_clicks") or [],
            flagged_lanes=data.get("flagged_lanes") or [],
            lane_titles=data.get("lane_titles") or [],
            change_threshold=data.get("change_threshold") if data.get("change_threshold") is not None else LANE_PAUSE_THRESHOLD,
            turns=data.get("turns") or [],
            running=str(data.get("running") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "lanes": True,
            "lane_count": LANE_COUNT,
            "prefix": self.prefix,
            "start": self.start,
            "end": self.end,
            "current": self.current,
            "currents": list(self.currents),
            "step": self.step,
            "exhausted": self.exhausted,
            "last_used": list(self.last_used),
            "flagged": list(self.flagged),
            "flagged_lanes": [list(items) for items in self.flagged_lanes],
            "lane_titles": list(self.lane_titles),
            "paused": self.paused,
            "paused_codes": list(self.paused_codes),
            "paused_lanes": list(self.paused_lanes),
            "waiting": self.waiting,
            "waiting_lanes": list(self.waiting_lanes),
            "lane_clicks": list(self.lane_clicks),
            "change_threshold": self.change_threshold,
            "watch_frames": [dict(item) if item else None for item in self.watch_frames],
            "turns": [dict(item) for item in self.turns],
            "running": self.running,
            "digits": self.running_digits(),
            "changed_digits": self.changed_digits(),
        }
        if self.watch_frame is not None:
            payload["watch_frame"] = dict(self.watch_frame)
        return payload

    def peek_active(self) -> list[tuple[int, str]]:
        """(lane index, code) for browsers that still have a number and are not paused."""
        if self.exhausted or len(self.prefix) != PREFIX_LEN:
            return []
        out: list[tuple[int, str]] = []
        end = int(self.end)
        for index in range(LANE_COUNT):
            if self.waiting_lanes[index]:
                continue
            if int(self.currents[index]) > end:
                continue
            out.append((index, make_code(self.prefix, self.currents[index])))
        return out

    def peek(self) -> list[str]:
        """Active codes only — paused browsers are omitted so the others keep running."""
        return [code for _index, code in self.peek_active()]

    def peek_one(self) -> str:
        codes = self.peek()
        return codes[0] if codes else ""

    def running_digits(self) -> str:
        if self.running:
            return code_digits(self.running)
        peek = self.peek_one()
        if peek:
            return code_digits(peek)
        if self.last_used:
            return code_digits(self.last_used[0])
        return ""

    def changed_digits(self) -> list[str]:
        seen: list[str] = []
        for item in self.turns:
            digits = str(item.get("digits") or "")
            if item.get("changed") and digits and digits not in seen:
                seen.append(digits)
        for code in self.flagged:
            digits = code_digits(code)
            if digits and digits not in seen:
                seen.append(digits)
        return seen

    def mark_running(self, code: str) -> str:
        self.running = _clean_code(code)
        return self.running

    def note_enter(self, code: str, changed: bool = False) -> dict[str, Any]:
        """Record one typed code after Enter. Keep a prior F9 'changed' mark."""
        cleaned = _clean_code(code)
        digits = code_digits(cleaned)
        was = bool(self.turns and self.turns[-1].get("code") == cleaned and self.turns[-1].get("changed"))
        entry = {"digits": digits, "code": cleaned, "changed": bool(changed or was)}
        if self.turns and self.turns[-1].get("code") == cleaned:
            self.turns[-1] = entry
        else:
            self.turns.append(entry)
            if len(self.turns) > _MAX_ITEMS:
                self.turns = self.turns[-_MAX_ITEMS:]
        if entry["changed"]:
            self.flag(cleaned)
        self.running = ""
        return entry

    def board_rows(self, limit: int = 40) -> list[dict[str, Any]]:
        """Newest first. Current 5 digits stay on top as 'run'."""
        rows: list[dict[str, Any]] = []
        current = self.running or self.peek_one()
        current_code = _clean_code(current)
        if current_code:
            rows.append(
                {
                    "digits": code_digits(current_code),
                    "code": current_code,
                    "changed": False,
                    "state": "run",
                }
            )
        for item in reversed(self.turns[-max(1, int(limit or 40)) :]):
            if current_code and item.get("code") == current_code:
                continue
            rows.append({**item, "state": "changed" if item.get("changed") else "ok"})
            if len(rows) >= max(1, int(limit or 40)):
                break
        return rows

    def take_lane(self, index: int) -> str:
        """Advance one browser after its frame did not change."""
        if not (0 <= index < LANE_COUNT) or self.waiting_lanes[index]:
            return ""
        code = self._code_at(index)
        if not code:
            return ""
        nxt = int(self.currents[index]) + LANE_COUNT * self.step
        if nxt > 10**DIGIT_LEN - 1:
            nxt = 10**DIGIT_LEN
        self.currents[index] = str(min(nxt, 10**DIGIT_LEN - 1)).zfill(DIGIT_LEN)
        self.last_used = ([code] + [item for item in self.last_used if item != code])[:LANE_COUNT]
        self._sync_legacy()
        return code

    def take(self) -> list[str]:
        active = self.peek_active()
        if not active:
            if not any(self.waiting_lanes):
                self.exhausted = True
            return []
        codes = [self.take_lane(index) for index, _code in active]
        codes = [code for code in codes if code]
        self.last_used = list(codes)
        self._sync_legacy()
        return codes

    def take_one(self) -> str:
        active = self.peek_active()
        if not active:
            if not any(self.waiting_lanes):
                self.exhausted = True
            return ""
        return self.take_lane(active[0][0])

    def flag(self, value: str | None = None) -> str | None:
        """Keep the full code (prefix + 5 digits) that made that browser change."""
        chosen = _clean_code(value or self.paused or self.peek_one() or (self.last_used[0] if self.last_used else ""))
        if not chosen:
            return None
        if chosen not in self.flagged:
            self.flagged.append(chosen)
            if len(self.flagged) > _MAX_ITEMS:
                self.flagged = self.flagged[-_MAX_ITEMS:]
        index = self._lane_for_code(chosen)
        if index is not None and chosen not in self.flagged_lanes[index]:
            self.flagged_lanes[index].append(chosen)
            if len(self.flagged_lanes[index]) > _MAX_ITEMS:
                self.flagged_lanes[index] = self.flagged_lanes[index][-_MAX_ITEMS:]
        return chosen

    def pause_lane(self, index: int, value: str | None = None) -> str | None:
        """Save only this browser's code. Other lanes keep incrementing."""
        if not (0 <= index < LANE_COUNT):
            return None
        code = _clean_code(value or self.paused_lanes[index] or self._code_at(index))
        if not code:
            return None
        self.flag(code)
        self.paused_lanes[index] = code
        self.waiting_lanes[index] = True
        self.currents[index] = normalize_digits(code[-DIGIT_LEN:] if len(code) >= DIGIT_LEN else code)
        self._sync_legacy()
        self.exhausted = False
        return code

    def pause_at(self, value: str | None = None) -> str | None:
        """Stop the browser that owns `value` and wait for Continue on that lane only."""
        saved = self.pause_batch([value] if value else [])
        return saved[0] if saved else None

    def pause_batch(self, codes: Any) -> list[str]:
        """Pause only the named codes. Other browsers stay on their next number."""
        items = _clean_codes(codes if isinstance(codes, (list, tuple)) else [codes] if codes else [])
        saved: list[str] = []
        for code in items:
            index = self._lane_for_code(code)
            if index is None:
                continue
            got = self.pause_lane(index, code)
            if got:
                saved.append(got)
        return saved

    def resume_lane(self, index: int) -> str:
        """Unlock one paused browser so only that website joins the next Enter."""
        if not (0 <= index < LANE_COUNT) or not self.waiting_lanes[index]:
            return ""
        code = self.paused_lanes[index]
        if code:
            self.currents[index] = normalize_digits(code[-DIGIT_LEN:] if len(code) >= DIGIT_LEN else code)
        self.waiting_lanes[index] = False
        self.paused_lanes[index] = ""
        self._sync_legacy()
        self.exhausted = False
        return code

    def resume(self) -> str:
        """Unlock every paused browser so they join the next Enter burst."""
        unlocked = [code for code in self.paused_lanes if code]
        for index in range(LANE_COUNT):
            self.resume_lane(index)
        return " · ".join(unlocked) if unlocked else (self.peek_one() or "")


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


def _pad_codes(raw: Any, count: int = LANE_COUNT) -> list[str]:
    items = list(raw or [])
    return [_clean_code(items[index]) if index < len(items) else "" for index in range(count)]


def _pad_bools(raw: Any, count: int = LANE_COUNT) -> list[bool]:
    items = list(raw or [])
    return [bool(items[index]) if index < len(items) else False for index in range(count)]


def _pad_titles(raw: Any, count: int = LANE_COUNT) -> list[str]:
    items = list(raw or [])
    return [str(items[index] or "").strip()[:40] if index < len(items) else "" for index in range(count)]


def _digit_event(event: dict[str, Any]) -> bool:
    return event.get("kind") == "key" and str(event.get("key") or "").isdigit()
