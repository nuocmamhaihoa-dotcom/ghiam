"""Fraud & Compliance API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.fraud_compliance import FraudComplianceService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/fraud", tags=["fraud"])


class FraudScanRequest(BaseModel):
    turns: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/scan")
async def scan(
    body: FraudScanRequest,
    user: CurrentUser = Depends(require_permissions("qa:read")),
) -> dict[str, Any]:
    return FraudComplianceService().scan(body.turns)
