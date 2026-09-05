"""Autonomous Sales AI API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.autonomous import AutonomousService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/autonomous", tags=["autonomous"])


class ContextRequest(BaseModel):
    lead_id: str | None = None
    call_id: str | None = None
    buy_signal: float = 0.0
    sentiment: str = "neutral"
    objection: str | None = None
    intent: str | None = None
    qa_score: float = 0.0
    churn_risk: float = 0.0
    cltv_score: float = 0.0
    missed_followups: int = 0
    auto_execute: bool = True


class AutomationRequest(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ProposeChangeRequest(BaseModel):
    change_type: str
    title: str
    proposal: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    requested_by: str = "autonomous_ai"


class DecideChangeRequest(BaseModel):
    approval_id: str
    approve: bool
    decided_by: str


@router.post("/recommend")
async def recommend(
    body: ContextRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AutonomousService().recommend(body.model_dump())}


@router.post("/nba")
async def run_nba(
    body: ContextRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    data = body.model_dump()
    auto = bool(data.pop("auto_execute", True))
    return {"status": "ok", **AutonomousService().run_nba_pipeline(data, auto_execute=auto)}


@router.post("/automations/trigger")
async def trigger_automation(
    body: AutomationRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AutonomousService().trigger_automation(body.kind, body.payload)}


@router.post("/approvals/propose")
async def propose_change(
    body: ProposeChangeRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **AutonomousService().propose_rule_change(
            change_type=body.change_type,
            title=body.title,
            proposal=body.proposal,
            evidence=body.evidence,
            requested_by=body.requested_by,
        ),
    }


@router.post("/approvals/decide")
async def decide_change(
    body: DecideChangeRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **AutonomousService().decide_rule_change(
            body.approval_id,
            approve=body.approve,
            decided_by=body.decided_by,
        ),
    }


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AutonomousService().dashboard()}


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AutonomousService().quality()}
