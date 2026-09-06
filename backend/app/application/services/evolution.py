"""Application service for Evolution Engine."""

from __future__ import annotations

from typing import Any

from evolution.engine import EvolutionEngine
from evolution.types import ArtifactType, PipelineOutput


class EvolutionService:
    def __init__(self, engine: EvolutionEngine | None = None) -> None:
        self._engine = engine or EvolutionEngine()

    def dashboard(self) -> dict[str, Any]:
        return self._engine.executive_dashboard()

    def quality_gate(self) -> dict[str, Any]:
        return self._engine.quality.evaluate()

    def shadow_compare(self, call_id: str, production: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        report = self._engine.shadow.compare(call_id, production, candidate)
        return report.to_dict()

    def create_experiment(self, name: str, arm_a: dict[str, Any], arm_b: dict[str, Any]) -> dict[str, Any]:
        return self._engine.experiments.create(name, arm_a, arm_b).to_dict()

    def record_experiment(self, experiment_id: str, arm: str, metrics: dict[str, float]) -> dict[str, Any]:
        return self._engine.experiments.record(experiment_id, arm, metrics)

    def conclude_experiment(self, experiment_id: str) -> dict[str, Any]:
        return self._engine.experiments.conclude(experiment_id).to_dict()

    def list_experiments(self) -> list[dict[str, Any]]:
        return self._engine.experiments.list_experiments()

    def scorecard(self) -> dict[str, Any]:
        return self._engine.scorecard.dashboard()

    def update_scorecard(self, model_name: str, metrics: dict[str, float], sample_count: int = 0) -> dict[str, Any]:
        return self._engine.scorecard.update(model_name, metrics, sample_count=sample_count)

    def detect_drift(self, signals: dict[str, float]) -> dict[str, Any]:
        return self._engine.drift.detect(signals)

    def drift_report(self) -> dict[str, Any]:
        return self._engine.drift.report()

    def capture_failure(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._engine.failures.capture(**payload)

    def replay_failure(self, failure_id: str) -> dict[str, Any]:
        return self._engine.failures.replay(failure_id)

    def analyze_performance(self, snapshot: dict[str, float]) -> dict[str, Any]:
        return self._engine.performance.analyze(snapshot)

    def record_observability(self, metrics: dict[str, float]) -> dict[str, Any]:
        return self._engine.observability.record(metrics)

    def observability_snapshot(self) -> dict[str, Any]:
        return self._engine.observability.snapshot()

    def weekly_cycle(self, signals: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._engine.run_weekly_cycle(signals)

    def pending_proposals(self) -> list[dict[str, Any]]:
        return self._engine.proposals.list_pending_qa()

    def approve_proposal(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        return self._engine.proposals.approve(proposal_id, reviewer)

    def reject_proposal(self, proposal_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        return self._engine.proposals.reject(proposal_id, reviewer, reason)

    def promote_proposal(self, proposal_id: str, actor: str) -> dict[str, Any]:
        return self._engine.proposals.promote(proposal_id, actor)

    def snapshot_artifact(
        self,
        artifact_id: str,
        artifact_type: str,
        payload: dict[str, Any],
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        return self._engine.rollback.snapshot(
            artifact_id,
            ArtifactType(artifact_type) if artifact_type in ArtifactType._value2member_map_ else artifact_type,
            payload,
            version=version,
        ).to_dict()

    def rollback_artifact(
        self,
        artifact_id: str,
        *,
        to_version: str | None = None,
        to_version_id: str | None = None,
        actor: str = "qa",
    ) -> dict[str, Any]:
        return self._engine.rollback.rollback(
            artifact_id,
            to_version=to_version,
            to_version_id=to_version_id,
            actor=actor,
        )

    def rollback_readiness(self) -> dict[str, Any]:
        return self._engine.rollback.readiness()

    def write_reports(self, out_dir: str = "docs/evolution") -> dict[str, str]:
        return self._engine.write_reports(out_dir)
