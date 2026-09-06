"""Evolution Engine tests — shadow, experiments, proposals, rollback, quality gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from evolution.engine import EvolutionEngine
from evolution.proposals import ProposalGenerator
from evolution.quality import EvolutionQualityGate
from evolution.store import EvolutionStore
from evolution.types import ArtifactType, PipelineOutput, ProposalStatus
from experiments.engine import ExperimentEngine
from metrics.drift import DriftDetector
from metrics.failure_replay import FailureReplayEngine
from metrics.observability import ObservabilityCenter
from metrics.performance import PerformanceOptimizer
from metrics.scorecard import ModelScorecard
from rollback.engine import RollbackEngine
from shadow_mode.runner import ShadowMode


@pytest.fixture()
def store(tmp_path: Path) -> EvolutionStore:
    return EvolutionStore(root=tmp_path / "evolution")


@pytest.fixture()
def engine(store: EvolutionStore) -> EvolutionEngine:
    return EvolutionEngine(store=store)


def _prod(call_id: str = "c1") -> PipelineOutput:
    return PipelineOutput(
        call_id=call_id,
        score=70.0,
        root_cause="price objection",
        emotion="neutral",
        buying_signal=0.2,
        coaching="ask budget",
        revenue_leak=0.1,
    )


def _cand(call_id: str = "c1") -> PipelineOutput:
    return PipelineOutput(
        call_id=call_id,
        score=92.0,
        root_cause="trust gap",
        emotion="anxious",
        buying_signal=0.85,
        coaching="reassure and probe",
        revenue_leak=0.45,
    )


def test_shadow_mode_zero_user_impact(store: EvolutionStore) -> None:
    sm = ShadowMode(store=store)
    report = sm.compare("c1", _prod(), _cand())
    assert report.user_impact is False
    assert report.significant is True
    assert len(report.deltas) == 6
    assert len(store.list_shadow_reports()) == 1


def test_shadow_candidate_failure_isolated(store: EvolutionStore) -> None:
    sm = ShadowMode(store=store)

    def boom() -> PipelineOutput:
        raise RuntimeError("candidate crashed")

    report = sm.run("c1", lambda: _prod(), boom)
    assert report.user_impact is False
    assert report.candidate["root_cause"] == "candidate_error"


def test_experiment_never_auto_promotes(store: EvolutionStore) -> None:
    ee = ExperimentEngine(store=store)
    exp = ee.create("Rule A vs B", {"rule": "A"}, {"rule": "B"})
    metrics_a = {
        "accuracy": 0.6,
        "qa_agreement": 0.6,
        "revenue_impact": 0.5,
        "coaching_effectiveness": 0.55,
        "conversion_improvement": 0.5,
    }
    metrics_b = {k: v + 0.25 for k, v in metrics_a.items()}
    for _ in range(10):
        ee.record(exp.experiment_id, "a", metrics_a)
        ee.record(exp.experiment_id, "b", metrics_b)
    result = ee.conclude(exp.experiment_id)
    assert result.winner == "b"
    assert result.auto_promoted is False
    assert "QA" in result.conclusion or "approval" in result.conclusion.lower()


def test_scorecard_and_drift(store: EvolutionStore) -> None:
    sc = ModelScorecard(store=store)
    row = sc.update(
        "Evidence AI",
        {
            "accuracy": 0.91,
            "precision": 0.9,
            "recall": 0.89,
            "f1": 0.895,
            "drift": 0.04,
            "latency_ms": 110,
        },
        sample_count=50,
    )
    assert row["accuracy"] == 0.91
    dash = sc.dashboard()
    assert "Evidence AI" in dash["models"]

    drift = DriftDetector(store=store, threshold=0.25)
    out = drift.detect({"data": 0.4, "language": 0.1, "industry": 0.05, "customer": 0.0})
    assert out["elevated"] is True
    assert drift.open_alerts()


def test_failure_replay_and_performance(store: EvolutionStore) -> None:
    fr = FailureReplayEngine(store=store)
    row = fr.capture(call_id="c9", transcript="hello", error="timeout")
    replay = fr.replay(row["failure_id"])
    assert replay["qa_review_ready"] is True

    perf = PerformanceOptimizer(store=store).analyze({"query_p95_ms": 900, "cache_hit_rate": 0.4})
    assert perf["suggestion_count"] >= 1


def test_observability_alerts(store: EvolutionStore) -> None:
    obs = ObservabilityCenter(store=store)
    result = obs.record(
        {
            "error_rate": 0.2,
            "ai_latency_ms": 5000,
            "queue_depth": 10,
            "upload_speed_mbps": 5,
            "transcript_speed_x": 2,
            "dashboard_p95_ms": 400,
        }
    )
    assert result["alerts"]


def test_proposals_require_qa_no_auto_merge(store: EvolutionStore) -> None:
    gen = ProposalGenerator(store=store)
    created = gen.generate_weekly(signals={"shadow_significant": 3})
    assert len(created) == 4
    for p in created:
        assert p["status"] == ProposalStatus.PENDING_QA.value
        assert p["metadata"]["auto_merge"] is False

    pid = created[0]["proposal_id"]
    with pytest.raises(ValueError):
        gen.promote(pid, "qa")

    approved = gen.approve(pid, "qa")
    assert approved["status"] == ProposalStatus.APPROVED.value
    assert approved.get("promoted_to_production") is False

    promoted = gen.promote(pid, "qa")
    assert promoted["status"] == ProposalStatus.PRODUCTION.value
    assert store.list_versions(pid)


def test_rollback_engine(store: EvolutionStore) -> None:
    rb = RollbackEngine(store=store)
    rb.snapshot("rule_1", ArtifactType.RULE, {"text": "v1"}, version="v1")
    rb.snapshot("rule_1", ArtifactType.RULE, {"text": "v2"}, version="v2")
    out = rb.rollback("rule_1", to_version="v1", actor="qa")
    assert out["from_version"] == "v1"
    assert out["restored"]["payload"]["text"] == "v1"
    ready = rb.readiness()
    assert ready["ready"] is True


def test_quality_gate_blocks_drift_and_auto_merge(store: EvolutionStore) -> None:
    DriftDetector(store=store, threshold=0.2).detect({"data": 0.9})
    gate = EvolutionQualityGate(store=store).evaluate()
    assert gate["ok"] is False
    assert any("drift" in b for b in gate["blockers"])


def test_evolution_engine_weekly_and_dashboard(engine: EvolutionEngine, store: EvolutionStore) -> None:
    # Keep observability healthy so gate can pass after clearing drift
    engine.observability.record(
        {
            "error_rate": 0.01,
            "ai_latency_ms": 400,
            "queue_depth": 1,
            "upload_speed_mbps": 8,
            "transcript_speed_x": 3,
            "dashboard_p95_ms": 200,
        }
    )
    engine.scorecard.update(
        "Evidence AI",
        {"accuracy": 0.9, "precision": 0.9, "recall": 0.9, "f1": 0.9, "drift": 0.02, "latency_ms": 90},
        sample_count=10,
    )
    engine.shadow.compare("c1", _prod(), _cand())
    cycle = engine.run_weekly_cycle()
    assert cycle["auto_merged"] is False
    assert cycle["proposals_created"] == 4

    dash = engine.executive_dashboard()
    assert dash["shadow_comparison"]["user_impact_always_false"] is True
    assert dash["experiment_results"]["auto_promoted_count"] == 0
    assert "pending_qa_proposals" in dash

    paths = engine.write_reports(out_dir=store.root / "reports")
    assert len(paths) >= 8
    for p in paths.values():
        assert Path(p).exists()
