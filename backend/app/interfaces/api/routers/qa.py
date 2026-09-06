"""QA calibration APIs."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.calibration import CalibrationService
from app.core.deps import CurrentUser, DbSession, require_permissions

router = APIRouter(prefix="/qa", tags=["qa"])


class CalibrationSessionCreate(BaseModel):
    name: str
    notes: str | None = None


class CalibrationItemSubmit(BaseModel):
    session_id: UUID
    call_id: UUID
    human_score: float = Field(ge=0, le=100)
    notes: str | None = None
    stage_scores: dict[str, float] = Field(default_factory=dict)


@router.get("/queue")
async def qa_queue(
    user: Annotated[CurrentUser, Depends(require_permissions("qa:read"))],
    session: DbSession,
) -> dict[str, Any]:
    items = await CalibrationService(session).list_queue()
    return {"data": items}


@router.post("/calibration/sessions", status_code=status.HTTP_201_CREATED)
async def create_calibration_session(
    body: CalibrationSessionCreate,
    user: Annotated[CurrentUser, Depends(require_permissions("qa:calibrate"))],
    session: DbSession,
) -> dict[str, Any]:
    return await CalibrationService(session).create_session(
        name=body.name,
        created_by=user.id,
        notes=body.notes,
    )


@router.post("/calibration/items", status_code=status.HTTP_201_CREATED)
async def submit_calibration_item(
    body: CalibrationItemSubmit,
    user: Annotated[CurrentUser, Depends(require_permissions("qa:calibrate"))],
    session: DbSession,
) -> dict[str, Any]:
    payload = body.model_dump()
    payload["reviewer_user_id"] = str(user.id)
    try:
        return await CalibrationService(session).submit_item(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
