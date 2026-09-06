"""CLTV Engine — shared types."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


QUALITY_THRESHOLDS: dict[str, float] = {
    "forecast_accuracy": 0.70,
    "stability": 0.65,
    "explainability": 0.70,
    "evidence_validation": 0.70,
}

PRIORITY_BANDS = ("high_value", "medium", "low")


@dataclass
class CLTVScores:
    cltv_score: float
    retention_score: float
    upsell_score: float
    cross_sell_score: float
    referral_score: float
    lifetime_value: float
    repeat_purchase_probability: float
    churn_risk: float
    referral_probability: float
    priority: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    explanations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CLTVPrediction:
    prediction_id: str
    lead_id: str
    scores: dict[str, Any]
    inputs_used: dict[str, Any]
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
