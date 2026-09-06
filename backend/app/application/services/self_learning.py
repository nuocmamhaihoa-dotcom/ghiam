"""Application service for Self-Learning Lab."""
from __future__ import annotations

from typing import Any

from self_learning import SelfLearningLab, get_self_learning_lab


class SelfLearningService:
    def __init__(self, lab: SelfLearningLab | None = None) -> None:
        self._lab = lab or get_self_learning_lab()

    def ingest_call(self, call: dict[str, Any]) -> dict[str, Any]:
        return self._lab.ingest_call(call)

    def dashboard(self) -> dict[str, Any]:
        return self._lab.dashboard()

    def qa_queue(self) -> list[dict[str, Any]]:
        return self._lab.qa_queue()

    def approve(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        return self._lab.approve(proposal_id, reviewer)

    def reject(self, proposal_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        return self._lab.reject(proposal_id, reviewer, reason)

    def merge(self, proposal_id: str, into_proposal_id: str, reviewer: str) -> dict[str, Any]:
        return self._lab.merge(proposal_id, into_proposal_id, reviewer)

    def edit(self, proposal_id: str, reviewer: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self._lab.edit(proposal_id, reviewer, patch)

    def promote(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        return self._lab.promote(proposal_id, reviewer)

    def rollback(self, version_id: str, reviewer: str) -> dict[str, Any]:
        return self._lab.rollback(version_id, reviewer)

    def discover_golden(self, calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        return self._lab.discover_golden(calls)

    def discover_failures(self, calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        return self._lab.discover_failures(calls)

    def discover_revenue_leaks(
        self, calls: list[dict[str, Any]] | None = None, week_id: str = "current"
    ) -> list[dict[str, Any]]:
        return self._lab.discover_revenue_leaks(calls, week_id=week_id)

    def generate_coaching(self, proposal_id: str | None = None) -> list[dict[str, Any]]:
        return self._lab.generate_coaching(proposal_id)

    def self_evaluate(self, metrics_update: dict[str, float] | None = None) -> dict[str, Any]:
        return self._lab.self_evaluate(metrics_update)

    def dataset_growth(self) -> dict[str, Any]:
        return self._lab.dataset_growth()

    def quality(self) -> dict[str, Any]:
        return self._lab.quality_snapshot()
