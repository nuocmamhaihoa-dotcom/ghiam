"""War Room AI — shared types."""
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
    "alert_precision": 0.70,
    "freshness": 0.70,
    "coverage": 0.65,
    "evidence_validation": 0.70,
}

ALERT_KINDS = (
    "conversion_drop",
    "sla_breach",
    "queue_overflow",
    "objection_spike",
    "churn_risk",
    "idle_agent",
)


@dataclass
class LiveAgent:
    agent_id: str
    name: str
    status: str
    active_calls: int = 0
    idle_sec: float = 0.0
    conversion_rate: float = 0.0
    last_seen_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveCall:
    call_id: str
    agent_id: str
    lead_id: str
    status: str
    duration_sec: int = 0
    sentiment: str = "neutral"
    objection: str | None = None
    buy_signal: float = 0.0
    started_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WarRoomAlert:
    alert_id: str
    kind: str
    severity: str
    title: str
    message: str
    entity_type: str
    entity_id: str
    evidence: list[str] = field(default_factory=list)
    recommended_action: str = ""
    created_at: str = field(default_factory=now_iso)
    acknowledged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
