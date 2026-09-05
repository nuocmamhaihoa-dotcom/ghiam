"""Revenue leak repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import RevenueLeakModel


class SqlAlchemyRevenueLeakRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self, call_id: UUID, score_id: UUID | None, payload: dict[str, Any]
    ) -> UUID:
        row_id = uuid4()
        amount = payload.get("estimated_amount")
        if amount is None:
            amount = payload.get("estimated_loss_vnd")
        self._session.add(
            RevenueLeakModel(
                id=row_id,
                call_id=call_id,
                score_id=score_id,
                verdict=str(
                    payload.get("verdict")
                    or payload.get("status")
                    or "Insufficient Evidence"
                ),
                estimated_amount=float(amount) if amount is not None else None,
                currency=str(payload.get("currency", "VND")),
                explanation=str(payload.get("explanation", "")),
                components=list(payload.get("components") or []),
                payload=payload,
            )
        )
        await self._session.flush()
        return row_id

    async def get_latest(self, call_id: UUID) -> dict[str, Any] | None:
        stmt = (
            select(RevenueLeakModel)
            .where(RevenueLeakModel.call_id == call_id)
            .order_by(RevenueLeakModel.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return dict(row.payload) if row else None
