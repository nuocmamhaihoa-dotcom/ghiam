
"""Voice separation."""
from __future__ import annotations
from typing import Any


def separate_voices(
    *,
    duration_sec: float,
    channels: int = 1,
    overlap_hint: float = 0.0,
    transcript_hint: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    duration = max(0.1, float(duration_sec or 1.0))
    lines = transcript_hint or []
    agent_sec = customer_sec = 0.0
    for line in lines:
        span = max(0.0, float(line.get("end_sec") or 0) - float(line.get("start_sec") or 0))
        sp = str(line.get("speaker") or "").lower()
        if sp in {"agent", "a", "sale", "seller"}:
            agent_sec += span
        elif sp in {"customer", "client", "c"}:
            customer_sec += span
        else:
            agent_sec += span / 2
            customer_sec += span / 2
    if agent_sec + customer_sec <= 0:
        agent_sec, customer_sec = duration * 0.55, duration * 0.40
    total = max(agent_sec + customer_sec, 0.01)
    overlap = float(overlap_hint)
    sorted_lines = sorted(lines, key=lambda x: float(x.get("start_sec") or 0))
    for i in range(1, len(sorted_lines)):
        prev_end = float(sorted_lines[i - 1].get("end_sec") or 0)
        cur_start = float(sorted_lines[i].get("start_sec") or 0)
        if cur_start < prev_end:
            overlap += prev_end - cur_start
    overlap = min(duration, max(0.0, overlap))
    interrupts = sum(
        1 for i in range(1, len(lines))
        if float(lines[i].get("start_sec") or 0) < float(lines[i - 1].get("end_sec") or 0)
    )
    return {
        "ok": True,
        "tracks": {
            "agent": {"duration_sec": round(agent_sec, 3), "ratio": round(agent_sec / total, 3)},
            "customer": {"duration_sec": round(customer_sec, 3), "ratio": round(customer_sec / total, 3)},
            "background": {"duration_sec": round(max(0.0, duration - total), 3)},
        },
        "overlap_time_sec": round(overlap, 3),
        "interrupt_count": interrupts,
        "speaking_ratio": {
            "agent": round(agent_sec / duration, 3),
            "customer": round(customer_sec / duration, 3),
        },
        "channels_in": channels,
        "method": "stereo_split" if channels >= 2 else "heuristic_overlap",
    }
