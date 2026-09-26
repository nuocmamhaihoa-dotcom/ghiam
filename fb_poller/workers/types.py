from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PostJob:
    """Detached poll job — safe across DB sessions / queue hops."""

    id: int
    url: str
    tier: str
