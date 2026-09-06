"""AI Digital Twin Salesperson package."""
from __future__ import annotations

from typing import Any

__all__ = ["DigitalTwinEngine", "get_digital_twin_engine"]


def __getattr__(name: str) -> Any:
    if name in {"DigitalTwinEngine", "get_digital_twin_engine"}:
        from digital_twin.engine import DigitalTwinEngine, get_digital_twin_engine

        return {
            "DigitalTwinEngine": DigitalTwinEngine,
            "get_digital_twin_engine": get_digital_twin_engine,
        }[name]
    raise AttributeError(name)
