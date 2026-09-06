"""Self-Learning Lab suite (>5000 cases)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.clusters import ClusterEngine
from self_learning.lab import SelfLearningLab
from self_learning.store import LearningStore

FIX = Path(__file__).with_name("fixtures_5000.jsonl")
FIXTURES = [json.loads(line) for line in FIX.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def lab(tmp_path: Path) -> SelfLearningLab:
    return SelfLearningLab(store=LearningStore(root=tmp_path))


@pytest.mark.parametrize("row", FIXTURES, ids=[r["id"] for r in FIXTURES])
def test_self_learning_fixture(lab: SelfLearningLab, row: dict) -> None:
    kind = row["kind"]

    if kind == "ingest":
        out = lab.ingest_call(row["call"])
        assert out["pending_qa"] is True
        assert out["auto_applied_to_production"] is False
        assert out["call_id"] == row["call"]["call_id"]
        assert lab.quality_snapshot()["pending_leaked_into_production"] == 0

    elif kind == "golden":
        found = lab.discover_golden([row["call"]])
        assert len(found) >= row["expect_min"]
        for p in found:
            assert p["status"] == "pending_qa"
            assert float(p["quality_score"]) >= 0.55

    elif kind == "failure":
        found = lab.discover_failures([row["call"]])
        assert isinstance(found, list)
        for p in found:
            assert p["status"] == "pending_qa"

    elif kind == "revenue":
        found = lab.discover_revenue_leaks([row["call"]], week_id=row.get("week_id", "current"))
        assert len(found) >= row["expect_min"]
        assert found[0]["kind"] == "revenue_leak"
        assert found[0]["status"] == "pending_qa"

    elif kind == "qa_flow":
        assert lab.ingest_call(row["call"])["auto_applied_to_production"] is False
        queue = lab.qa_queue()
        assert queue
        pid = queue[0]["proposal_id"]
        action = row["action"]
        if action == "approve_promote":
            approved = lab.approve(pid, "qa_bot")
            assert approved["proposal"]["status"] == "approved"
            assert approved.get("promoted_to_production") is False
            promoted = lab.promote(pid, "qa_bot")
            assert promoted.get("promoted") is True
            assert lab.quality_snapshot()["production_count"] >= 1
        elif action == "reject":
            rejected = lab.reject(pid, "qa_bot", reason="insufficient evidence")
            assert rejected["proposal"]["status"] == "rejected"
            with pytest.raises(ValueError):
                lab.promote(pid, "qa_bot")
        else:
            edited = lab.edit(pid, "qa_bot", {"title": "Edited by QA", "summary": "patched"})
            assert edited["proposal"]["status"] == "edited"
            assert edited["proposal"]["title"] == "Edited by QA"

    elif kind == "cluster":
        clusters = ClusterEngine().aggregate_corpus(row["phrases"])
        assert len(clusters) >= row["expect_min"]
        assert clusters[0].name
        assert clusters[0].hidden_meaning

    elif kind == "meta":
        lab.ingest_call(row["call"])
        sub = row["sub"]
        if sub == "coaching":
            assert isinstance(lab.generate_coaching(), list)
        elif sub == "self_eval":
            result = lab.self_evaluate({"pattern_accuracy": 0.9})
            assert "metrics" in result
            assert "healthy" in result
        elif sub == "dashboard":
            dash = lab.dashboard()
            assert dash["auto_apply_blocked"] is True
            for key in (
                "new_patterns", "new_intents", "new_objections", "qa_queue",
                "approved_rules", "rejected_rules", "learning_velocity",
                "revenue_impact", "confidence_trend", "knowledge_growth",
            ):
                assert key in dash["widgets"]
        elif sub == "quality":
            q = lab.quality_snapshot()
            assert q["requires_qa"] is True
            assert q["ok"] is True
        elif sub == "growth":
            g = lab.dataset_growth()
            assert g["raw_calls"] >= 1
        else:
            raise AssertionError(sub)

    else:
        raise AssertionError(kind)


def test_no_auto_production_without_qa(tmp_path: Path) -> None:
    lab = SelfLearningLab(store=LearningStore(root=tmp_path))
    out = lab.ingest_call({
        "call_id": "NOAUTO",
        "transcript": "Để em chuyển khoản tối",
        "turns": [{"speaker": "customer", "text": "Để em chuyển khoản tối"}],
        "closed": True,
        "discovery_score": 90,
        "objection_score": 90,
        "duration_sec": 300,
        "outcome": "won",
    })
    assert out["auto_applied_to_production"] is False
    assert lab.quality_snapshot()["production_count"] == 0
    queue = lab.qa_queue()
    assert queue
    with pytest.raises(ValueError, match="approved"):
        lab.promote(queue[0]["proposal_id"], "qa")


def test_promote_after_approve(tmp_path: Path) -> None:
    lab = SelfLearningLab(store=LearningStore(root=tmp_path))
    lab.ingest_call({
        "call_id": "OK",
        "transcript": "Chờ hết tháng cô hồn",
        "turns": [{"speaker": "customer", "text": "Chờ hết tháng cô hồn"}],
        "closed": True,
        "discovery_score": 90,
        "objection_score": 90,
        "duration_sec": 300,
        "outcome": "won",
    })
    pid = lab.qa_queue()[0]["proposal_id"]
    lab.approve(pid, "qa")
    result = lab.promote(pid, "qa")
    assert result["promoted"] is True
    assert lab.quality_snapshot()["production_count"] == 1
