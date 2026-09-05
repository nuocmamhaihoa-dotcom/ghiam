"""Datasets / golden calls router."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import CurrentUser, get_golden_repo, require_permissions
from app.domain.entities import GoldenCallEntity
from app.infrastructure.repositories.golden import SqlAlchemyGoldenCallRepository
from app.interfaces.api.schemas import GoldenCallCreateRequest

router = APIRouter(prefix="/datasets", tags=["datasets"])


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


@router.get("/golden-calls/{golden_id}")
async def get_golden(
    golden_id: str,
    user: Annotated[CurrentUser, Depends(require_permissions("datasets:read"))],
    golden: Annotated[SqlAlchemyGoldenCallRepository, Depends(get_golden_repo)],
) -> dict[str, Any]:
    from uuid import UUID

    row = await golden.get(UUID(golden_id))
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
