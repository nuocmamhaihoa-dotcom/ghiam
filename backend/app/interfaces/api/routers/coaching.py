"""Coaching router."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import CurrentUser, get_coaching_repo, require_permissions
from app.infrastructure.repositories.coaching import SqlAlchemyCoachingRepository

router = APIRouter(prefix="/coaching", tags=["coaching"])


@router.get("/plans/me")
async def my_plans(
    user: Annotated[CurrentUser, Depends(require_permissions("coaching:read"))],
    coaching: Annotated[SqlAlchemyCoachingRepository, Depends(get_coaching_repo)],
) -> dict[str, Any]:
    plans = await coaching.list_for_agent(user.id)
    return {"data": plans}


@router.get("/plans")
async def list_plans(
    user: Annotated[CurrentUser, Depends(require_permissions("coaching:read"))],
    coaching: Annotated[SqlAlchemyCoachingRepository, Depends(get_coaching_repo)],
    agent_id: UUID | None = None,
) -> dict[str, Any]:
    target = agent_id or user.id
    if agent_id and agent_id != user.id:
        user.require("coaching:manage")
    plans = await coaching.list_for_agent(target)
    return {"data": plans}


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permissions("coaching:read"))],
    coaching: Annotated[SqlAlchemyCoachingRepository, Depends(get_coaching_repo)],
) -> dict[str, Any]:
    plan = await coaching.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan
