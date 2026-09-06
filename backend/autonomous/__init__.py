"""Autonomous Sales AI — NBA + safe automation + approval-gated rule changes."""
from __future__ import annotations

from typing import Any

__all__ = ["AutonomousEngine", "get_autonomous_engine"]


def __getattr__(name: str) -> Any:
    if name in {"AutonomousEngine", "get_autonomous_engine"}:
        from autonomous.engine import AutonomousEngine, get_autonomous_engine

        return {"AutonomousEngine": AutonomousEngine, "get_autonomous_engine": get_autonomous_engine}[name]
    raise AttributeError(name)
