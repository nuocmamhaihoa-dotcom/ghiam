"""Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ErrorResponse(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str | None = None
    request_id: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    tenant_code: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    time: datetime


class ReadyCheck(BaseModel):
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, ReadyCheck]


class CallCreateRequest(BaseModel):
    external_call_id: str
    campaign_code: str | None = None
    agent_email: str | None = None
    agent_user_id: UUID | None = None
    direction: str = "outbound"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_sec: float | None = Field(default=None, gt=0)
    customer_phone: str | None = None
    crm_outcome: str | None = None
    crm_order_value: float | None = None
    currency: str = "VND"
    audio_content_type: str = "audio/mpeg"
    metadata: dict[str, Any] = Field(default_factory=dict)
    transcript_text: str | None = None
    transcript_turns: list[dict[str, Any]] | None = None
    evidence: list[dict[str, Any]] | None = None


class CallResponse(BaseModel):
    id: UUID
    external_call_id: str
    status: str
    direction: str
    campaign_code: str | None = None
    agent_user_id: UUID | None = None
    duration_sec: float | None = None
    crm_outcome: str | None = None
    upload: dict[str, Any] | None = None
    created_at: datetime | None = None


class CallListResponse(BaseModel):
    data: list[CallResponse]
    limit: int
    offset: int


class TranscriptIngestRequest(BaseModel):
    language: str = "vi"
    full_text: str = ""
    turns: list[dict[str, Any]] = Field(default_factory=list)
    avg_confidence: float = Field(default=0.0, ge=0, le=1)


class EvidenceIngestRequest(BaseModel):
    items: list[dict[str, Any]]


class ScoringResponseSchema(BaseModel):
    score: float
    stage_scores: dict[str, float]
    violations: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    root_cause: dict[str, Any]
    coaching: dict[str, Any]
    revenue_leak: dict[str, Any]
    result: str | None = None
    explanation: str | None = None
    auto_fail_triggered: bool | None = None
    schema_version: str | None = None
    call_id: str | None = None


class RuleCreateRequest(BaseModel):
    rule_code: str
    category: str
    title: str
    description: str = ""
    severity: str = "major"
    weight: float = 1.0
    auto_fail: bool = False
    required: bool = True
    status: str = "active"
    evaluator_type: str = "keyword"
    evidence_requirements: dict[str, Any] = Field(default_factory=dict)
    evaluator_config: dict[str, Any] = Field(default_factory=dict)
    subcategory: str | None = None
    cause_code_on_fail: str | None = None
    coaching_template_code: str | None = None
    revenue_impact_code: str | None = None
    industries: list[str] = Field(default_factory=lambda: ["*"])


class RuleUpdateRequest(BaseModel):
    category: str | None = None
    title: str | None = None
    description: str | None = None
    severity: str | None = None
    weight: float | None = None
    auto_fail: bool | None = None
    required: bool | None = None
    status: str | None = None
    evaluator_type: str | None = None
    evidence_requirements: dict[str, Any] | None = None
    evaluator_config: dict[str, Any] | None = None
    subcategory: str | None = None
    cause_code_on_fail: str | None = None
    coaching_template_code: str | None = None
    revenue_impact_code: str | None = None
    industries: list[str] | None = None
    change_note: str | None = None


class RuleResponse(BaseModel):
    id: UUID
    rule_code: str
    category: str
    title: str
    description: str
    severity: str
    weight: float
    auto_fail: bool
    status: str
    evaluator_type: str
    evidence_requirements: dict[str, Any]
    evaluator_config: dict[str, Any]
    current_version: int
    subcategory: str | None = None
    required: bool = True
    cause_code_on_fail: str | None = None
    coaching_template_code: str | None = None
    revenue_impact_code: str | None = None
    industries: list[str] = Field(default_factory=list)


class RuleListResponse(BaseModel):
    data: list[RuleResponse]
    total: int
    limit: int
    offset: int


class GoldenCallCreateRequest(BaseModel):
    name: str
    call_id: UUID | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    expected_score: float | None = None
    notes: str | None = None
    transcript_text: str | None = None


class DashboardSummary(BaseModel):
    calls_total: int
    calls_scored: int
    rules_active: int
    avg_score: float | None = None
