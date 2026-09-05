"""AI Negotiation Strategy Engine package."""
from __future__ import annotations

from typing import Any

__all__ = ["NegotiationEngine", "get_negotiation_engine"]


def __getattr__(name: str) -> Any:
    if name in {"NegotiationEngine", "get_negotiation_engine"}:
        from negotiation.engine import NegotiationEngine, get_negotiation_engine

        return {
            "NegotiationEngine": NegotiationEngine,
            "get_negotiation_engine": get_negotiation_engine,
        }[name]
    raise AttributeError(name)
