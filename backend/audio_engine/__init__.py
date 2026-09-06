
"""Audio Intelligence Engine — first layer of the AI Sales OS pipeline."""
from __future__ import annotations

from typing import Any

__all__ = ["AudioIntelligenceEngine", "get_audio_engine"]


def __getattr__(name: str) -> Any:
    if name in {"AudioIntelligenceEngine", "get_audio_engine"}:
        from audio_engine.engine import AudioIntelligenceEngine, get_audio_engine

        return {"AudioIntelligenceEngine": AudioIntelligenceEngine, "get_audio_engine": get_audio_engine}[name]
    raise AttributeError(name)
