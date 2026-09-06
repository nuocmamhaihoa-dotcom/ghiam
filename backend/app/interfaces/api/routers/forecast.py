"""Sales Forecast API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.sales_forecast import SalesForecastService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/forecast", tags=["forecast"])


class ForecastRequest(BaseModel):
    historical: list[dict[str, Any]] = Field(default_factory=list)
    horizon_days: int = 30


@router.post("/kpi")
async def forecast_kpi(
    body: ForecastRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return SalesForecastService().forecast(
        historical=body.historical, horizon_days=body.horizon_days
    )
