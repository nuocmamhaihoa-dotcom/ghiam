"""Unit tests for root cause, coaching, revenue leak services."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.application.services.coaching import CoachingService
from app.application.services.revenue_leak import RevenueLeakService
from app.application.services.root_cause import RootCauseService
from app.domain.entities import CallEntity, RuleEntity
from app.domain.enums import (
    CallDirection,
    CallStatus,
    RuleSeverity,
    RuleStatus,
    Verdict,
)
from app.domain.value_objects import EvidenceSpanRef, ScoreItemResult, insufficient_evidence_response


def _span() -> EvidenceSpanRef:
    return EvidenceSpanRef(
        id=uuid4(),
        quote="Giá hơi cao",
        audio_ts_start=10.0,
        audio_ts_end=12.0,
        turn_index=4,
        confidence=0.91,
        stage_key="objection",
        slot="price",
        speaker="customer",
    )


def _item(verdict: Verdict = Verdict.FAIL) -> ScoreItemResult:
    return ScoreItemResult(
        rule_code="R-OBJ-01",
        title="Handle price objection",
        verdict=verdict,
        score=0.0 if verdict == Verdict.FAIL else 1.0,
        weight=2.0,
        confidence=0.88,
        evaluated_at=datetime.now(timezone.utc),
        explanation="No value reframe after price pushback.",
        evidence_spans=[_span()],
        scoring_path=["rule_engine"],
        category="objection",
        severity="major",
        auto_fail=False,
    )


def _rule() -> RuleEntity:
    return RuleEntity(
        id=uuid4(),
        rule_code="R-OBJ-01",
        category="objection",
        title="Handle price objection",
        description="Must reframe value after price objection",
        severity=RuleSeverity.MAJOR,
        weight=2.0,
        auto_fail=False,
        status=RuleStatus.ACTIVE,
        evaluator_type="keyword",
        evidence_requirements={},
        evaluator_config={},
        current_version=1,
        cause_code_on_fail="RC-OBJ-PRICE-UNHANDLED",
        coaching_template_code="CT-OBJ-PRICE",
        revenue_impact_code="LEAK_PRICE_OBJECTION_MISS",
    )


def test_canonical_scoring_keys() -> None:
    data = insufficient_evidence_response().to_dict()
    assert set(data) >= {
        "score",
        "stage_scores",
        "violations",
        "evidence",
        "root_cause",
        "coaching",
        "revenue_leak",
    }
    assert data["root_cause"]["status"] == "Insufficient Evidence"
    assert data["revenue_leak"]["estimated_loss_vnd"] is None
    assert data["coaching"]["tips"] == []


def test_root_cause_identified() -> None:
    rc = RootCauseService().analyze([_item()], {"R-OBJ-01": _rule()})
    assert rc["verdict"] == "identified"
    assert rc["primary_code"] == "RC-OBJ-PRICE-UNHANDLED"
    assert rc["primary_cause_code"] == "RC-OBJ-PRICE-UNHANDLED"
    assert rc["status"] == "ok"
    assert rc["children"]


def test_root_cause_insufficient() -> None:
    rc = RootCauseService().analyze(
        [_item(Verdict.INSUFFICIENT_EVIDENCE)], {"R-OBJ-01": _rule()}
    )
    assert rc["verdict"] == "Insufficient Evidence"
    assert rc["primary_code"] is None


def test_coaching_tips() -> None:
    rc = RootCauseService().analyze([_item()], {"R-OBJ-01": _rule()})
    tips = CoachingService().generate_call_tips([_item()], {"R-OBJ-01": _rule()}, rc)
    assert tips["priority"] in {"high", "medium", "low"}
    assert len(tips["tips"]) == 1
    assert tips["tips"][0]["linked_rule_ids"] == ["R-OBJ-01"]
    assert tips["drill_ids"]


def test_revenue_leak_estimated() -> None:
    call = CallEntity(
        id=uuid4(),
        external_call_id="ext-1",
        status=CallStatus.PROCESSING,
        direction=CallDirection.OUTBOUND,
        agent_user_id=None,
        tenant_id=None,
        crm_order_value=500_000,
        currency="VND",
    )
    leak = RevenueLeakService().estimate(
        call=call, items=[_item()], rules_by_code={"R-OBJ-01": _rule()}
    )
    assert leak["verdict"] == "estimated"
    assert leak["estimated_loss_vnd"] == 75000.0
    assert "LEAK_PRICE_OBJECTION_MISS" in leak["leak_codes"]


def test_revenue_leak_insufficient_without_order_value() -> None:
    call = CallEntity(
        id=uuid4(),
        external_call_id="ext-2",
        status=CallStatus.PROCESSING,
        direction=CallDirection.OUTBOUND,
        agent_user_id=None,
        tenant_id=None,
        crm_order_value=None,
    )
    leak = RevenueLeakService().estimate(
        call=call, items=[_item()], rules_by_code={"R-OBJ-01": _rule()}
    )
    assert leak["verdict"] == "Insufficient Evidence"
    assert leak["estimated_loss_vnd"] is None
