"""Datasets / golden calls / dataset builder router."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.dataset_builder import DatasetBuilderService
from app.application.services.golden_benchmark import GoldenBenchmarkService
from app.core.deps import CurrentUser, DbSession, get_golden_repo, require_permissions
from app.domain.entities import GoldenCallEntity
from app.infrastructure.repositories.golden import SqlAlchemyGoldenCallRepository
from app.interfaces.api.schemas import GoldenCallCreateRequest

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetBuildRequest(BaseModel):
    name: str
    dataset_type: str = "scoring"
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=100, ge=1, le=1000)


class GoldenCompareRequest(BaseModel):
    golden_id: UUID
    call_id: UUID | None = None


@router.get("/golden-calls")
async def list_golden(
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:read"))],
    golden: Annotated[SqlAlchemyGoldenCallRepository, Depends(get_golden_repo)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    rows = await golden.list(limit=limit, offset=offset)
    return {
        "data": [
            {
                "id": str(r.id),
                "name": r.name,
                "call_id": str(r.call_id) if r.call_id else None,
                "labels": r.labels,
                "expected_score": r.expected_score,
                "notes": r.notes,
            }
            for r in rows
        ]
    }


@router.post("/golden-calls", status_code=status.HTTP_201_CREATED)
async def create_golden(
    body: GoldenCallCreateRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:write"))],
    golden: Annotated[SqlAlchemyGoldenCallRepository, Depends(get_golden_repo)],
) -> dict[str, Any]:
    entity = GoldenCallEntity(
        id=uuid4(),
        call_id=body.call_id,
        name=body.name,
        labels=body.labels,
        expected_score=body.expected_score,
        notes=body.notes,
        transcript_text=body.transcript_text,
    )
    created = await golden.create(entity)
    return {
        "id": str(created.id),
        "name": created.name,
        "expected_score": created.expected_score,
    }


@router.post("/golden-calls/compare")
async def compare_golden_call(
    body: GoldenCompareRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:read"))],
    session: DbSession,
) -> dict[str, Any]:
    try:
        return await GoldenBenchmarkService(session).compare(
            golden_id=body.golden_id,
            call_id=body.call_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/golden-calls/{golden_id}")
async def get_golden(
    golden_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:read"))],
    golden: Annotated[SqlAlchemyGoldenCallRepository, Depends(get_golden_repo)],
) -> dict[str, Any]:
    row = await golden.get(golden_id)
    if not row:
        raise HTTPException(status_code=404, detail="Golden call not found")
    return {
        "id": str(row.id),
        "name": row.name,
        "call_id": str(row.call_id) if row.call_id else None,
        "labels": row.labels,
        "expected_score": row.expected_score,
        "notes": row.notes,
        "transcript_text": row.transcript_text,
    }


@router.post("/exports")
async def build_dataset_export(
    body: DatasetBuildRequest,
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:write"))],
    session: DbSession,
) -> dict[str, Any]:
    return await DatasetBuilderService(session).build(
        name=body.name,
        dataset_type=body.dataset_type,
        created_by=user.id,
        filters=body.filters,
        limit=body.limit,
    )


@router.get("/exports")
async def list_dataset_exports(
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:read"))],
    session: DbSession,
    limit: int = 50,
) -> dict[str, Any]:
    rows = await DatasetBuilderService(session).list_exports(limit=limit)
    return {"data": rows}
