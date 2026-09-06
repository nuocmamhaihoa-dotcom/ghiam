"""CLTV Engine API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.cltv import CLTVService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/cltv", tags=["cltv"])


class PredictRequest(BaseModel):
    lead_id: str | None = None
    call_history: list[dict[str, Any]] = Field(default_factory=list)
    conversation_dna: dict[str, Any] = Field(default_factory=dict)
    intent: str | dict[str, Any] | None = None
    emotion: str | dict[str, Any] | None = None
    buying_signal: float | dict[str, Any] | None = None
    crm: dict[str, Any] = Field(default_factory=dict)
    follow_up: dict[str, Any] = Field(default_factory=dict)


class PrioritizeRequest(BaseModel):
    leads: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/predict")
async def predict(
    body: PredictRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **CLTVService().predict(
            lead_id=body.lead_id,
            call_history=body.call_history,
            conversation_dna=body.conversation_dna,
            intent=body.intent,
            emotion=body.emotion,
            buying_signal=body.buying_signal,
            crm=body.crm,
            follow_up=body.follow_up,
        ),
    }


@router.post("/prioritize")
async def prioritize(
    body: PrioritizeRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **CLTVService().prioritize(body.leads)}


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **CLTVService().dashboard()}


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **CLTVService().quality()}
