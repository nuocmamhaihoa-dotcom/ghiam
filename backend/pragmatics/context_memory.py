"""Context memory — keeps ±N surrounding turns for pragmatic resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ContextWindow:
    center_index: int
    before: list[dict[str, Any]]
    after: list[dict[str, Any]]
    before_indices: list[int]
    after_indices: list[int]
    joined_before: str
    joined_after: str
    joined_all: str


class ContextMemory:
    def __init__(self, *, radius: int = 5) -> None:
        if radius < 5 or radius > 10:
            raise ValueError("context radius must be between 5 and 10 inclusive")
        self.radius = radius

    def window(self, turns: list[dict[str, Any]], index: int) -> ContextWindow:
        start = max(0, index - self.radius)
        end = min(len(turns), index + self.radius + 1)
        before_idx = list(range(start, index))
        after_idx = list(range(index + 1, end))
        before = [turns[i] for i in before_idx]
        after = [turns[i] for i in after_idx]
        joined_before = " ".join(str(t.get("text") or "") for t in before).lower()
        joined_after = " ".join(str(t.get("text") or "") for t in after).lower()
        joined_all = f"{joined_before} {str(turns[index].get('text') or '').lower()} {joined_after}".strip()
        return ContextWindow(
            center_index=index,
            before=before,
            after=after,
            before_indices=before_idx,
            after_indices=after_idx,
            joined_before=joined_before,
            joined_after=joined_after,
            joined_all=joined_all,
        )
