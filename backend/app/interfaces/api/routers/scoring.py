"""Scoring router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application.services.scoring import ScoringService
from app.core.deps import (
    CurrentUser,
    get_audit_repo,
    get_call_repo,
    get_coaching_repo,
    get_evidence_repo,
    get_revenue_repo,
    get_root_cause_repo,
    get_rule_repo,
    get_score_repo,
    require_permissions,
)
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.call import SqlAlchemyCallRepository
from app.infrastructure.repositories.coaching import SqlAlchemyCoachingRepository
from app.infrastructure.repositories.evidence import SqlAlchemyEvidenceRepository
from app.infrastructure.repositories.revenue import SqlAlchemyRevenueLeakRepository
from app.infrastructure.repositories.root_cause import SqlAlchemyRootCauseRepository
from app.infrastructure.repositories.rule import SqlAlchemyRuleRepository
from app.infrastructure.repositories.score import SqlAlchemyScoreRepository
from app.interfaces.api.schemas import ScoringResponseSchema

router = APIRouter(tags=["scoring"])


def get_scoring_service(
    calls: Annotated[SqlAlchemyCallRepository, Depends(get_call_repo)],
    rules: Annotated[SqlAlchemyRuleRepository, Depends(get_rule_repo)],
    evidence: Annotated[SqlAlchemyEvidenceRepository, Depends(get_evidence_repo)],
    scores: Annotated[SqlAlchemyScoreRepository, Depends(get_score_repo)],
    root_causes: Annotated[SqlAlchemyRootCauseRepository, Depends(get_root_cause_repo)],
    coaching: Annotated[SqlAlchemyCoachingRepository, Depends(get_coaching_repo)],
    revenue: Annotated[SqlAlchemyRevenueLeakRepository, Depends(get_revenue_repo)],
    audit: Annotated[SqlAlchemyAuditRepository, Depends(get_audit_repo)],
) -> ScoringService:
    return ScoringService(
        calls=calls,
        rules=rules,
        evidence=evidence,
        scores=scores,
        root_causes=root_causes,
        coaching=coaching,
        revenue=revenue,
        audit=audit,
    )


@router.post(
    "/calls/{call_id}/score",
    response_model=ScoringResponseSchema,
    status_code=status.HTTP_200_OK,
)
async def score_call(
    call_id: UUID,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("scorecard:read"))],
    scoring: Annotated[ScoringService, Depends(get_scoring_service)],
) -> ScoringResponseSchema:
    try:
        result = await scoring.score_call(
            call_id,
            actor_user_id=user.id,
            request_id=getattr(request.state, "request_id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = result.to_dict()
    payload["call_id"] = str(call_id)
    return ScoringResponseSchema(**payload)


@router.get("/calls/{call_id}/analysis", response_model=ScoringResponseSchema)
async def get_analysis(
    call_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permissions("scorecard:read"))],
    scoring: Annotated[ScoringService, Depends(get_scoring_service)],
) -> ScoringResponseSchema:
    payload = await scoring.get_analysis(call_id)
    return ScoringResponseSchema(**payload)


@router.post("/scoring/run", response_model=ScoringResponseSchema)
async def run_scoring(
    body: dict,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("scorecard:read"))],
    scoring: Annotated[ScoringService, Depends(get_scoring_service)],
) -> ScoringResponseSchema:
    call_id_raw = body.get("call_id")
    if not call_id_raw:
        raise HTTPException(status_code=422, detail="call_id is required")
    call_id = UUID(str(call_id_raw))
    try:
        result = await scoring.score_call(
            call_id,
            actor_user_id=user.id,
            request_id=getattr(request.state, "request_id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = result.to_dict()
    payload["call_id"] = str(call_id)
    return ScoringResponseSchema(**payload)
