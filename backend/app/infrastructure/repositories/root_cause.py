"""Root cause repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import RootCauseModel


class SqlAlchemyRootCauseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self, call_id: UUID, score_id: UUID | None, payload: dict[str, Any]
    ) -> UUID:
        row_id = uuid4()
        self._session.add(
            RootCauseModel(
                id=row_id,
                call_id=call_id,
                score_id=score_id,
                verdict=str(
                    payload.get("verdict")
                    or payload.get("status")
                    or "Insufficient Evidence"
                ),
                primary_cause_code=payload.get("primary_cause_code")
                or payload.get("primary_code"),
                causes=list(payload.get("causes") or payload.get("children") or []),
                explanation=str(
                    payload.get("explanation") or payload.get("reason") or ""
                ),
                payload=payload,
            )
        )
        await self._session.flush()
        return row_id

    async def get_latest(self, call_id: UUID) -> dict[str, Any] | None:
        stmt = (
            select(RootCauseModel)
            .where(RootCauseModel.call_id == call_id)
            .order_by(RootCauseModel.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return dict(row.payload) if row else None
