"""CLTV Engine suite — >=1600 cases.

- 1000 CLTV prediction scenarios
- 300 retention scenarios
- 300 churn scenarios
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cltv.engine import CLTVEngine
from cltv.scorer import score_cltv
from cltv.store import CLTVStore


SEGMENTS = ["vip", "premium", "standard", "basic"]
EMOTIONS = ["positive", "curious", "neutral", "frustrated", "resistant"]
INTENTS = ["buy", "consider", "reject", "unknown", "purchase"]


def _lead(i: int) -> dict:
    seg = SEGMENTS[i % len(SEGMENTS)]
    emotion = EMOTIONS[i % len(EMOTIONS)]
    intent = INTENTS[i % len(INTENTS)]
    buy = round((i % 10) / 10 * 0.9 + 0.05, 3)
    converted = i % 4 == 0
    connected = i % 5 != 0
    return {
        "lead_id": f"L{i}",
        "call_history": [
            {
                "connected": connected,
                "converted": converted,
                "qa_score": 40 + (i % 60),
                "duration": 30 + (i % 200),
                "objection_count": i % 3,
            }
        ],
        "conversation_dna": {
            "rapport": round((i % 9) / 10 + 0.1, 2),
            "value_building": round((i % 8) / 10 + 0.15, 2),
            "closing": round((i % 7) / 10 + 0.1, 2),
        },
        "intent": {"label": intent, "confidence": 0.4 + (i % 6) / 10},
        "emotion": emotion,
        "buying_signal": buy,
        "crm": {
            "past_revenue": (i % 12) * 250_000,
            "segment": seg,
            "aov": 300_000 + (i % 5) * 100_000,
            "tenure_days": (i % 20) * 30,
            "support_tickets": i % 4,
            "products_owned": 1 + (i % 3),
        },
        "follow_up": {"completed": i % 5, "missed": i % 3},
    }


@pytest.fixture()
def engine(tmp_path: Path) -> CLTVEngine:
    return CLTVEngine(store=CLTVStore(root=tmp_path))


CLTV_CASES = [{"i": i, **_lead(i)} for i in range(1000)]
RETENTION_CASES = [{"i": i, **_lead(i + 3)} for i in range(300)]
CHURN_CASES = [{"i": i, **_lead(i + 7)} for i in range(300)]


@pytest.mark.parametrize("case", CLTV_CASES, ids=[f"cltv-{c['i']}" for c in CLTV_CASES])
def test_cltv_predictions(engine: CLTVEngine, case: dict) -> None:
    out = engine.predict(
        lead_id=case["lead_id"],
        call_history=case["call_history"],
        conversation_dna=case["conversation_dna"],
        intent=case["intent"],
        emotion=case["emotion"],
        buying_signal=case["buying_signal"],
        crm=case["crm"],
        follow_up=case["follow_up"],
    )
    assert out["ok"] is True
    scores = out["scores"]
    assert 0.0 <= float(scores["cltv_score"]) <= 1.0
    assert float(scores["lifetime_value"]) >= 0.0
    assert scores["priority"] in {"high_value", "medium", "low"}
    assert len(scores["evidence"]) >= 5
    assert len(scores["explanations"]) >= 3
    assert out["priority"] == scores["priority"]


@pytest.mark.parametrize("case", RETENTION_CASES, ids=[f"retention-{c['i']}" for c in RETENTION_CASES])
def test_retention_signals(case: dict) -> None:
    scored = score_cltv(
        lead_id=case["lead_id"],
        call_history=case["call_history"],
        conversation_dna=case["conversation_dna"],
        intent=case["intent"],
        emotion=case["emotion"],
        buying_signal=case["buying_signal"],
        crm=case["crm"],
        follow_up=case["follow_up"],
    )
    assert 0.0 <= scored.retention_score <= 1.0
    assert 0.0 <= scored.repeat_purchase_probability <= 1.0
    if case["follow_up"]["completed"] >= 3 and case["follow_up"]["missed"] == 0:
        assert scored.retention_score >= 0.35


@pytest.mark.parametrize("case", CHURN_CASES, ids=[f"churn-{c['i']}" for c in CHURN_CASES])
def test_churn_signals(case: dict) -> None:
    scored = score_cltv(
        lead_id=case["lead_id"],
        call_history=case["call_history"],
        conversation_dna=case["conversation_dna"],
        intent=case["intent"],
        emotion=case["emotion"],
        buying_signal=case["buying_signal"],
        crm=case["crm"],
        follow_up=case["follow_up"],
    )
    assert 0.0 <= scored.churn_risk <= 1.0
    if case["emotion"] in {"frustrated", "resistant"} and case["follow_up"]["missed"] >= 2:
        assert scored.churn_risk >= 0.35


def test_prioritize_orders_high_value_first(engine: CLTVEngine) -> None:
    leads = [_lead(0), _lead(11), _lead(22)]
    leads[0]["buying_signal"] = 0.95
    leads[0]["crm"]["segment"] = "vip"
    leads[0]["crm"]["past_revenue"] = 5_000_000
    leads[0]["call_history"][0]["converted"] = True
    leads[2]["buying_signal"] = 0.05
    leads[2]["emotion"] = "frustrated"
    leads[2]["intent"] = {"label": "reject", "confidence": 0.9}
    out = engine.prioritize(leads)
    assert out["ok"] is True
    ranked = out["ranked"]
    assert len(ranked) == 3
    top = float((ranked[0].get("scores") or {}).get("cltv_score") or 0)
    bottom = float((ranked[-1].get("scores") or {}).get("cltv_score") or 0)
    assert top >= bottom


def test_quality_gate_passes(engine: CLTVEngine) -> None:
    for i in (1, 2, 3, 50, 90):
        payload = _lead(i)
        engine.predict(
            lead_id=payload["lead_id"],
            call_history=payload["call_history"],
            conversation_dna=payload["conversation_dna"],
            intent=payload["intent"],
            emotion=payload["emotion"],
            buying_signal=payload["buying_signal"],
            crm=payload["crm"],
            follow_up=payload["follow_up"],
        )
    snap = engine.quality_snapshot()
    assert snap["ok"] is True
    assert snap["separation_ok"] is True
    for key in ("forecast_accuracy", "stability", "explainability", "evidence_validation"):
        assert snap["checks"][key]["ok"] is True


def test_dashboard_widgets(engine: CLTVEngine) -> None:
    payload = _lead(5)
    engine.predict(
        lead_id=payload["lead_id"],
        call_history=payload["call_history"],
        conversation_dna=payload["conversation_dna"],
        intent=payload["intent"],
        emotion=payload["emotion"],
        buying_signal=payload["buying_signal"],
        crm=payload["crm"],
        follow_up=payload["follow_up"],
    )
    dash = engine.dashboard()
    widgets = dash["widgets"]
    for key in (
        "cltv_forecast",
        "churn_forecast",
        "avg_lifetime_value",
        "upsell_opportunity",
        "referral_opportunity",
        "prediction_count",
        "priority_counts",
    ):
        assert key in widgets
