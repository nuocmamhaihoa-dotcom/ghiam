"""Pragmatics API — Vietnamese soft-language intent resolution."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.pragmatics import PragmaticsEngine
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/pragmatics", tags=["pragmatics"])


class TranscriptTurn(BaseModel):
    speaker: str = Field(..., examples=["customer", "agent"])
    text: str
    turn_index: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class PragmaticsAnalyzeRequest(BaseModel):
    turns: list[TranscriptTurn]
    dialect_hint: str | None = Field(
        default=None, description="Optional: north | central | south"
    )


@router.post("/analyze")
async def analyze_pragmatics(
    body: PragmaticsAnalyzeRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("calls:read"))],
) -> dict[str, Any]:
    engine = PragmaticsEngine()
    result = engine.analyze_transcript(
        [turn.model_dump() for turn in body.turns],
        dialect_hint=body.dialect_hint,
    )
    return engine.to_dict(result)
