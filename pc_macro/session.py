"""Record a session of desktop events and play it back with repeat / cancel."""

from __future__ import annotations

from typing import Any, Callable

from pc_macro.events import point_in_box, sanitize_events, schedule

SendFn = Callable[[dict[str, Any]], None]
SleepFn = Callable[[float], None]
IgnoreFn = Callable[[float, float], bool]
PrepareFn = Callable[[int], list[dict[str, Any]]]
AfterFn = Callable[[int], list[dict[str, Any]] | None]


class Recorder:
    """Collect events while the user has pressed Ghi. F8 stops without being stored."""

    def __init__(self, ignore_point: IgnoreFn | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.on = False
        self._t0 = 0
        self._ignore_point = ignore_point

    def start(self, now_ms: int) -> None:
        self.on = True
        self._t0 = now_ms
        self.events = []

    def add(self, now_ms: int, raw: dict[str, Any]) -> bool:
        """Append one event. Return False when F8 asked the session to stop."""
        if not self.on:
            return True
        kind = raw.get("kind")
        key = str(raw.get("key") or "").strip().lower()
        if key == "escape":
            key = "esc"
        if kind == "key" and key == "f8":
            self.stop()
            return False
        if kind in {"click", "scroll"}:
            try:
                x = float(raw.get("x"))
                y = float(raw.get("y"))
            except (TypeError, ValueError):
                return True
            if self._ignore_point and self._ignore_point(x, y):
                return True
        item = dict(raw)
        item["t"] = max(0, int(now_ms) - int(self._t0))
        cleaned = sanitize_events([item])
        if cleaned:
            self.events.append(cleaned[0])
        return True

    def stop(self) -> list[dict[str, Any]]:
        self.on = False
        self.events = sanitize_events(self.events)
        return self.events


class Player:
    """Replay sanitized events. times=0 keeps looping until cancel()."""

    def __init__(self, send: SendFn, sleep: SleepFn) -> None:
        self._send = send
        self._sleep = sleep
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def emit(self, events: list[dict[str, Any]], speed: float = 1.0) -> int:
        """Replay one batch with compiled delays. Used for the Hướng 2 Enter burst."""
        return self._emit(sanitize_events(events), speed)

    def play(
        self,
        events: list[dict[str, Any]],
        *,
        times: int = 1,
        gap_ms: int = 400,
        speed: float = 1.0,
        prepare: PrepareFn | None = None,
        after: AfterFn | None = None,
    ) -> dict[str, Any]:
        cleaned = sanitize_events(events)
        loops = 0
        played = 0
        if not cleaned and prepare is None:
            return {"loops": 0, "played": 0, "stopped": False}
        while not self.cancelled:
            try:
                batch = sanitize_events(prepare(loops) if prepare is not None else cleaned)
            except Exception:
                return {"loops": loops, "played": played, "stopped": True}
            if not batch:
                break
            played += self._emit(batch, speed)
            if self.cancelled:
                return {"loops": loops, "played": played, "stopped": True}
            loops += 1
            if after is not None and not self.cancelled:
                try:
                    extra = sanitize_events(after(loops) or [])
                except Exception:
                    extra = []
                if extra:
                    played += self._emit(extra, speed)
                    if self.cancelled:
                        return {"loops": loops, "played": played, "stopped": True}
            if times > 0 and loops >= times:
                break
            if gap_ms and not self.cancelled:
                self._sleep(max(0, gap_ms) / 1000.0)
        return {"loops": loops, "played": played, "stopped": self.cancelled}

    def _emit(self, events: list[dict[str, Any]], speed: float) -> int:
        played = 0
        for wait_ms, event in schedule(events, speed):
            if self.cancelled:
                return played
            if wait_ms:
                self._sleep(wait_ms / 1000.0)
            if self.cancelled:
                return played
            try:
                self._send(event)
            except Exception:
                continue
            played += 1
        return played


def ignore_window(box_fn: Callable[[], tuple[float, float, float, float] | None]) -> IgnoreFn:
    """Skip clicks that land on the recorder window so Ghi / Dừng are not replayed."""

    def _ignore(x: float, y: float) -> bool:
        box = box_fn()
        if box is None:
            return False
        return point_in_box(x, y, box)

    return _ignore
