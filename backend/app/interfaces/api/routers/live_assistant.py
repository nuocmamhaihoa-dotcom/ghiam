"""Live Call Assistant API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.live_assistant import LiveCallAssistant
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/live-assistant", tags=["live-assistant"])


class LiveSuggestRequest(BaseModel):
    turns: list[dict[str, Any]] = Field(default_factory=list)
    now_ts: float | None = None


@router.post("/suggest")
async def suggest(
    body: LiveSuggestRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return LiveCallAssistant().suggest(body.turns, now_ts=body.now_ts)
