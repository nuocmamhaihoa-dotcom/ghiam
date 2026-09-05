"""Shared types for Autonomous Sales AI."""
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
    "recommendation_precision": 0.70,
    "automation_safety": 0.85,
    "approval_enforcement": 0.95,
    "evidence_validation": 0.70,
}

NBA_ACTIONS = (
    "callback",
    "zalo_message",
    "email",
    "escalate_leader",
    "reassign_sale",
    "close_lead",
    "send_proposal",
    "coaching_nudge",
)

SAFE_AUTOMATIONS = (
    "callback_reminder",
    "follow_up_message",
    "crm_note",
    "create_task",
    "coaching_nudge",
)

RESTRICTED_CHANGES = (
    "rule_change",
    "sop_change",
    "pricing_change",
    "routing_policy_change",
)


@dataclass
class Recommendation:
    recommendation_id: str
    action: str
    confidence: float
    rationale: str
    evidence: list[str] = field(default_factory=list)
    expected_impact: float = 0.0
    requires_approval: bool = False
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AutomationJob:
    job_id: str
    kind: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    completed_at: str | None = None
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRequest:
    approval_id: str
    change_type: str
    title: str
    proposal: dict[str, Any]
    status: str = "pending"
    evidence: list[str] = field(default_factory=list)
    requested_by: str = "autonomous_ai"
    created_at: str = field(default_factory=now_iso)
    decided_at: str | None = None
    decided_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
