"""Customer Personality API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.personality_engine import PersonalityEngine
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/personality", tags=["personality"])


class PersonalityRequest(BaseModel):
    turns: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/analyze")
async def analyze(
    body: PersonalityRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return PersonalityEngine().analyze(body.turns)
