"""Dashboard overview and KPI endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.application.services.dashboard import DashboardService
from app.core.deps import CurrentUser, DbSession, require_permissions

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.get("/overview")
async def overview(
    user: Annotated[CurrentUser, Depends(require_permissions("dashboard:read"))],
    session: DbSession,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    service = DashboardService(session)
    return await service.overview(window_from=_parse_dt(from_), window_to=_parse_dt(to))


@router.get("/summary")
async def summary(
    user: Annotated[CurrentUser, Depends(require_permissions("dashboard:read"))],
    session: DbSession,
) -> dict[str, Any]:
    data = await DashboardService(session).overview()
    return {
        "calls_total": data["calls_total"],
        "calls_scored": data["scored"],
        "rules_active": 0,
        "avg_score": data["avg_score"],
        "insufficient_evidence": data["insufficient_evidence"],
        "failed": data["failed"],
        "estimated_revenue_leak_vnd": data["estimated_revenue_leak_vnd"],
    }
