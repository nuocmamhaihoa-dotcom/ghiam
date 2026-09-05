"""Objection Simulator API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.application.services.objection_simulator import ObjectionSimulator
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/simulator", tags=["simulator"])


class GradeRequest(BaseModel):
    scenario_id: str
    agent_reply: str


@router.get("/scenarios")
async def scenarios(
    group: str | None = Query(default=None),
    user: CurrentUser = Depends(require_permissions("coaching:read")),
) -> dict[str, Any]:
    return {"data": ObjectionSimulator().list_scenarios(group)}


@router.post("/start")
async def start(
    group: str | None = None,
    seed: int | None = None,
    user: CurrentUser = Depends(require_permissions("coaching:read")),
) -> dict[str, Any]:
    return ObjectionSimulator().start(group=group, seed=seed)


@router.post("/grade")
async def grade(
    body: GradeRequest,
    user: CurrentUser = Depends(require_permissions("coaching:read")),
) -> dict[str, Any]:
    return ObjectionSimulator().grade(scenario_id=body.scenario_id, agent_reply=body.agent_reply)
