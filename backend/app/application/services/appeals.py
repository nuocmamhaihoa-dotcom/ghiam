"""Appeal workflow service."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models import AppealModel, CallModel, UserModel


class AppealService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_appeals(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        stmt = select(AppealModel).order_by(AppealModel.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(AppealModel.status == status)
        rows = list((await self._session.execute(stmt)).scalars())
        agent_ids = {row.agent_user_id for row in rows if row.agent_user_id}
        users: dict[UUID, UserModel] = {}
        if agent_ids:
            for user in (await self._session.execute(select(UserModel).where(UserModel.id.in_(agent_ids)))).scalars():
                users[user.id] = user
        result: list[dict[str, Any]] = []
        for row in rows:
            agent = users.get(row.agent_user_id) if row.agent_user_id else None
            result.append(
                {
                    "id": str(row.id),
                    "call_id": str(row.call_id),
                    "agent_name": agent.full_name if agent else "Unknown",
                    "rule_code": row.rule_code,
                    "rule_title": row.rule_title,
                    "current_verdict": row.current_verdict,
                    "proposed_verdict": row.proposed_verdict,
                    "reason_code": row.reason_code,
                    "reason_text": row.reason_text,
                    "status": row.status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "evidence_quote": row.evidence_quote,
                }
            )
        return result

    async def create_appeal(self, payload: dict[str, Any], *, actor_user_id: UUID | None) -> dict[str, Any]:
        call_id = UUID(str(payload["call_id"]))
        call = await self._session.get(CallModel, call_id)
        if call is None:
            raise ValueError("Call not found")
        row = AppealModel(
            id=uuid4(),
            call_id=call_id,
            agent_user_id=call.agent_user_id or actor_user_id,
            rule_code=str(payload["rule_code"]),
            rule_title=str(payload.get("rule_title") or payload["rule_code"]),
            current_verdict=str(payload.get("current_verdict") or "fail"),
            proposed_verdict=str(payload.get("proposed_verdict") or "pass"),
            reason_code=str(payload.get("reason_code") or "other"),
            reason_text=str(payload.get("reason_text") or ""),
            evidence_quote=payload.get("evidence_quote"),
            status="open",
            payload={},
        )
        self._session.add(row)
        await self._session.flush()
        items = await self.list_appeals(limit=1)
        return items[0] if items else {"id": str(row.id)}

    async def resolve_appeal(
        self,
        appeal_id: UUID,
        *,
        status: str,
        resolution_note: str | None,
        reviewer_user_id: UUID | None,
    ) -> dict[str, Any]:
        row = await self._session.get(AppealModel, appeal_id)
        if row is None:
            raise ValueError("Appeal not found")
        if status not in {"open", "under_review", "overturned", "upheld"}:
            raise ValueError("Invalid appeal status")
        row.status = status
        row.resolution_note = resolution_note
        row.reviewer_user_id = reviewer_user_id
        await self._session.flush()
        return {"id": str(row.id), "status": row.status}
