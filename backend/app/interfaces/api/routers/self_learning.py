"""Self-Learning Lab API — research proposals, QA approval, never auto-apply to production."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.self_learning import SelfLearningService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/self-learning", tags=["self-learning"])


class IngestCallRequest(BaseModel):
    call: dict[str, Any]


class ReviewerRequest(BaseModel):
    reviewer: str = "qa"


class RejectRequest(BaseModel):
    reviewer: str = "qa"
    reason: str = ""


class MergeRequest(BaseModel):
    into_proposal_id: str
    reviewer: str = "qa"


class EditRequest(BaseModel):
    reviewer: str = "qa"
    patch: dict[str, Any] = Field(default_factory=dict)


class CallsBatchRequest(BaseModel):
    calls: list[dict[str, Any]] = Field(default_factory=list)
    week_id: str = "current"


class EvaluateRequest(BaseModel):
    metrics_update: dict[str, float] = Field(default_factory=dict)


@router.post("/ingest")
async def ingest_call(
    body: IngestCallRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    result = SelfLearningService().ingest_call(body.call)
    return {"status": "ok", **result}


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().dashboard()}


@router.get("/qa-queue")
async def qa_queue(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = SelfLearningService().qa_queue()
    return {"status": "ok", "items": items, "count": len(items)}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    body: ReviewerRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().approve(proposal_id, body.reviewer)}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    body: RejectRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().reject(proposal_id, body.reviewer, body.reason)}


@router.post("/proposals/{proposal_id}/merge")
async def merge_proposal(
    proposal_id: str,
    body: MergeRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **SelfLearningService().merge(proposal_id, body.into_proposal_id, body.reviewer),
    }


@router.post("/proposals/{proposal_id}/edit")
async def edit_proposal(
    proposal_id: str,
    body: EditRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().edit(proposal_id, body.reviewer, body.patch)}


@router.post("/proposals/{proposal_id}/promote")
async def promote_proposal(
    proposal_id: str,
    body: ReviewerRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().promote(proposal_id, body.reviewer)}


@router.post("/versions/{version_id}/rollback")
async def rollback_version(
    version_id: str,
    body: ReviewerRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().rollback(version_id, body.reviewer)}


@router.post("/discover/golden")
async def discover_golden(
    body: CallsBatchRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = SelfLearningService().discover_golden(body.calls or None)
    return {"status": "ok", "items": items, "count": len(items)}


@router.post("/discover/failures")
async def discover_failures(
    body: CallsBatchRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = SelfLearningService().discover_failures(body.calls or None)
    return {"status": "ok", "items": items, "count": len(items)}


@router.post("/discover/revenue-leaks")
async def discover_revenue_leaks(
    body: CallsBatchRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = SelfLearningService().discover_revenue_leaks(body.calls or None, week_id=body.week_id)
    return {"status": "ok", "items": items, "count": len(items)}


@router.post("/coaching/generate")
async def generate_coaching(
    body: ReviewerRequest,
    proposal_id: str | None = None,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = SelfLearningService().generate_coaching(proposal_id)
    return {"status": "ok", "items": items, "count": len(items)}


@router.post("/self-evaluate")
async def self_evaluate(
    body: EvaluateRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().self_evaluate(body.metrics_update or None)}


@router.get("/dataset-growth")
async def dataset_growth(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().dataset_growth()}


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **SelfLearningService().quality()}
