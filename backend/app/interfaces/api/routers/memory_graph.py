"""Memory Graph + RAG API."""

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
    graph: dict[str, Any] | None = None
    node_type: str | None = None
    query: str | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str
    node_type: str | None = None
    limit: int = 20


class SimilarRequest(BaseModel):
    text: str
    limit: int = 10


class UpdateKnowledgeRequest(BaseModel):
    node_id: str
    node_type: str
    label: str
    content: str
    source: str
    confidence: float = 0.9
    metadata: dict[str, Any] = Field(default_factory=dict)


class VersionHistoryRequest(BaseModel):
    entity_id: str
    limit: int = 50


class ExploreRequest(BaseModel):
    limit: int = 200


class AskRequest(BaseModel):
    question: str
    limit: int = 6


class SyncRequest(BaseModel):
    kind: str | None = None
    reset: bool = False


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


@router.post("/knowledge/search")
async def search_knowledge(
    body: KnowledgeSearchRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().search_knowledge(body.query, node_type=body.node_type, limit=body.limit)


@router.post("/similar/objection")
async def similar_objection(
    body: SimilarRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().similar_objection(body.text, limit=body.limit)


@router.post("/similar/call")
async def similar_call(
    body: SimilarRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().similar_call(body.text, limit=body.limit)


@router.post("/knowledge/update")
async def update_knowledge(
    body: UpdateKnowledgeRequest,
    user: CurrentUser = Depends(require_permissions("rulebook:write")),
) -> dict[str, Any]:
    return MemoryGraphService().update_knowledge(
        node_id=body.node_id,
        node_type=body.node_type,
        label=body.label,
        content=body.content,
        source=body.source,
        confidence=body.confidence,
        metadata=body.metadata,
    )


@router.post("/versions")
async def version_history(
    body: VersionHistoryRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().version_history(body.entity_id, limit=body.limit)


@router.post("/explore")
async def explore_graph(
    body: ExploreRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().explore(limit=body.limit)


@router.post("/ask")
async def ask_rag(
    body: AskRequest,
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().ask(body.question, limit=body.limit)


@router.post("/sync")
async def sync_knowledge(
    body: SyncRequest,
    user: CurrentUser = Depends(require_permissions("rulebook:write")),
) -> dict[str, Any]:
    return MemoryGraphService().sync(kind=body.kind, reset=body.reset)


@router.get("/quality")
async def quality_report(
    user: CurrentUser = Depends(require_permissions("calls:read")),
) -> dict[str, Any]:
    return MemoryGraphService().quality_report()
