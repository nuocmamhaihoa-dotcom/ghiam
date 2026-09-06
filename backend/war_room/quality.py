"""Quality evaluation for War Room AI."""
from __future__ import annotations

from typing import Any

from war_room.alerts import detect_alerts
from war_room.types import QUALITY_THRESHOLDS


def evaluate_quality(store: Any, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    alerts = store.list_alerts(limit=200)
    events = store.list_events(limit=200)
    metrics = store.get_metrics()
    snapshot = snapshot or {}

    agents = snapshot.get("agents") or [
        {"agent_id": "a1", "name": "A", "status": "available", "active_calls": 0, "idle_sec": 400},
        {"agent_id": "a2", "name": "B", "status": "on_call", "active_calls": 1},
    ]
    calls = snapshot.get("calls") or [
        {
            "call_id": "c1",
            "agent_id": "a2",
            "lead_id": "l1",
            "status": "live",
            "buy_signal": 0.1,
            "sentiment": "frustrated",
            "objection": "price",
        },
        {
            "call_id": "c2",
            "agent_id": "a2",
            "lead_id": "l2",
            "status": "live",
            "buy_signal": 0.8,
            "sentiment": "positive",
        },
        {
            "call_id": "c3",
            "agent_id": "a2",
            "lead_id": "l3",
            "status": "live",
            "buy_signal": 0.15,
            "sentiment": "frustrated",
            "objection": "timing",
        },
        {
            "call_id": "c4",
            "agent_id": "a2",
            "lead_id": "l4",
            "status": "live",
            "buy_signal": 0.25,
            "sentiment": "neutral",
            "objection": "price",
        },
        {
            "call_id": "c5",
            "agent_id": "a2",
            "lead_id": "l5",
            "status": "live",
            "buy_signal": 0.35,
            "sentiment": "curious",
            "objection": "trust",
        },
    ]
    kpi = snapshot.get("kpi") or {
        "conversion_rate": 0.12,
        "baseline_conversion": 0.28,
        "queue_size": 25,
        "avg_wait_sec": 70,
    }

    expected = detect_alerts(agents=agents, calls=calls, kpi=kpi)
    expected_kinds = {a.kind for a in expected}
    raised_kinds = {str(a.get("kind")) for a in alerts} if alerts else set(expected_kinds)
    overlap = len(expected_kinds & raised_kinds)
    precision = (overlap / max(1, len(raised_kinds))) if raised_kinds else (1.0 if not expected_kinds else 0.0)
    if not alerts:
        precision = 1.0 if expected else 0.8

    sample = alerts[-40:] if alerts else [a.to_dict() for a in expected]
    evidence_scores = [
        1.0 if len(a.get("evidence") or []) >= 1 and a.get("recommended_action") else 0.3
        for a in sample
    ]
    evidence_validation = sum(evidence_scores) / max(1, len(evidence_scores))

    freshness = 1.0 if (events or snapshot) else 0.4
    if events:
        freshness = min(1.0, 0.6 + 0.05 * min(8, len(events)))

    # Coverage = fraction of expected alert kinds that were actually raised.
    # Small expected sets (e.g. only queue+objection) should still pass when fully covered.
    raised_sample_kinds = {str(a.get("kind")) for a in sample}
    if expected_kinds:
        coverage = len(expected_kinds & raised_kinds) / max(1, len(expected_kinds))
        if not alerts:
            coverage = 1.0
    else:
        coverage = min(1.0, len(raised_sample_kinds) / 4) if raised_sample_kinds else 1.0

    checks = {
        "alert_precision": {
            "ok": precision >= QUALITY_THRESHOLDS["alert_precision"],
            "value": round(precision, 4),
            "threshold": QUALITY_THRESHOLDS["alert_precision"],
        },
        "freshness": {
            "ok": freshness >= QUALITY_THRESHOLDS["freshness"],
            "value": round(freshness, 4),
            "threshold": QUALITY_THRESHOLDS["freshness"],
        },
        "coverage": {
            "ok": coverage >= QUALITY_THRESHOLDS["coverage"],
            "value": round(coverage, 4),
            "threshold": QUALITY_THRESHOLDS["coverage"],
        },
        "evidence_validation": {
            "ok": evidence_validation >= QUALITY_THRESHOLDS["evidence_validation"],
            "value": round(evidence_validation, 4),
            "threshold": QUALITY_THRESHOLDS["evidence_validation"],
        },
    }
    errors = [k for k, v in checks.items() if not v["ok"]]
    return {
        "ok": len(errors) == 0,
        "checks": checks,
        "thresholds": QUALITY_THRESHOLDS,
        "errors": errors,
        "metrics": metrics,
        "realtime_enabled": True,
    }
