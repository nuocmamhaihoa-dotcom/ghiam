"""Revenue leak APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.application.services.dashboard import DashboardService
from app.core.deps import CurrentUser, DbSession, require_permissions

router = APIRouter(prefix="/revenue-leak", tags=["revenue-leak"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@router.get("/summary")
async def revenue_leak_summary(
    user: Annotated[CurrentUser, Depends(require_permissions("revenue:read"))],
    session: DbSession,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    return await DashboardService(session).revenue_leak_summary(
        window_from=_parse_dt(from_),
        window_to=_parse_dt(to),
    )
