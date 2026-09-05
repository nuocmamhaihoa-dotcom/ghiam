"""Application service wrapper for Sales OS."""
from __future__ import annotations

from typing import Any

from sales_os import SalesOS, get_sales_os


class SalesOSService:
    def __init__(self, os: SalesOS | None = None) -> None:
        self._os = os or get_sales_os()

    def connectors(self) -> list[dict[str, Any]]:
        return self._os.list_connectors()

    def crm_sync(self, connector: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._os.crm_sync(connector, payload)

    def sync_all(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._os.sync_all(payload)

    def lead_score(self, lead: dict[str, Any]) -> dict[str, Any]:
        return self._os.lead_score(lead)

    def route_lead(
        self,
        lead: dict[str, Any],
        agents: list[dict[str, Any]],
        hour: int | None = None,
    ) -> dict[str, Any]:
        return self._os.route_lead(lead, agents, hour=hour)

    def next_best_action(self, call: dict[str, Any], automate: bool = False) -> dict[str, Any]:
        return self._os.next_best_action(call, automate=automate)

    def forecast(
        self,
        historical: list[dict[str, Any]] | None = None,
        pipeline: list[dict[str, Any]] | None = None,
        horizon_days: int = 30,
    ) -> dict[str, Any]:
        return self._os.forecast(historical, pipeline, horizon_days)

    def calendar_sync(self, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self._os.calendar_sync(events)

    def schedule_call(self, **kwargs: Any) -> dict[str, Any]:
        return self._os.schedule_call(**kwargs)

    def trigger_automation(self, job_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._os.trigger_automation(job_type, payload)

    def dashboard(self, role: str = "CEO", extras: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._os.dashboard(role, extras)

    def quality(self) -> dict[str, Any]:
        return self._os.quality_snapshot()

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._os.audit.list(limit)
