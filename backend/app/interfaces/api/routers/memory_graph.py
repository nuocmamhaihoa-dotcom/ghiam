"""Memory Graph API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.memory_graph import MemoryGraphService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/memory-graph", tags=["memory-graph"])


class BuildGraphRequest(BaseModel):
    call_id: str
    violations: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: dict[str, Any] = Field(default_factory=dict)
    coaching: dict[str, Any] = Field(default_factory=dict)
    intents: list[dict[str, Any]] = Field(default_factory=list)
    objections: list[dict[str, Any]] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)


class SearchGraphRequest(BaseModel):
    graph: dict[str, Any]
    node_type: str | None = None
    query: str | None = None


@router.post("/build")
async def build_graph(
    body: BuildGraphRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().build_from_analysis(
        call_id=body.call_id,
        violations=body.violations,
        root_cause=body.root_cause,
        coaching=body.coaching,
        intents=body.intents,
        objections=body.objections,
        products=body.products,
    )


@router.post("/search")
async def search_graph(
    body: SearchGraphRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().search(body.graph, node_type=body.node_type, query=body.query)
