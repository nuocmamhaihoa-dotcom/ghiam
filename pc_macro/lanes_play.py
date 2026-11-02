"""One Hướng 2 turn: type lane codes, then Enter with real key delays."""

from __future__ import annotations

from typing import Any, Callable

from pc_macro.events import sanitize_events, schedule
from pc_macro.script import compile_script, four_lane_enter_burst_steps, four_lane_type_steps, one_code_enter_steps

SendFn = Callable[[dict[str, Any]], None]
SleepFn = Callable[[float], None]
CancelFn = Callable[[], bool]


def emit_scheduled(
    events: list[dict[str, Any]],
    send: SendFn,
    sleep: SleepFn,
    cancelled: CancelFn | None = None,
    speed: float = 1.0,
) -> int:
    """Send events with the gaps compiled into `t`. Instant send drops Enter."""
    played = 0
    for wait_ms, event in schedule(sanitize_events(events), speed):
        if cancelled and cancelled():
            return played
        if wait_ms:
            sleep(float(wait_ms) / 1000.0)
        if cancelled and cancelled():
            return played
        send(event)
        played += 1
    return played


def type_lane_codes(
    codes: list[str] | tuple[str, ...],
    clicks: Any,
    send: SendFn,
    sleep: SleepFn,
    cancelled: CancelFn | None = None,
) -> int:
    _script, batch = compile_script(four_lane_type_steps(list(codes or []), clicks))
    return emit_scheduled(batch, send, sleep, cancelled)


def enter_lane_burst(
    clicks: Any,
    count: int,
    send: SendFn,
    sleep: SleepFn,
    cancelled: CancelFn | None = None,
) -> int:
    _script, burst = compile_script(four_lane_enter_burst_steps(clicks, count=count))
    return emit_scheduled(burst, send, sleep, cancelled)


def play_type_then_enter(
    codes: list[str] | tuple[str, ...],
    clicks: Any,
    send: SendFn,
    sleep: SleepFn,
    cancelled: CancelFn | None = None,
    settle: float = 0.5,
) -> dict[str, int]:
    """Type every code, wait for focus, then Enter each bound field."""
    typed = type_lane_codes(codes, clicks, send, sleep, cancelled)
    if cancelled and cancelled():
        return {"typed": typed, "enters": 0, "stopped": 1}
    entered = enter_lane_burst(clicks, len(list(codes or [])), send, sleep, cancelled)
    if cancelled and cancelled():
        return {"typed": typed, "enters": entered, "stopped": 1}
    if settle > 0:
        sleep(settle)
    return {"typed": typed, "enters": entered, "stopped": 0}


def play_one_code_enter(
    code: str,
    click: Any,
    send: SendFn,
    sleep: SleepFn,
    cancelled: CancelFn | None = None,
) -> dict[str, int]:
    """Focus the chosen point, type one code, press Enter once."""
    _script, batch = compile_script(one_code_enter_steps(str(code or ""), click))
    played = emit_scheduled(batch, send, sleep, cancelled)
    downs = [event for event in batch if event.get("kind") == "key" and event.get("action") == "down"]
    enters = sum(1 for event in downs if event.get("key") == "enter")
    letters = sum(1 for event in downs if len(str(event.get("key") or "")) == 1)
    stopped = 1 if cancelled and cancelled() else 0
    return {"played": played, "typed": letters, "enters": enters, "stopped": stopped}
