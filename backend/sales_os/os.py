"""Sales OS façade — CRM, telephony, AI routing, forecast, automation, dashboard."""
from __future__ import annotations

from typing import Any

from automation.engine import AutomationEngine
from forecast.engine import ForecastEngine
from integrations.registry import get_registry
from routing.nba import NextBestActionEngine
from routing.router import LeadRouter
from sales_os.audit import AuditLog
from sales_os.calendar import CalendarService
from sales_os.dashboard import build_dashboard


class SalesOS:
    """Central AI Sales Operating System."""

    def __init__(self) -> None:
        self.connectors = get_registry()
        self.router = LeadRouter()
        self.nba = NextBestActionEngine()
        self.forecast_engine = ForecastEngine()
        self.automation = AutomationEngine()
        self.calendar = CalendarService()
        self.audit = AuditLog()
        self._routing_history: list[dict[str, Any]] = []
        self._nba_history: list[dict[str, Any]] = []

    def list_connectors(self) -> list[dict[str, Any]]:
        return [
            {"key": p.key, "name": p.name, "kind": p.kind, "health": p.health()}
            for p in self.connectors.list()
        ]

    def crm_sync(self, connector: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.connectors.sync(connector, payload)
        data = result.to_dict()
        self.audit.record("crm_sync", resource=connector, details=data)
        if data.get("ok") and data.get("records_in") != data.get("records_out"):
            data["ok"] = False
            data["errors"] = list(data.get("errors") or []) + ["data_loss_detected"]
        return data

    def sync_all(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        results = [r.to_dict() for r in self.connectors.sync_all(payload)]
        ok = all(r.get("ok") for r in results)
        self.audit.record("sync_all", details={"count": len(results), "ok": ok})
        return {"ok": ok, "results": results}

    def lead_score(self, lead: dict[str, Any]) -> dict[str, Any]:
        score = self.router.score_lead(lead)
        self.audit.record("lead_score", resource=str(lead.get("lead_id")), details={"score": score})
        return {"lead_id": lead.get("lead_id"), "lead_score": score, "status": "ok"}

    def route_lead(
        self,
        lead: dict[str, Any],
        agents: list[dict[str, Any]],
        hour: int | None = None,
    ) -> dict[str, Any]:
        decision = self.router.route(lead, agents, hour=hour)
        data = decision.to_dict()
        self._routing_history.append(data)
        self.audit.record(
            "lead_route",
            resource=decision.lead_id,
            details={"agent_id": decision.agent_id},
        )
        return data

    def next_best_action(self, call: dict[str, Any], *, automate: bool = False) -> dict[str, Any]:
        nba = self.nba.decide(call).to_dict()
        self._nba_history.append(nba)
        self.audit.record(
            "nba",
            resource=str(call.get("lead_id") or call.get("call_id")),
            details=nba,
        )
        if automate:
            jobs = self.automation.run_nba_pipeline(nba, context=call)
            nba["automation_jobs"] = [j.to_dict() for j in jobs]
        return nba

    def forecast(
        self,
        historical: list[dict[str, Any]] | None = None,
        pipeline: list[dict[str, Any]] | None = None,
        horizon_days: int = 30,
    ) -> dict[str, Any]:
        result = self.forecast_engine.forecast(
            historical=historical,
            pipeline=pipeline,
            horizon_days=horizon_days,
        ).to_dict()
        self.audit.record(
            "forecast",
            details={"horizon_days": horizon_days, "status": result.get("status")},
        )
        return result

    def calendar_sync(self, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        conn = self.crm_sync("google_calendar", {"records": max(1, len(events or [1]))})
        cal = self.calendar.sync(events)
        out = {
            "connector": conn,
            "calendar": cal,
            "ok": bool(conn.get("ok") and cal.get("ok")),
        }
        self.audit.record("calendar_sync", details=out)
        return out

    def schedule_call(self, **kwargs: Any) -> dict[str, Any]:
        event = self.calendar.schedule_call(**kwargs)
        self.audit.record(
            "call_schedule",
            resource=str(kwargs.get("lead_id", "")),
            details=event,
        )
        return event

    def trigger_automation(
        self, job_type: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        job = self.automation.trigger(job_type, payload)  # type: ignore[arg-type]
        self.audit.record("automation_trigger", resource=job_type, details=job.to_dict())
        return job.to_dict()

    def dashboard(self, role: str = "CEO", extras: dict[str, Any] | None = None) -> dict[str, Any]:
        extras = extras or {}
        fc = self.forecast_engine.forecast(
            historical=extras.get("historical")
            or [
                {"conversion_rate": 0.18, "revenue": 90_000_000},
                {"conversion_rate": 0.2, "revenue": 100_000_000},
                {"conversion_rate": 0.22, "revenue": 110_000_000},
            ],
            pipeline=extras.get("pipeline")
            or [{"value": 50_000_000, "stage": "proposal", "days_in_stage": 5}],
        ).to_dict()
        routing_stats = {
            "assigned": len(self._routing_history),
            "unassigned": 0,
            "avg_lead_score": (
                sum(r.get("lead_score", 0) for r in self._routing_history)
                / len(self._routing_history)
                if self._routing_history
                else 72.0
            ),
            "avg_confidence": (
                sum(r.get("success_probability", 0) for r in self._routing_history)
                / len(self._routing_history)
                if self._routing_history
                else 0.78
            ),
        }
        auto_stats = {
            "coaching": sum(
                1
                for j in self.automation.list_jobs()
                if j.get("job_type") == "create_coaching"
            )
        }
        dash = build_dashboard(
            role=role,
            forecast=fc,
            routing_stats=routing_stats,
            automation_stats=auto_stats,
            extras=extras,
        )
        self.audit.record(
            "dashboard",
            resource=role,
            details={"widgets": dash.get("widget_names")},
        )
        return dash

    def quality_snapshot(self) -> dict[str, Any]:
        sync = self.sync_all({"records": 3, "dry_run": True})
        return {
            "crm_sync_ok": sync["ok"],
            "no_data_loss": all(
                (not r.get("ok")) or r.get("records_in") == r.get("records_out")
                for r in sync["results"]
            ),
            "connectors": len(self.connectors.keys()),
            "audit_entries": len(self.audit),
            "automation_jobs": len(self.automation.list_jobs()),
            "routing_decisions": len(self._routing_history),
        }


_OS: SalesOS | None = None


def get_sales_os() -> SalesOS:
    global _OS
    if _OS is None:
        _OS = SalesOS()
    return _OS


__all__ = ["SalesOS", "get_sales_os"]
