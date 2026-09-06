
"""Audio Intelligence Engine API — Upload/Repair/Transcript/Diarization/Quality/Emotion/Evidence/Export."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.services.audio_intelligence import AudioIntelligenceService
from app.core.deps import CurrentUser, require_permissions

router = APIRouter(prefix="/audio-intelligence", tags=["audio-intelligence"])


class UploadFileItem(BaseModel):
    filename: str
    size_bytes: int = 0
    hints: dict[str, Any] = Field(default_factory=dict)


class UploadRequest(BaseModel):
    files: list[UploadFileItem] = Field(default_factory=list)


class ProcessRequest(BaseModel):
    file_meta: dict[str, Any] = Field(default_factory=dict)
    transcript_turns: list[dict[str, Any]] = Field(default_factory=list)
    quality_hints: dict[str, Any] = Field(default_factory=dict)
    raw_text: str | None = None
    force_repair: bool = False
    duration_sec: float | None = None


class RepairRequest(BaseModel):
    file_id: str
    quality: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class RollbackRequest(BaseModel):
    repair: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    job_id: str
    format: str = "json"


class BatchRequest(BaseModel):
    items: list[ProcessRequest] = Field(default_factory=list)
    resume_from: int = 0


class SearchRequest(BaseModel):
    keyword: str | None = None
    emotion: str | None = None
    objection: str | None = None
    agent: str | None = None
    customer: str | None = None


class LiveRequest(BaseModel):
    text: str = ""
    session_id: str | None = None


@router.post("/upload")
async def upload(
    body: UploadRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().upload([f.model_dump() for f in body.files])}


@router.post("/process")
async def process(
    body: ProcessRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().process(body.model_dump())}


@router.post("/repair")
async def repair(
    body: RepairRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **AudioIntelligenceService().repair(file_id=body.file_id, quality=body.quality, force=body.force),
    }


@router.post("/repair/rollback")
async def rollback_repair(
    body: RollbackRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().rollback_repair(body.repair)}


@router.post("/transcript")
async def transcript(
    body: ProcessRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().transcript(body.model_dump())}


@router.post("/diarization")
async def diarization(
    body: ProcessRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().diarization(body.model_dump())}


@router.post("/quality")
async def quality(
    body: ProcessRequest | None = None,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    payload = body.model_dump() if body else None
    return {"status": "ok", **AudioIntelligenceService().quality(payload)}


@router.get("/quality/snapshot")
async def quality_snapshot(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().quality()}


@router.post("/emotion")
async def emotion(
    body: ProcessRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().emotion(body.model_dump())}


@router.post("/evidence")
async def evidence(
    body: ProcessRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().evidence(body.model_dump())}


@router.post("/export")
async def export(
    body: ExportRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().export(body.job_id, fmt=body.format)}


@router.post("/batch")
async def batch(
    body: BatchRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {
        "status": "ok",
        **AudioIntelligenceService().batch([i.model_dump() for i in body.items], resume_from=body.resume_from),
    }


@router.post("/search")
async def search(
    body: SearchRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().search(body.model_dump())}


@router.post("/live")
async def live(
    body: LiveRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().live(body.model_dump())}


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **AudioIntelligenceService().dashboard()}
