"""Smoke tests for scoring response shape (no DB required for pure domain)."""

from app.domain.enums import Verdict
from app.domain.value_objects import insufficient_evidence_response


def test_insufficient_evidence_shape() -> None:
    resp = insufficient_evidence_response()
    data = resp.to_dict()
    assert set(data.keys()) >= {
        "score",
        "stage_scores",
        "violations",
        "evidence",
        "root_cause",
        "coaching",
        "revenue_leak",
    }
    assert data["score"] == 0
    assert data["stage_scores"] == {}
    assert data["violations"] == []
    assert data["evidence"] == []
    assert data["root_cause"]["verdict"] == "Insufficient Evidence"
    assert data["revenue_leak"]["verdict"] == "Insufficient Evidence"


def test_verdict_enum() -> None:
    assert Verdict.INSUFFICIENT_EVIDENCE.value == "Insufficient Evidence"
