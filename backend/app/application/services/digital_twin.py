"""Application service for AI Digital Twin Salesperson."""
from __future__ import annotations

from typing import Any

from digital_twin import DigitalTwinEngine, get_digital_twin_engine


class DigitalTwinService:
    def __init__(self, engine: DigitalTwinEngine | None = None) -> None:
        self._engine = engine or get_digital_twin_engine()

    def train(self, *, agent_id: str, display_name: str, calls: list[dict[str, Any]], activate: bool = False) -> dict[str, Any]:
        return self._engine.train_twin(
            agent_id=agent_id,
            display_name=display_name,
            calls=calls,
            activate=activate,
        )

    def list_twins(self) -> list[dict[str, Any]]:
        return self._engine.list_twins()

    def get_twin(self, twin_id: str) -> dict[str, Any] | None:
        return self._engine.get_twin(twin_id)

    def act_as_twin(self, twin_id: str, customer_text: str, step: int = 0) -> dict[str, Any]:
        return self._engine.act_as_twin(twin_id, customer_text, step=step)

    def roleplay(
        self,
        twin_id: str,
        *,
        trainee_id: str,
        scenario: str,
        trainee_turns: list[str],
        customer_turns: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._engine.roleplay(
            twin_id,
            trainee_id=trainee_id,
            scenario=scenario,
            trainee_turns=trainee_turns,
            customer_turns=customer_turns,
        )

    def score_similarity(self, twin_id: str, trainee_text: str, customer_text: str = "") -> dict[str, Any]:
        return self._engine.score_similarity(twin_id, trainee_text, customer_text)

    def dashboard(self) -> dict[str, Any]:
        return self._engine.dashboard()

    def quality(self) -> dict[str, Any]:
        return self._engine.quality_snapshot()
