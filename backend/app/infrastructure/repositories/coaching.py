"""Coaching plan repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import CoachingPlanModel


class SqlAlchemyCoachingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_for_call(
        self, call_id: UUID, agent_user_id: UUID | None, payload: dict[str, Any]
    ) -> UUID:
        row_id = uuid4()
        self._session.add(
            CoachingPlanModel(
                id=row_id,
                call_id=call_id,
                agent_user_id=agent_user_id,
                call_tips=list(payload.get("call_tips") or []),
                explanation=str(payload.get("explanation", "")),
                payload=payload,
                status="active",
            )
        )
        await self._session.flush()
        return row_id

    async def list_for_agent(
        self, agent_user_id: UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        stmt = (
            select(CoachingPlanModel)
            .where(CoachingPlanModel.agent_user_id == agent_user_id)
            .order_by(CoachingPlanModel.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(r.id),
                "call_id": str(r.call_id) if r.call_id else None,
                **dict(r.payload or {}),
            }
            for r in rows
        ]

    async def get(self, plan_id: UUID) -> dict[str, Any] | None:
        row = await self._session.get(CoachingPlanModel, plan_id)
        if not row:
            return None
        return {
            "id": str(row.id),
            "call_id": str(row.call_id) if row.call_id else None,
            **dict(row.payload or {}),
        }
