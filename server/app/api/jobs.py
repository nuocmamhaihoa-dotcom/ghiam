from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.deps import ContainerDep, SessionDep
from app.jobs import service
from app.jobs.schemas import (
    CommentListOut,
    CreateJobIn,
    JobListOut,
    JobOut,
    PostListOut,
    PreviewIn,
    PreviewOut,
)
from app.security import require_admin

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_admin)])

ExportFormat = Literal["json", "ndjson", "csv"]


@router.post("/preview")
async def preview(data: PreviewIn) -> PreviewOut:
    return service.preview_permalinks(data.text)


@router.post("")
async def create_job(data: CreateJobIn, session: SessionDep) -> JobOut:
    return await service.create_job(session, data)


@router.get("")
async def list_jobs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListOut:
    return await service.list_jobs(session, limit=limit, offset=offset)


@router.get("/{job_id}")
async def get_job(job_id: int, session: SessionDep) -> JobOut:
    return await service.get_job(session, job_id)


@router.get("/{job_id}/posts")
async def list_posts(
    job_id: int,
    session: SessionDep,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PostListOut:
    return await service.list_posts(session, job_id, status=status, limit=limit, offset=offset)


@router.post("/{job_id}/pause")
async def pause_job(job_id: int, session: SessionDep) -> JobOut:
    return await service.pause_job(session, job_id)


@router.post("/{job_id}/resume")
async def resume_job(job_id: int, session: SessionDep) -> JobOut:
    return await service.resume_job(session, job_id)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int, session: SessionDep) -> JobOut:
    return await service.cancel_job(session, job_id)


@router.post("/{job_id}/retry-failed")
async def retry_failed(job_id: int, session: SessionDep) -> JobOut:
    return await service.retry_failed(session, job_id)


@router.get("/{job_id}/comments")
async def list_comments(
    job_id: int,
    session: SessionDep,
    post_id: int | None = None,
    q: str | None = None,
    min_likes: Annotated[int | None, Query(ge=0)] = None,
    after_id: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CommentListOut:
    return await service.list_comments(
        session, job_id, post_id=post_id, query=q, min_likes=min_likes, after_id=after_id, limit=limit
    )


@router.get("/{job_id}/export")
async def export_job(
    job_id: int,
    container: ContainerDep,
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
    post_id: int | None = None,
) -> StreamingResponse:
    media = {"json": "application/json", "ndjson": "application/x-ndjson", "csv": "text/csv; charset=utf-8"}
    filename = f"comments-job-{job_id}.{fmt if fmt != 'ndjson' else 'ndjson'}"
    return StreamingResponse(
        service.iter_export(container.db, job_id, fmt=fmt, post_id=post_id),
        media_type=media[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
