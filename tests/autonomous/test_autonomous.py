"""Autonomous Sales AI suite — >=1000 cases.

- 400 NBA recommendation scenarios
- 300 automation safety scenarios
- 300 approval / quality scenarios
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autonomous.automation import execute_automation, is_restricted_change, is_safe_automation
from autonomous.engine import AutonomousEngine
from autonomous.recommender import map_action_to_automation, recommend_nba
from autonomous.store import AutonomousStore
from autonomous.types import NBA_ACTIONS, QUALITY_THRESHOLDS, RESTRICTED_CHANGES, SAFE_AUTOMATIONS


SENTIMENTS = ["positive", "curious", "neutral", "frustrated", "resistant", "negative"]
OBJECTIONS = [None, "price", "timing", "trust", "competitor", "authority"]


def _ctx(i: int) -> dict:
    buy = (i % 20) / 20
    return {
        "lead_id": f"L{i}",
        "call_id": f"C{i}",
        "buy_signal": buy,
        "sentiment": SENTIMENTS[i % len(SENTIMENTS)],
        "objection": OBJECTIONS[i % len(OBJECTIONS)],
        "intent": "ready" if buy >= 0.85 else ("buy" if buy >= 0.75 else ""),
        "qa_score": 40 + (i % 60),
        "churn_risk": (i % 10) / 10,
        "cltv_score": (i % 12) / 12,
        "missed_followups": i % 5,
    }


NBA_CASES = [_ctx(i) for i in range(400)]
AUTO_CASES = list(range(300))
QUALITY_CASES = [_ctx(i + 7) for i in range(300)]


@pytest.fixture()
def engine(tmp_path: Path) -> AutonomousEngine:
    return AutonomousEngine(store=AutonomousStore(root=tmp_path))


@pytest.mark.parametrize("case", NBA_CASES, ids=[f"nba-{i}" for i in range(400)])
def test_nba_recommendations(engine: AutonomousEngine, case: dict) -> None:
    out = engine.recommend(case)
    assert out["ok"] is True
    rec = out["recommendation"]
    assert rec["action"] in NBA_ACTIONS
    assert 0.0 <= float(rec["confidence"]) <= 1.0
    assert rec["rationale"]
    assert rec["evidence"]
    pipe = engine.run_nba_pipeline(case, auto_execute=True)
    assert pipe["ok"] is True
    assert pipe["recommendation"]["action"] in NBA_ACTIONS
    auto = pipe.get("automation")
    if isinstance(auto, dict) and auto.get("job_id"):
        assert auto["kind"] in SAFE_AUTOMATIONS
        assert pipe["blocked"] is False


@pytest.mark.parametrize("i", AUTO_CASES, ids=[f"auto-{i}" for i in range(300)])
def test_automation_safety(engine: AutonomousEngine, i: int) -> None:
    safe_kind = SAFE_AUTOMATIONS[i % len(SAFE_AUTOMATIONS)]
    restricted_kind = RESTRICTED_CHANGES[i % len(RESTRICTED_CHANGES)]
    assert is_safe_automation(safe_kind) is True
    assert is_restricted_change(restricted_kind) is True

    ok = engine.trigger_automation(safe_kind, {"lead_id": f"L{i}"})
    assert ok["ok"] is True
    assert ok["blocked"] is False
    assert ok["job"]["kind"] == safe_kind
    assert ok["job"]["evidence"]

    blocked = engine.trigger_automation(restricted_kind, {"lead_id": f"L{i}"})
    assert blocked["ok"] is False
    assert blocked["blocked"] is True
    assert blocked["job"] is None

    unknown = execute_automation(f"unknown_action_{i}", {"lead_id": f"L{i}"})
    assert unknown["blocked"] is True


@pytest.mark.parametrize("case", QUALITY_CASES, ids=[f"qual-{i}" for i in range(300)])
def test_approval_and_quality(engine: AutonomousEngine, case: dict) -> None:
    engine.recommend(case)
    engine.run_nba_pipeline(case, auto_execute=True)
    change_type = RESTRICTED_CHANGES[int(case["buy_signal"] * 10) % len(RESTRICTED_CHANGES)]
    proposed = engine.propose_rule_change(
        change_type=change_type,
        title=f"change-{case['lead_id']}",
        proposal={"patch": case["lead_id"]},
        evidence=[f"lead={case['lead_id']}", f"buy={case['buy_signal']}"],
        requested_by="tester",
    )
    assert proposed["ok"] is True
    assert proposed["applied"] is False
    approval = proposed["approval"]
    assert approval["status"] == "pending"
    assert approval["change_type"] == change_type

    blocked = engine.trigger_automation(change_type, {"x": 1})
    assert blocked["blocked"] is True

    decided = engine.decide_rule_change(
        approval["approval_id"],
        approve=True,
        decided_by="manager",
    )
    assert decided["ok"] is True
    assert decided["applied"] is True
    assert decided["approval"]["status"] == "approved"
    assert decided["approval"]["decided_by"] == "manager"

    dash = engine.dashboard()
    assert dash["ok"] is True
    assert dash["approval_only_rule_changes"] is True
    for key in (
        "recommendation_count",
        "automations_run",
        "automations_blocked",
        "approvals_pending",
        "approvals_decided",
        "rule_changes_applied",
        "avg_confidence",
        "nba_mix",
    ):
        assert key in dash["widgets"]

    snap = engine.quality_snapshot()
    assert snap["ok"] is True
    assert snap["approval_only_rule_changes"] is True
    for key, threshold in QUALITY_THRESHOLDS.items():
        assert key in snap["checks"]
        assert snap["checks"][key]["ok"] is True
        assert float(snap["checks"][key]["value"]) >= threshold


def test_map_action_coverage() -> None:
    for action in NBA_ACTIONS:
        kind = map_action_to_automation(action)
        if kind is not None:
            assert kind in SAFE_AUTOMATIONS
    rec = recommend_nba({"buy_signal": 0.95, "intent": "ready"})
    assert rec.action in {"send_proposal", "close_lead"}
