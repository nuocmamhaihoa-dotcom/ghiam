"""Digital Twin Salesperson API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.application.services.digital_twin import DigitalTwinService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/digital-twin", tags=["digital-twin"])


class TrainRequest(BaseModel):
    agent_id: str
    display_name: str
    calls: list[dict[str, Any]] = Field(default_factory=list)
    activate: bool = False


class ActRequest(BaseModel):
    customer_text: str
    step: int = 0


class RoleplayRequest(BaseModel):
    trainee_id: str
    scenario: str = "general"
    trainee_turns: list[str] = Field(default_factory=list)
    customer_turns: list[str] | None = None


class SimilarityRequest(BaseModel):
    trainee_text: str
    customer_text: str = ""


@router.post("/train")
async def train_twin(
    body: TrainRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **DigitalTwinService().train(
        agent_id=body.agent_id,
        display_name=body.display_name,
        calls=body.calls,
        activate=body.activate,
    )}


@router.get("/twins")
async def list_twins(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = DigitalTwinService().list_twins()
    return {"status": "ok", "twins": items, "count": len(items)}


@router.get("/twins/{twin_id}")
async def get_twin(
    twin_id: str,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    twin = DigitalTwinService().get_twin(twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="twin_not_found")
    return {"status": "ok", "twin": twin}


@router.post("/twins/{twin_id}/act")
async def act_as_twin(
    twin_id: str,
    body: ActRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **DigitalTwinService().act_as_twin(twin_id, body.customer_text, step=body.step)}


@router.post("/twins/{twin_id}/roleplay")
async def roleplay(
    twin_id: str,
    body: RoleplayRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **DigitalTwinService().roleplay(
        twin_id,
        trainee_id=body.trainee_id,
        scenario=body.scenario,
        trainee_turns=body.trainee_turns,
        customer_turns=body.customer_turns,
    )}


@router.post("/twins/{twin_id}/similarity")
async def score_similarity(
    twin_id: str,
    body: SimilarityRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **DigitalTwinService().score_similarity(
        twin_id, body.trainee_text, body.customer_text
    )}


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **DigitalTwinService().dashboard()}


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **DigitalTwinService().quality()}
