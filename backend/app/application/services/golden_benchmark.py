"""Golden call benchmark comparisons — evidence-first delta analysis."""

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
        stage_scores = dict(score.stage_scores or {}) if score else {}
        expected_stages = dict(golden.expected_stage_scores or {}) if hasattr(golden, "expected_stage_scores") else {}
        stage_deltas = {
            key: round(float(stage_scores.get(key, 0)) - float(expected_stages.get(key, 0)), 2)
            for key in sorted(set(stage_scores) | set(expected_stages))
        }
        passed = abs(delta) <= 5 if delta is not None else False
        gaps = [
            {"stage": stage, "delta": value}
            for stage, value in stage_deltas.items()
            if value < -5
        ]

        payload_score = actual if actual is not None else 0.0
        explanation = (
            f"Golden '{golden.name}': expected {expected}, actual {actual}, delta {delta}."
            if expected is not None
            else "Golden call missing expected_score; cannot compute delta."
        )
        return {
            "score": payload_score,
            "stage_scores": stage_scores,
            "violations": [
                {
                    "rule_code": f"GOLDEN-GAP-{gap['stage'].upper()}",
                    "title": f"Below golden benchmark on {gap['stage']}",
                    "severity": "major" if gap["delta"] <= -15 else "minor",
                    "verdict": "fail",
                    "explanation": f"Stage delta {gap['delta']} vs golden call.",
                    "weight": 1.0,
                    "auto_fail": False,
                }
                for gap in gaps
            ],
            "evidence": [],
            "root_cause": {
                "verdict": "benchmark" if delta is not None else "Insufficient Evidence",
                "status": "ok" if delta is not None else "Insufficient Evidence",
                "primary_code": gaps[0]["stage"] if gaps else None,
                "primary_cause_code": gaps[0]["stage"] if gaps else None,
                "label": "Gap vs golden call" if gaps else None,
                "confidence": 0.7 if gaps else None,
                "contributing_factors": [g["stage"] for g in gaps[1:5]],
                "evidence_refs": [],
                "causes": [
                    {
                        "cause_code": g["stage"],
                        "label": f"Stage {g['stage']} below golden",
                        "weight": abs(g["delta"]),
                    }
                    for g in gaps
                ],
                "children": [],
                "explanation": explanation,
                "reason": explanation,
            },
            "coaching": {
                "plan_id": None,
                "priority": "high" if gaps else "low",
                "tips": [
                    {
                        "tip_id": f"tip.golden.{gap['stage']}",
                        "title": f"Close gap on {gap['stage']}",
                        "script_suggestion": (
                            f"Replay golden call segment for stage {gap['stage']} "
                            f"and mirror the discovery/value sequence."
                        ),
                        "linked_rule_ids": [],
                        "evidence_refs": [],
                    }
                    for gap in gaps[:5]
                ],
                "call_tips": [],
                "drill_ids": [f"drill.golden.{gap['stage']}" for gap in gaps[:5]],
                "explanation": "Use golden transcript as coaching reference.",
            },
            "revenue_leak": {
                "verdict": "n/a",
                "status": "ok",
                "estimated_amount": None,
                "estimated_loss_vnd": None,
                "currency": "VND",
                "leak_codes": [],
                "probability": None,
                "components": [],
                "evidence_refs": [],
                "explanation": "Benchmark mode does not estimate revenue leak.",
            },
            "golden_id": str(golden.id),
            "golden_name": golden.name,
            "expected_score": expected,
            "actual_score": actual,
            "delta": delta,
            "stage_deltas": stage_deltas,
            "call_id": str(target_call_id),
            "pass": passed,
            "explanation": explanation,
        }


# Back-compat alias used by API routers
GoldenBenchmarkService = GoldenBenchmarkService
