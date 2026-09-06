"""Sales OS suite (>5000 cases): CRM sync, routing, forecast, automation, calendar, permissions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.engine import AutomationEngine
from forecast.engine import ForecastEngine
from integrations.registry import get_registry
from routing.nba import NextBestActionEngine
from routing.router import LeadRouter
from sales_os import SalesOS
from sales_os.dashboard import ROLE_WIDGETS, WIDGETS, build_dashboard

FIX = Path(__file__).with_name("fixtures_5000.jsonl")
FIXTURES = [json.loads(line) for line in FIX.read_text(encoding="utf-8").splitlines() if line.strip()]

AGENTS = [
    {
        "agent_id": "A1",
        "name": "An",
        "telesale_skill": 0.92,
        "conversation_dna": "consultative",
        "close_rate": 0.31,
        "industry_experience": ["banking", "insurance"],
        "peak_hours": list(range(9, 18)),
        "workload": 2,
        "active": True,
    },
    {
        "agent_id": "A2",
        "name": "Binh",
        "telesale_skill": 0.65,
        "conversation_dna": "assertive",
        "close_rate": 0.2,
        "industry_experience": ["telecom", "retail"],
        "peak_hours": [10, 11, 14, 15],
        "workload": 8,
        "active": True,
    },
    {
        "agent_id": "A3",
        "name": "Chi",
        "telesale_skill": 0.84,
        "conversation_dna": "empathic",
        "close_rate": 0.27,
        "industry_experience": ["banking", "education"],
        "peak_hours": [9, 10, 16, 17],
        "workload": 4,
        "active": True,
    },
    {
        "agent_id": "A4",
        "name": "Dung",
        "telesale_skill": 0.78,
        "conversation_dna": "analytical",
        "close_rate": 0.25,
        "industry_experience": ["insurance", "retail"],
        "peak_hours": list(range(13, 18)),
        "workload": 5,
        "active": True,
    },
]


@pytest.fixture(scope="module")
def sos() -> SalesOS:
    return SalesOS()


@pytest.mark.parametrize("row", FIXTURES, ids=[r["id"] for r in FIXTURES])
def test_sales_os_fixture(sos: SalesOS, row: dict) -> None:
    kind = row["kind"]

    if kind == "crm_sync":
        out = sos.crm_sync(row["connector"], {"records": row["records"], "dry_run": True})
        assert out["ok"] is True
        assert out["records_in"] == out["records_out"] == row["records"]
        assert not out.get("errors")

    elif kind == "routing":
        lead = row["lead"]
        decision = sos.route_lead(lead, AGENTS, hour=row.get("hour"))
        assert decision["lead_id"] == lead["lead_id"]
        assert decision["agent_id"] in {a["agent_id"] for a in AGENTS}
        assert 0 <= decision["lead_score"] <= 100
        assert 0 <= decision["success_probability"] <= 1
        assert decision["assignment_reason"]
        assert decision["evidence"]
        if (
            lead["industry"] == "banking"
            and lead["dna_preference"] == "consultative"
            and row.get("hour") in range(9, 18)
        ):
            assert decision["agent_id"] == "A1"

    elif kind == "forecast":
        historical = [
            {
                "conversion_rate": row["base_rate"] + (j * 0.005),
                "revenue": row["base_rev"] + j * 1_000_000,
            }
            for j in range(row["points"])
        ]
        pipeline = [
            {
                "value": 20_000_000 * (k + 1),
                "stage": ["new", "qualified", "proposal", "negotiation"][k % 4],
                "days_in_stage": 3 + k,
            }
            for k in range(row["pipeline_n"])
        ]
        fc = sos.forecast(historical, pipeline, horizon_days=30)
        assert fc["status"] == "ok"
        assert 0 <= fc["close_rate"] <= 1
        assert fc["weekly_revenue"] >= 0
        assert fc["monthly_revenue"] >= 0
        assert fc["quarterly_revenue"] >= fc["monthly_revenue"]
        assert 0 <= fc["pipeline_risk"] <= 1
        assert 0 <= fc["confidence"] <= 1
        fc2 = ForecastEngine().forecast(historical=historical, pipeline=pipeline).to_dict()
        assert fc2["close_rate"] == fc["close_rate"]
        assert fc2["monthly_revenue"] == fc["monthly_revenue"]

    elif kind == "automation":
        job = sos.trigger_automation(row["job_type"], row["payload"])
        assert job["status"] == "completed"
        assert job["result"]["ok"] is True
        assert job["job_type"] == row["job_type"]

    elif kind == "nba":
        call = dict(row["call"])
        call["agent_id"] = "A1"
        out = sos.next_best_action(call, automate=row["automate"])
        assert out["action"] in set(NextBestActionEngine.ACTIONS)
        assert 0 <= out["confidence"] <= 1
        assert out["evidence"]
        assert out["expected_impact"]
        if row["automate"]:
            assert isinstance(out.get("automation_jobs"), list)
            assert len(out["automation_jobs"]) >= 1

    elif kind == "calendar":
        events = [{"title": f"evt-{i}", "lead_id": row["lead_id"]} for i in range(row["events"])]
        sync = sos.calendar_sync(events)
        assert sync["ok"] is True
        scheduled = sos.schedule_call(lead_id=row["lead_id"], agent_id=row["agent_id"])
        assert scheduled["ok"] is True
        assert scheduled["event"]["lead_id"] == row["lead_id"]

    elif kind == "permission":
        if row["allowed_dashboard"]:
            dash = build_dashboard(
                role=row["role"],
                forecast={
                    "weekly_revenue": 1,
                    "monthly_revenue": 1,
                    "quarterly_revenue": 1,
                    "close_rate": 0.2,
                    "pipeline_risk": 0.2,
                    "confidence": 0.7,
                },
            )
            assert dash["status"] == "ok"
            assert dash["role"] == row["role"]
            assert set(dash["widget_names"]).issubset(set(WIDGETS))
            assert set(dash["widget_names"]) == set(ROLE_WIDGETS[row["role"]])
        else:
            dash = build_dashboard(role=row["role"])
            assert dash["status"] == "ok"
            assert dash["role"] == "CEO"

    elif kind == "dashboard":
        dash = sos.dashboard(row["role"])
        assert dash["status"] == "ok"
        assert dash["role"] == row["role"]
        assert len(dash["widget_names"]) >= 5
        for name in dash["widget_names"]:
            assert name in dash["widgets"]

    else:
        raise AssertionError(f"unknown kind {kind}")


def test_connector_registry_complete() -> None:
    keys = set(get_registry().keys())
    required = {
        "hubspot",
        "salesforce",
        "bitrix24",
        "zoho",
        "asterisk",
        "threecx",
        "callio",
        "stringee",
        "twilio",
        "facebook_lead_ads",
        "zalo_oa",
        "google_sheets",
        "google_calendar",
    }
    assert required.issubset(keys)


def test_sync_all_no_data_loss(sos: SalesOS) -> None:
    out = sos.sync_all({"records": 7, "dry_run": True})
    assert out["ok"] is True
    for r in out["results"]:
        assert r["ok"] is True
        assert r["records_in"] == r["records_out"]


def test_quality_snapshot(sos: SalesOS) -> None:
    q = sos.quality_snapshot()
    assert q["crm_sync_ok"] is True
    assert q["no_data_loss"] is True
    assert q["connectors"] >= 13
    assert q["audit_entries"] >= 1


def test_audit_log_grows(sos: SalesOS) -> None:
    before = len(sos.audit)
    sos.lead_score({"lead_id": "AUD-1", "urgency": 0.5})
    assert len(sos.audit) >= before + 1


def test_router_explainability() -> None:
    decision = LeadRouter().route(
        {
            "lead_id": "X1",
            "industry": "banking",
            "dna_preference": "consultative",
            "urgency": 0.9,
            "value": 100_000_000,
            "preferred_hour": 10,
        },
        AGENTS,
        hour=10,
    )
    assert decision.agent_id == "A1"
    assert decision.assignment_reason


def test_automation_nba_pipeline() -> None:
    jobs = AutomationEngine().run_nba_pipeline(
        {"action": "callback", "schedule_at": "+2h"},
        context={"lead_id": "L9", "agent_id": "A1", "score": 40},
    )
    types = {j.job_type for j in jobs}
    assert "callback_reminder" in types
    assert "create_task" in types
    assert "create_coaching" in types
