"""Auto SOP Generator API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.auto_sop import AutoSOPGenerator
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/auto-sop", tags=["auto-sop"])


class AutoSOPRequest(BaseModel):
    golden_calls: list[dict[str, Any]] = Field(default_factory=list)
    version: str | None = None


@router.post("/generate")
async def generate(
    body: AutoSOPRequest,
    user: CurrentUser = Depends(require_permissions("rules:write")),
) -> dict[str, Any]:
    return AutoSOPGenerator().generate(body.golden_calls, version=body.version)
