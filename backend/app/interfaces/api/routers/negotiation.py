"""Negotiation Strategy Engine API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.negotiation import NegotiationService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/negotiation", tags=["negotiation"])


class AnalyzeRequest(BaseModel):
    customer_utterance: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class PredictRequest(BaseModel):
    customer_utterance: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class CompareRequest(BaseModel):
    customer_utterance: str
    context: dict[str, Any] = Field(default_factory=dict)


@router.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **NegotiationService().analyze(
            body.customer_utterance,
            history=body.history,
            context=body.context,
        ),
    }


@router.post("/predict")
async def predict(
    body: PredictRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **NegotiationService().predict(
            body.customer_utterance,
            history=body.history,
            context=body.context,
        ),
    }


@router.post("/compare")
async def compare(
    body: CompareRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **NegotiationService().compare(
            body.customer_utterance,
            context=body.context,
        ),
    }


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **NegotiationService().dashboard()}


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **NegotiationService().quality()}
