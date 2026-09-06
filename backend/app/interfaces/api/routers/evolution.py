"""Evolution Engine API — shadow, experiments, scorecard, drift, proposals, rollback.

Hard rule: proposals never auto-merge to production. Approve ≠ promote.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.evolution import EvolutionService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/evolution", tags=["evolution"])


class ShadowCompareRequest(BaseModel):
    call_id: str
    production: dict[str, Any]
    candidate: dict[str, Any]


class ExperimentCreateRequest(BaseModel):
    name: str
    arm_a: dict[str, Any]
    arm_b: dict[str, Any]


class ExperimentRecordRequest(BaseModel):
    arm: str
    metrics: dict[str, float]


class ScorecardUpdateRequest(BaseModel):
    model_name: str
    metrics: dict[str, float]
    sample_count: int = 0


class DriftRequest(BaseModel):
    signals: dict[str, float] = Field(default_factory=dict)


class FailureCaptureRequest(BaseModel):
    call_id: str
    audio_ref: str = ""
    transcript: str = ""
    rule: dict[str, Any] = Field(default_factory=dict)
    ai_output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PerformanceRequest(BaseModel):
    snapshot: dict[str, float] = Field(default_factory=dict)


class ObservabilityRequest(BaseModel):
    metrics: dict[str, float] = Field(default_factory=dict)


class WeeklyCycleRequest(BaseModel):
    signals: dict[str, Any] = Field(default_factory=dict)


class ReviewerRequest(BaseModel):
    reviewer: str = "qa"


class RejectRequest(BaseModel):
    reviewer: str = "qa"
    reason: str = ""


class PromoteRequest(BaseModel):
    actor: str = "qa"


class SnapshotRequest(BaseModel):
    artifact_id: str
    artifact_type: str = "rule"
    payload: dict[str, Any]
    version: str | None = None


class RollbackRequest(BaseModel):
    artifact_id: str
    to_version: str | None = None
    to_version_id: str | None = None
    actor: str = "qa"


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().dashboard()}


@router.get("/quality-gate")
async def quality_gate(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().quality_gate()}


@router.post("/shadow/compare")
async def shadow_compare(
    body: ShadowCompareRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    report = EvolutionService().shadow_compare(body.call_id, body.production, body.candidate)
    return {"status": "ok", "report": report, "user_impact": False}


@router.post("/experiments")
async def create_experiment(
    body: ExperimentCreateRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    exp = EvolutionService().create_experiment(body.name, body.arm_a, body.arm_b)
    return {"status": "ok", "experiment": exp, "auto_promoted": False}


@router.post("/experiments/{experiment_id}/record")
async def record_experiment(
    experiment_id: str,
    body: ExperimentRecordRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    row = EvolutionService().record_experiment(experiment_id, body.arm, body.metrics)
    return {"status": "ok", "experiment": row}


@router.post("/experiments/{experiment_id}/conclude")
async def conclude_experiment(
    experiment_id: str,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    result = EvolutionService().conclude_experiment(experiment_id)
    return {"status": "ok", "result": result, "auto_promoted": False}


@router.get("/experiments")
async def list_experiments(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = EvolutionService().list_experiments()
    return {"status": "ok", "items": items, "count": len(items)}


@router.get("/scorecard")
async def scorecard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().scorecard()}


@router.post("/scorecard")
async def update_scorecard(
    body: ScorecardUpdateRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    row = EvolutionService().update_scorecard(body.model_name, body.metrics, body.sample_count)
    return {"status": "ok", "model": row}


@router.post("/drift/detect")
async def detect_drift(
    body: DriftRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().detect_drift(body.signals)}


@router.get("/drift/report")
async def drift_report(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().drift_report()}


@router.post("/failures/capture")
async def capture_failure(
    body: FailureCaptureRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    row = EvolutionService().capture_failure(body.model_dump())
    return {"status": "ok", "failure": row}


@router.post("/failures/{failure_id}/replay")
async def replay_failure(
    failure_id: str,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().replay_failure(failure_id)}


@router.post("/performance/analyze")
async def analyze_performance(
    body: PerformanceRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().analyze_performance(body.snapshot)}


@router.post("/observability/record")
async def record_observability(
    body: ObservabilityRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().record_observability(body.metrics)}


@router.get("/observability")
async def observability_snapshot(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().observability_snapshot()}


@router.post("/weekly-cycle")
async def weekly_cycle(
    body: WeeklyCycleRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    result = EvolutionService().weekly_cycle(body.signals)
    return {"status": "ok", **result, "auto_merged": False}


@router.get("/proposals/pending")
async def pending_proposals(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    items = EvolutionService().pending_proposals()
    return {"status": "ok", "items": items, "count": len(items)}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    body: ReviewerRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    row = EvolutionService().approve_proposal(proposal_id, body.reviewer)
    return {"status": "ok", "proposal": row, "promoted_to_production": False}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    body: RejectRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    row = EvolutionService().reject_proposal(proposal_id, body.reviewer, body.reason)
    return {"status": "ok", "proposal": row}


@router.post("/proposals/{proposal_id}/promote")
async def promote_proposal(
    proposal_id: str,
    body: PromoteRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    """Explicit second step after approve — never automatic."""
    row = EvolutionService().promote_proposal(proposal_id, body.actor)
    return {"status": "ok", "proposal": row, "promoted_to_production": True}


@router.post("/rollback/snapshot")
async def snapshot_artifact(
    body: SnapshotRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    snap = EvolutionService().snapshot_artifact(
        body.artifact_id, body.artifact_type, body.payload, version=body.version
    )
    return {"status": "ok", "snapshot": snap}


@router.post("/rollback")
async def rollback_artifact(
    body: RollbackRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    result = EvolutionService().rollback_artifact(
        body.artifact_id,
        to_version=body.to_version,
        to_version_id=body.to_version_id,
        actor=body.actor,
    )
    return {"status": "ok", **result}


@router.get("/rollback/readiness")
async def rollback_readiness(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **EvolutionService().rollback_readiness()}


@router.post("/reports/write")
async def write_reports(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    paths = EvolutionService().write_reports()
    return {"status": "ok", "files": paths}
