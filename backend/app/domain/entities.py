"""Domain entities (pure dataclasses — no ORM)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.enums import (
    CallDirection,
    CallStatus,
    RuleSeverity,
    RuleStatus,
    UserStatus,
)


@dataclass(slots=True)
class UserEntity:
    id: UUID
    email: str
    full_name: str
    status: UserStatus
    password_hash: str | None
    tenant_id: UUID | None
    roles: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    last_login_at: datetime | None = None


@dataclass(slots=True)
class RoleEntity:
    id: UUID
    code: str
    name: str
    description: str | None = None


@dataclass(slots=True)
class CallEntity:
    id: UUID
    external_call_id: str
    status: CallStatus
    direction: CallDirection
    agent_user_id: UUID | None
    tenant_id: UUID | None
    campaign_code: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_sec: float | None = None
    customer_phone: str | None = None
    crm_outcome: str | None = None
    crm_order_value: float | None = None
    currency: str = "VND"
    audio_s3_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class TranscriptEntity:
    id: UUID
    call_id: UUID
    language: str
    full_text: str
    turns: list[dict[str, Any]]
    avg_confidence: float
    turn_count: int
    created_at: datetime | None = None


@dataclass(slots=True)
class EvidenceEntity:
    id: UUID
    call_id: UUID
    quote: str
    speaker: str
    stage_key: str | None
    slot: str | None
    confidence: float
    audio_ts_start: float | None
    audio_ts_end: float | None
    turn_index: int | None
    labels: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuleEntity:
    id: UUID
    rule_code: str
    category: str
    title: str
    description: str
    severity: RuleSeverity
    weight: float
    auto_fail: bool
    status: RuleStatus
    evaluator_type: str
    evidence_requirements: dict[str, Any]
    evaluator_config: dict[str, Any]
    current_version: int
    subcategory: str | None = None
    required: bool = True
    cause_code_on_fail: str | None = None
    coaching_template_code: str | None = None
    revenue_impact_code: str | None = None
    industries: list[str] = field(default_factory=lambda: ["*"])


@dataclass(slots=True)
class RuleVersionEntity:
    id: UUID
    rule_id: UUID
    version: int
    snapshot: dict[str, Any]
    created_by: UUID | None
    created_at: datetime | None = None
    change_note: str | None = None


@dataclass(slots=True)
class GoldenCallEntity:
    id: UUID
    call_id: UUID | None
    name: str
    labels: dict[str, Any]
    expected_score: float | None
    notes: str | None = None
    transcript_text: str | None = None
