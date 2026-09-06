"""Evidence repository."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import EvidenceEntity
from app.infrastructure.db.models import EvidenceModel


class SqlAlchemyEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, row: EvidenceModel) -> EvidenceEntity:
        return EvidenceEntity(
            id=row.id,
            call_id=row.call_id,
            quote=row.quote,
            speaker=row.speaker,
            stage_key=row.stage_key,
            slot=row.slot,
            confidence=row.confidence,
            audio_ts_start=row.audio_ts_start,
            audio_ts_end=row.audio_ts_end,
            turn_index=row.turn_index,
            labels=list(row.labels or []),
            metadata=dict(row.metadata_json or {}),
        )

    async def list_for_call(self, call_id: UUID) -> list[EvidenceEntity]:
        stmt = select(EvidenceModel).where(EvidenceModel.call_id == call_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def bulk_create(self, items: list[EvidenceEntity]) -> list[EvidenceEntity]:
        created: list[EvidenceEntity] = []
        for item in items:
            row = EvidenceModel(
                id=item.id or uuid4(),
                call_id=item.call_id,
                quote=item.quote,
                speaker=item.speaker,
                stage_key=item.stage_key,
                slot=item.slot,
                confidence=item.confidence,
                audio_ts_start=item.audio_ts_start,
                audio_ts_end=item.audio_ts_end,
                turn_index=item.turn_index,
                labels=item.labels,
                metadata_json=item.metadata,
            )
            self._session.add(row)
            created.append(self._to_entity(row))
        await self._session.flush()
        return created
