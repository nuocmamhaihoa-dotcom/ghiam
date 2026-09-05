"""Application service for Autonomous Sales AI."""
from __future__ import annotations

from typing import Any

from autonomous import AutonomousEngine, get_autonomous_engine


class AutonomousService:
    def __init__(self, engine: AutonomousEngine | None = None) -> None:
        self._engine = engine or get_autonomous_engine()

    def recommend(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._engine.recommend(context)

    def run_nba_pipeline(self, context: dict[str, Any], *, auto_execute: bool = True) -> dict[str, Any]:
        return self._engine.run_nba_pipeline(context, auto_execute=auto_execute)

    def assign_lead(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._engine.assign_lead(context)

    def schedule_follow_up(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._engine.schedule_follow_up(context)

    def run_workflow(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._engine.run_workflow(context)

    def trigger_automation(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._engine.trigger_automation(kind, payload)

    def propose_rule_change(
        self,
        *,
        change_type: str,
        title: str,
        proposal: dict[str, Any],
        evidence: list[str] | None = None,
        requested_by: str = "autonomous_ai",
    ) -> dict[str, Any]:
        return self._engine.propose_rule_change(
            change_type=change_type,
            title=title,
            proposal=proposal,
            evidence=evidence,
            requested_by=requested_by,
        )

    def decide_rule_change(self, approval_id: str, *, approve: bool, decided_by: str) -> dict[str, Any]:
        return self._engine.decide_rule_change(approval_id, approve=approve, decided_by=decided_by)

    def propose_learning(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._engine.propose_learning(context)

    def dashboard(self) -> dict[str, Any]:
        return self._engine.dashboard()

    def quality(self) -> dict[str, Any]:
        return self._engine.quality_snapshot()
