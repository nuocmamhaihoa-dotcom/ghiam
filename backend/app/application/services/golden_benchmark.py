"""Golden call benchmark comparisons."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import GoldenCallModel, ScoreModel


class GoldenBenchmarkService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def compare(self, *, golden_id: UUID, call_id: UUID | None = None) -> dict[str, Any]:
        golden = await self._session.get(GoldenCallModel, golden_id)
        if golden is None:
            raise ValueError("Golden call not found")
        target_call_id = call_id or golden.call_id
        if target_call_id is None:
            raise ValueError("No call_id available for comparison")
        score = (
            await self._session.execute(
                select(ScoreModel)
                .where(ScoreModel.call_id == target_call_id)
                .order_by(ScoreModel.evaluated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        actual = float(score.overall_score) if score else None
        expected = float(golden.expected_score) if golden.expected_score is not None else None
        delta = (actual - expected) if actual is not None and expected is not None else None
        return {
            "score": actual if actual is not None else 0.0,
            "stage_scores": dict(score.stage_scores or {}) if score else {},
            "violations": [],
            "evidence": [],
            "root_cause": {"verdict": "benchmark", "primary_cause_code": None, "causes": [], "explanation": "Golden call benchmark comparison."},
            "coaching": {"plan_id": None, "call_tips": [], "explanation": "Use golden transcript as coaching reference."},
            "revenue_leak": {"verdict": "n/a", "estimated_amount": None, "currency": "VND", "explanation": "Benchmark mode does not estimate revenue leak."},
            "golden_id": str(golden.id),
            "golden_name": golden.name,
            "expected_score": expected,
            "actual_score": actual,
            "delta": delta,
            "call_id": str(target_call_id),
            "pass": (abs(delta) <= 5) if delta is not None else False,
        }
