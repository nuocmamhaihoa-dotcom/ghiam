"""QA calibration sessions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    CalibrationItemModel,
    CalibrationSessionModel,
    CallModel,
    ScoreModel,
    UserModel,
)


class CalibrationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_queue(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Build QA queue from low-confidence / failed / IE calls."""
        scores = list(
            (
                await self._session.execute(
                    select(ScoreModel).order_by(ScoreModel.evaluated_at.desc()).limit(200)
                )
            ).scalars()
        )
        call_ids = [s.call_id for s in scores]
        calls = {
            c.id: c
            for c in (
                await self._session.execute(select(CallModel).where(CallModel.id.in_(call_ids)))
            ).scalars()
        } if call_ids else {}
        agent_ids = {c.agent_user_id for c in calls.values() if c.agent_user_id}
        users = {
            u.id: u
            for u in (
                await self._session.execute(select(UserModel).where(UserModel.id.in_(agent_ids)))
            ).scalars()
        } if agent_ids else {}
        queue: list[dict[str, Any]] = []
        for score in scores:
            result = (score.result or "").lower()
            conf = float((score.payload or {}).get("confidence") or 0.7)
            queue_name = None
            reason = ""
            if "insufficient" in result:
                queue_name, reason = "ie_heavy", "Insufficient Evidence result"
            elif score.auto_fail_triggered:
                queue_name, reason = "compliance", "Auto-fail triggered"
            elif conf < 0.65:
                queue_name, reason = "low_confidence", f"Low AI confidence ({conf:.2f})"
            elif result in {"fail", "failed"}:
                queue_name, reason = "calibration", "Failed score needs human calibration"
            if not queue_name:
                continue
            call = calls.get(score.call_id)
            agent = users.get(call.agent_user_id) if call and call.agent_user_id else None
            queue.append(
                {
                    "call_id": str(score.call_id),
                    "agent_name": agent.full_name if agent else "Unknown",
                    "queue": queue_name,
                    "score": float(score.overall_score),
                    "confidence": conf,
                    "flagged_at": score.evaluated_at.isoformat() if score.evaluated_at else None,
                    "reason": reason,
                }
            )
            if len(queue) >= limit:
                break
        return queue

    async def create_session(self, *, name: str, created_by: UUID | None, notes: str | None = None) -> dict[str, Any]:
        row = CalibrationSessionModel(id=uuid4(), name=name, status="open", created_by=created_by, notes=notes, payload={})
        self._session.add(row)
        await self._session.flush()
        return {"id": str(row.id), "name": row.name, "status": row.status}

    async def submit_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(str(payload["session_id"]))
        call_id = UUID(str(payload["call_id"]))
        session = await self._session.get(CalibrationSessionModel, session_id)
        if session is None:
            raise ValueError("Calibration session not found")
        score = (
            await self._session.execute(
                select(ScoreModel).where(ScoreModel.call_id == call_id).order_by(ScoreModel.evaluated_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        human_score = float(payload["human_score"])
        ai_score = float(score.overall_score) if score else None
        agreement = abs(human_score - ai_score) <= 5 if ai_score is not None else None
        item = CalibrationItemModel(
            id=uuid4(),
            session_id=session_id,
            call_id=call_id,
            reviewer_user_id=UUID(str(payload["reviewer_user_id"])) if payload.get("reviewer_user_id") else None,
            human_score=human_score,
            ai_score=ai_score,
            agreement=agreement,
            notes=payload.get("notes"),
            stage_scores=payload.get("stage_scores") or {},
            payload={},
        )
        self._session.add(item)
        await self._session.flush()
        return {
            "id": str(item.id),
            "session_id": str(session_id),
            "call_id": str(call_id),
            "human_score": human_score,
            "ai_score": ai_score,
            "agreement": agreement,
        }
