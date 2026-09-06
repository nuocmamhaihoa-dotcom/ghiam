"""Sales OS API — Lead Score, NBA, Forecast, CRM Sync, Calendar, Call Schedule, Automation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.sales_os import SalesOSService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/sales-os", tags=["sales-os"])


class LeadScoreRequest(BaseModel):
    lead: dict[str, Any]


class RouteRequest(BaseModel):
    lead: dict[str, Any]
    agents: list[dict[str, Any]]
    hour: int | None = None


class NbaRequest(BaseModel):
    call: dict[str, Any]
    automate: bool = False


class ForecastRequest(BaseModel):
    historical: list[dict[str, Any]] = Field(default_factory=list)
    pipeline: list[dict[str, Any]] = Field(default_factory=list)
    horizon_days: int = 30


class CrmSyncRequest(BaseModel):
    connector: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncAllRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class CalendarSyncRequest(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)


class CallScheduleRequest(BaseModel):
    lead_id: str
    agent_id: str
    when: str | None = None
    duration_min: int = 15
    title: str | None = None


class AutomationTriggerRequest(BaseModel):
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DashboardRequest(BaseModel):
    role: str = "CEO"
    extras: dict[str, Any] = Field(default_factory=dict)


@router.get("/connectors")
async def list_connectors(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = SalesOSService().connectors()
    return {"status": "ok", "connectors": items, "count": len(items)}


@router.post("/lead-score")
async def lead_score(
    body: LeadScoreRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().lead_score(body.lead)


@router.post("/route")
async def route_lead(
    body: RouteRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().route_lead(body.lead, body.agents, body.hour)


@router.post("/next-best-action")
async def next_best_action(
    body: NbaRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().next_best_action(body.call, automate=body.automate)


@router.post("/forecast")
async def forecast(
    body: ForecastRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().forecast(body.historical, body.pipeline, body.horizon_days)


@router.post("/crm-sync")
async def crm_sync(
    body: CrmSyncRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().crm_sync(body.connector, body.payload)


@router.post("/sync-all")
async def sync_all(
    body: SyncAllRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().sync_all(body.payload)


@router.post("/calendar-sync")
async def calendar_sync(
    body: CalendarSyncRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().calendar_sync(body.events)


@router.post("/call-schedule")
async def call_schedule(
    body: CallScheduleRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().schedule_call(
        lead_id=body.lead_id,
        agent_id=body.agent_id,
        when=body.when,
        duration_min=body.duration_min,
        title=body.title,
    )


@router.post("/automation/trigger")
async def automation_trigger(
    body: AutomationTriggerRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().trigger_automation(body.job_type, body.payload)


@router.post("/dashboard")
async def dashboard(
    body: DashboardRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().dashboard(body.role, body.extras)


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesOSService().quality()


@router.get("/audit")
async def audit_log(
    limit: int = 100,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    entries = SalesOSService().audit(limit)
    return {"status": "ok", "entries": entries, "count": len(entries)}
