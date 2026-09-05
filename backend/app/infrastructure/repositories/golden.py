"""Golden call repository."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import GoldenCallEntity
from app.infrastructure.db.models import GoldenCallModel


class SqlAlchemyGoldenCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, row: GoldenCallModel) -> GoldenCallEntity:
        return GoldenCallEntity(
            id=row.id,
            call_id=row.call_id,
            name=row.name,
            labels=dict(row.labels or {}),
            expected_score=row.expected_score,
            notes=row.notes,
            transcript_text=row.transcript_text,
        )

    async def list(self, limit: int = 50, offset: int = 0) -> list[GoldenCallEntity]:
        stmt = (
            select(GoldenCallModel)
            .order_by(GoldenCallModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def create(self, entity: GoldenCallEntity) -> GoldenCallEntity:
        row = GoldenCallModel(
            id=entity.id or uuid4(),
            call_id=entity.call_id,
            name=entity.name,
            labels=entity.labels,
            expected_score=entity.expected_score,
            notes=entity.notes,
            transcript_text=entity.transcript_text,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_entity(row)

    async def get(self, golden_id: UUID) -> GoldenCallEntity | None:
        row = await self._session.get(GoldenCallModel, golden_id)
        return self._to_entity(row) if row else None
