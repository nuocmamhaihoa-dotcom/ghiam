"""War Room AI suite — >=1000 cases.

- 500 live floor scenarios
- 300 alert detection scenarios
- 200 realtime / quality scenarios
"""
from __future__ import annotations

from pathlib import Path

import pytest

from war_room.alerts import detect_alerts
from war_room.engine import WarRoomEngine
from war_room.store import WarRoomStore
from war_room.types import ALERT_KINDS


SENTIMENTS = ["positive", "curious", "neutral", "frustrated", "resistant", "negative"]
OBJECTIONS = [None, "price", "timing", "trust", "competitor", "authority"]
STATUSES = ["available", "on_call", "break", "offline"]


def _scenario(i: int) -> dict:
    conversion = max(0.05, 0.4 - (i % 20) * 0.02)
    baseline = 0.28
    queue = 5 + (i % 40)
    wait = 10 + (i % 100)
    agents = []
    for a in range(3):
        agents.append(
            {
                "agent_id": f"A{i}-{a}",
                "name": f"Agent-{a}",
                "status": STATUSES[(i + a) % len(STATUSES)],
                "active_calls": 0 if (i + a) % 4 == 0 else 1,
                "idle_sec": 50 + ((i * 17 + a * 40) % 500),
                "conversion_rate": conversion,
            }
        )
    calls = []
    for c in range(5 + (i % 3)):
        calls.append(
            {
                "call_id": f"C{i}-{c}",
                "agent_id": agents[c % len(agents)]["agent_id"],
                "lead_id": f"L{i}-{c}",
                "status": "live",
                "buy_signal": round((c + i % 7) / 10, 2),
                "sentiment": SENTIMENTS[(i + c) % len(SENTIMENTS)],
                "objection": OBJECTIONS[(i + c) % len(OBJECTIONS)],
            }
        )
    return {
        "i": i,
        "agents": agents,
        "calls": calls,
        "kpi": {
            "conversion_rate": conversion,
            "baseline_conversion": baseline,
            "queue_size": queue,
            "avg_wait_sec": wait,
        },
    }


LIVE_CASES = [_scenario(i) for i in range(500)]
ALERT_CASES = [_scenario(i + 11) for i in range(300)]
QUALITY_CASES = [_scenario(i + 29) for i in range(200)]


@pytest.fixture()
def engine(tmp_path: Path) -> WarRoomEngine:
    return WarRoomEngine(store=WarRoomStore(root=tmp_path))


@pytest.mark.parametrize("case", LIVE_CASES, ids=[f"live-{c['i']}" for c in LIVE_CASES])
def test_live_floor_scenarios(engine: WarRoomEngine, case: dict) -> None:
    for agent in case["agents"]:
        out = engine.upsert_agent(agent)
        assert out["ok"] is True
        assert out["agent"]["agent_id"]
    for call in case["calls"]:
        out = engine.upsert_call(call)
        assert out["ok"] is True
        assert 0.0 <= float(out["call"]["buy_signal"]) <= 1.5
    kpi = engine.update_kpi(case["kpi"])
    assert kpi["ok"] is True
    snap = engine.snapshot()
    assert snap["ok"] is True
    widgets = snap["widgets"]
    for key in (
        "active_calls",
        "online_agents",
        "queue_size",
        "avg_wait_sec",
        "conversion_rate",
        "open_alerts",
        "critical_alerts",
        "avg_buy_signal",
    ):
        assert key in widgets
    assert widgets["active_calls"] == len(case["calls"])
    assert widgets["queue_size"] == case["kpi"]["queue_size"]
    dash = engine.dashboard()
    assert dash["ok"] is True
    assert dash["realtime_enabled"] is True
    assert dash["widgets"]["active_calls"] == widgets["active_calls"]


@pytest.mark.parametrize("case", ALERT_CASES, ids=[f"alert-{c['i']}" for c in ALERT_CASES])
def test_alert_detection(case: dict) -> None:
    alerts = detect_alerts(agents=case["agents"], calls=case["calls"], kpi=case["kpi"])
    for alert in alerts:
        assert alert.kind in ALERT_KINDS
        assert alert.severity in {"critical", "high", "medium", "low"}
        assert alert.evidence
        assert alert.recommended_action
        payload = alert.to_dict()
        assert payload["alert_id"]
        assert payload["title"]
        assert payload["message"]

    kpi = case["kpi"]
    kinds = {a.kind for a in alerts}
    if kpi["conversion_rate"] < kpi["baseline_conversion"] * 0.7:
        assert "conversion_drop" in kinds
    if kpi["avg_wait_sec"] >= 45:
        assert "sla_breach" in kinds
    if kpi["queue_size"] >= 20:
        assert "queue_overflow" in kinds


@pytest.mark.parametrize("case", QUALITY_CASES, ids=[f"quality-{c['i']}" for c in QUALITY_CASES])
def test_realtime_quality(engine: WarRoomEngine, case: dict) -> None:
    for agent in case["agents"]:
        engine.upsert_agent(agent)
    for call in case["calls"]:
        engine.upsert_call(call)
    engine.update_kpi(case["kpi"])
    scanned = engine.scan_alerts()
    assert scanned["ok"] is True
    assert scanned["count"] == len(scanned["alerts"])
    for alert in scanned["alerts"]:
        assert alert["kind"] in ALERT_KINDS
        assert alert["evidence"]
        assert alert["recommended_action"]
    if scanned["alerts"]:
        ack = engine.acknowledge_alert(scanned["alerts"][0]["alert_id"])
        assert ack["ok"] is True
        assert ack["alert"]["acknowledged"] is True
    quality = engine.quality_snapshot()
    assert "checks" in quality
    assert quality["realtime_enabled"] is True
    for key in ("alert_precision", "freshness", "coverage", "evidence_validation"):
        assert key in quality["checks"]
        assert "ok" in quality["checks"][key]
        assert "value" in quality["checks"][key]
    assert quality["ok"] is True
