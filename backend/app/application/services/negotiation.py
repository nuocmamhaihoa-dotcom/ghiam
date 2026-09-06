"""Application service for Negotiation Strategy Engine."""
from __future__ import annotations

from typing import Any

from negotiation import NegotiationEngine, get_negotiation_engine


class NegotiationService:
    def __init__(self, engine: NegotiationEngine | None = None) -> None:
        self._engine = engine or get_negotiation_engine()

    def analyze(
        self,
        customer_utterance: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._engine.analyze(
            customer_utterance,
            history=history,
            context=context,
        )

    def predict(
        self,
        customer_utterance: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._engine.predict(
            customer_utterance,
            history=history,
            context=context,
        )

    def compare(
        self,
        customer_utterance: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._engine.compare_strategies(
            customer_utterance,
            context=context,
        )

    def dashboard(self) -> dict[str, Any]:
        return self._engine.dashboard()

    def quality(self) -> dict[str, Any]:
        return self._engine.quality_snapshot()
