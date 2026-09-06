"""Score / violation repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.value_objects import ScoringResponse
from app.infrastructure.db.models import ScoreModel, ViolationModel


class SqlAlchemyScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_scoring(self, call_id: UUID, response: ScoringResponse) -> UUID:
        score_id = uuid4()
        payload = response.to_dict()
        row = ScoreModel(
            id=score_id,
            call_id=call_id,
            overall_score=response.score,
            result=response.result.value,
            auto_fail_triggered=response.auto_fail_triggered,
            explanation=response.explanation,
            stage_scores=response.stage_scores,
            items=[
                {
                    "rule_code": i.rule_code,
                    "title": i.title,
                    "verdict": i.verdict.value,
                    "score": i.score,
                    "weight": i.weight,
                    "confidence": i.confidence,
                    "evaluated_at": i.evaluated_at.isoformat(),
                    "explanation": i.explanation,
                    "scoring_path": i.scoring_path,
                    "category": i.category,
                }
                for i in response.items
            ],
            payload=payload,
        )
        self._session.add(row)
        for violation in response.violations:
            self._session.add(
                ViolationModel(
                    id=uuid4(),
                    score_id=score_id,
                    call_id=call_id,
                    rule_code=str(violation.get("rule_code", "")),
                    title=str(violation.get("title", "")),
                    severity=str(violation.get("severity", "major")),
                    verdict=str(violation.get("verdict", "fail")),
                    explanation=str(violation.get("explanation", "")),
                    weight=float(violation.get("weight", 1.0)),
                    auto_fail=bool(violation.get("auto_fail", False)),
                    details=violation,
                )
            )
        await self._session.flush()
        return score_id

    async def get_latest_for_call(self, call_id: UUID) -> dict[str, Any] | None:
        stmt = (
            select(ScoreModel)
            .where(ScoreModel.call_id == call_id)
            .order_by(ScoreModel.evaluated_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if not row:
            return None
        return dict(row.payload or {})
