"""Multi Product Intelligence API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.multi_product import MultiProductIntelligence
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/multi-product", tags=["multi-product"])


class MultiProductRequest(BaseModel):
    turns: list[dict[str, Any]] = Field(default_factory=list)
    current_sku: str | None = None


@router.post("/suggest")
async def suggest(
    body: MultiProductRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MultiProductIntelligence().suggest(body.turns, current_sku=body.current_sku)
