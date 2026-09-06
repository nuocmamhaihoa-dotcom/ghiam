"""Appeal mode APIs."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.appeals import AppealService
from app.core.deps import CurrentUser, DbSession, require_permissions

router = APIRouter(prefix="/appeals", tags=["appeals"])


class AppealCreateRequest(BaseModel):
    call_id: UUID
    rule_code: str
    rule_title: str | None = None
    current_verdict: str = "fail"
    proposed_verdict: str = "pass"
    reason_code: str = "other"
    reason_text: str = ""
    evidence_quote: str | None = None


class AppealResolveRequest(BaseModel):
    status: str = Field(description="open|under_review|overturned|upheld")
    resolution_note: str | None = None


@router.get("")
async def list_appeals(
    user: Annotated[CurrentUser, Depends(require_permissions("appeals:read"))],
    session: DbSession,
    status_filter: str | None = None,
) -> dict[str, Any]:
    items = await AppealService(session).list_appeals(status=status_filter)
    return {"data": items}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_appeal(
    body: AppealCreateRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("appeals:write"))],
    session: DbSession,
) -> dict[str, Any]:
    try:
        return await AppealService(session).create_appeal(body.model_dump(), actor_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{appeal_id}/resolve")
async def resolve_appeal(
    appeal_id: UUID,
    body: AppealResolveRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("appeals:write"))],
    session: DbSession,
) -> dict[str, Any]:
    try:
        return await AppealService(session).resolve_appeal(
            appeal_id,
            status=body.status,
            resolution_note=body.resolution_note,
            reviewer_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
