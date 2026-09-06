"""Call repository SQLAlchemy implementation."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import CallEntity, TranscriptEntity
from app.domain.enums import CallDirection, CallStatus
from app.infrastructure.db.models import CallModel, TranscriptModel


class SqlAlchemyCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, row: CallModel) -> CallEntity:
        return CallEntity(
            id=row.id,
            external_call_id=row.external_call_id,
            status=CallStatus(row.status),
            direction=CallDirection(row.direction),
            agent_user_id=row.agent_user_id,
            tenant_id=row.tenant_id,
            campaign_code=row.campaign_code,
            started_at=row.started_at,
            ended_at=row.ended_at,
            duration_sec=float(row.duration_sec) if row.duration_sec is not None else None,
            customer_phone=row.customer_phone,
            crm_outcome=row.crm_outcome,
            crm_order_value=float(row.crm_order_value) if row.crm_order_value is not None else None,
            currency=row.currency,
            audio_s3_key=row.audio_s3_key,
            metadata=dict(row.metadata_json or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, call: CallEntity) -> CallEntity:
        row = CallModel(
            id=call.id,
            tenant_id=call.tenant_id,
            external_call_id=call.external_call_id,
            campaign_code=call.campaign_code,
            agent_user_id=call.agent_user_id,
            direction=call.direction.value,
            started_at=call.started_at,
            ended_at=call.ended_at,
            duration_sec=call.duration_sec,
            customer_phone=call.customer_phone,
            crm_outcome=call.crm_outcome,
            crm_order_value=call.crm_order_value,
            currency=call.currency,
            status=call.status.value,
            audio_s3_key=call.audio_s3_key,
            metadata_json=call.metadata,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_entity(row)

    async def get(self, call_id: UUID) -> CallEntity | None:
        row = await self._session.get(CallModel, call_id)
        return self._to_entity(row) if row else None

    async def get_by_external(
        self, tenant_id: UUID | None, external_call_id: str
    ) -> CallEntity | None:
        stmt = select(CallModel).where(CallModel.external_call_id == external_call_id)
        if tenant_id is not None:
            stmt = stmt.where(CallModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return self._to_entity(row) if row else None

    async def list(
        self,
        *,
        agent_user_id: UUID | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CallEntity]:
        stmt = select(CallModel).order_by(CallModel.created_at.desc())
        if agent_user_id:
            stmt = stmt.where(CallModel.agent_user_id == agent_user_id)
        if status:
            stmt = stmt.where(CallModel.status == status)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def update_status(self, call_id: UUID, status: str) -> None:
        row = await self._session.get(CallModel, call_id)
        if row:
            row.status = status
            await self._session.flush()

    async def save_transcript(self, transcript: TranscriptEntity) -> TranscriptEntity:
        row = TranscriptModel(
            id=transcript.id or uuid4(),
            call_id=transcript.call_id,
            language=transcript.language,
            full_text=transcript.full_text,
            turns=transcript.turns,
            avg_confidence=transcript.avg_confidence,
            turn_count=transcript.turn_count,
        )
        self._session.add(row)
        await self._session.flush()
        return TranscriptEntity(
            id=row.id,
            call_id=row.call_id,
            language=row.language,
            full_text=row.full_text,
            turns=list(row.turns or []),
            avg_confidence=row.avg_confidence,
            turn_count=row.turn_count,
            created_at=row.created_at,
        )

    async def get_transcript(self, call_id: UUID) -> TranscriptEntity | None:
        stmt = (
            select(TranscriptModel)
            .where(TranscriptModel.call_id == call_id)
            .order_by(TranscriptModel.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if not row:
            return None
        return TranscriptEntity(
            id=row.id,
            call_id=row.call_id,
            language=row.language,
            full_text=row.full_text,
            turns=list(row.turns or []),
            avg_confidence=row.avg_confidence,
            turn_count=row.turn_count,
            created_at=row.created_at,
        )

    async def set_audio_key(self, call_id: UUID, s3_key: str) -> None:
        row = await self._session.get(CallModel, call_id)
        if row:
            row.audio_s3_key = s3_key
            await self._session.flush()
