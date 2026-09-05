"""Domain value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.enums import ScoreResult, Verdict


@dataclass(frozen=True, slots=True)
class EvidenceSpanRef:
    id: UUID | None
    quote: str
    audio_ts_start: float | None
    audio_ts_end: float | None
    turn_index: int | None
    confidence: float
    stage_key: str | None = None
    slot: str | None = None
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class ViolationItem:
    rule_code: str
    title: str
    severity: str
    verdict: Verdict
    explanation: str
    weight: float
    auto_fail: bool = False


@dataclass(slots=True)
class ScoreItemResult:
    rule_code: str
    title: str
    verdict: Verdict
    score: float | None
    weight: float
    confidence: float
    evaluated_at: datetime
    explanation: str
    evidence_spans: list[EvidenceSpanRef]
    scoring_path: list[str]
    category: str | None = None
    severity: str | None = None
    auto_fail: bool = False


@dataclass(slots=True)
class ScoringResponse:
    """Canonical scoring payload required by every scoring endpoint."""

    score: float
    stage_scores: dict[str, float]
    violations: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    root_cause: dict[str, Any]
    coaching: dict[str, Any]
    revenue_leak: dict[str, Any]
    result: ScoreResult = ScoreResult.INSUFFICIENT_EVIDENCE
    explanation: str = ""
    auto_fail_triggered: bool = False
    items: list[ScoreItemResult] = field(default_factory=list)
    schema_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "stage_scores": self.stage_scores,
            "violations": self.violations,
            "evidence": self.evidence,
            "root_cause": self.root_cause,
            "coaching": self.coaching,
            "revenue_leak": self.revenue_leak,
            "result": self.result.value,
            "explanation": self.explanation,
            "auto_fail_triggered": self.auto_fail_triggered,
            "schema_version": self.schema_version,
        }


def insufficient_evidence_response(
    *,
    explanation: str = "No evidence available to score this call.",
) -> ScoringResponse:
    return ScoringResponse(
        score=0,
        stage_scores={},
        violations=[],
        evidence=[],
        root_cause={
            "verdict": "Insufficient Evidence",
            "primary_cause_code": None,
            "causes": [],
            "explanation": explanation,
        },
        coaching={
            "plan_id": None,
            "call_tips": [],
            "explanation": explanation,
        },
        revenue_leak={
            "verdict": "Insufficient Evidence",
            "estimated_amount": None,
            "currency": "VND",
            "explanation": explanation,
        },
        result=ScoreResult.INSUFFICIENT_EVIDENCE,
        explanation=explanation,
        auto_fail_triggered=False,
        items=[],
    )
