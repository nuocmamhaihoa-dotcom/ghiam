"""Application service for CLTV Engine."""
from __future__ import annotations

from typing import Any

from cltv import CLTVEngine, get_cltv_engine


class CLTVService:
    def __init__(self, engine: CLTVEngine | None = None) -> None:
        self._engine = engine or get_cltv_engine()

    def predict(self, **kwargs: Any) -> dict[str, Any]:
        return self._engine.predict(**kwargs)

    def prioritize(self, leads: list[dict[str, Any]]) -> dict[str, Any]:
        return self._engine.prioritize(leads)

    def dashboard(self) -> dict[str, Any]:
        return self._engine.dashboard()

    def quality(self) -> dict[str, Any]:
        return self._engine.quality_snapshot()
