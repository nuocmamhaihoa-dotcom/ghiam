"""Display formatters for the Vietnamese interface."""

from __future__ import annotations


def format_int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    whole = max(0, int(seconds))
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_percent(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "%"
