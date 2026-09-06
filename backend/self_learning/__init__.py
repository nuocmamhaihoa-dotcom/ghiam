"""AI Self-Learning Lab — continuous learning without auto-mutating production rules."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["SelfLearningLab", "get_self_learning_lab"]

if TYPE_CHECKING:
    from self_learning.lab import SelfLearningLab


def __getattr__(name: str) -> Any:
    if name in {"SelfLearningLab", "get_self_learning_lab"}:
        from self_learning.lab import SelfLearningLab, get_self_learning_lab
        return {"SelfLearningLab": SelfLearningLab, "get_self_learning_lab": get_self_learning_lab}[name]
    raise AttributeError(name)
