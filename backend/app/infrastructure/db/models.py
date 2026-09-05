"""SQLAlchemy ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    users: Mapped[list[UserModel]] = relationship(
        secondary="user_roles", back_populates="roles"
    )


class UserModel(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[RoleModel]] = relationship(
        secondary="user_roles", back_populates="users", lazy="selectin"
    )


class UserRoleModel(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CallModel(Base, TimestampMixin):
    __tablename__ = "calls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_call_id", name="uq_calls_tenant_external"),
        Index("idx_calls_status", "status"),
        Index("idx_calls_agent", "agent_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    external_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_code: Mapped[str | None] = mapped_column(String(128))
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="outbound")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[float | None] = mapped_column(Float)
    customer_phone: Mapped[str | None] = mapped_column(String(64))
    crm_outcome: Mapped[str | None] = mapped_column(String(64))
    crm_order_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    audio_s3_key: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    transcripts: Mapped[list[TranscriptModel]] = relationship(back_populates="call")
    evidence_items: Mapped[list[EvidenceModel]] = relationship(back_populates="call")
    scores: Mapped[list[ScoreModel]] = relationship(back_populates="call")


class TranscriptModel(Base, TimestampMixin):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="vi")
    full_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    turns: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    avg_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    call: Mapped[CallModel] = relationship(back_populates="transcripts")


class EvidenceModel(Base, TimestampMixin):
    __tablename__ = "evidence"
    __table_args__ = (Index("idx_evidence_call_stage", "call_id", "stage_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    stage_key: Mapped[str | None] = mapped_column(String(64))
    slot: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    audio_ts_start: Mapped[float | None] = mapped_column(Float)
    audio_ts_end: Mapped[float | None] = mapped_column(Float)
    turn_index: Mapped[int | None] = mapped_column(Integer)
    labels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    call: Mapped[CallModel] = relationship(back_populates="evidence_items")


class RuleModel(Base, TimestampMixin):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subcategory: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="major")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    auto_fail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    evaluator_type: Mapped[str] = mapped_column(String(64), nullable=False, default="keyword")
    evidence_requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evaluator_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cause_code_on_fail: Mapped[str | None] = mapped_column(String(64))
    coaching_template_code: Mapped[str | None] = mapped_column(String(64))
    revenue_impact_code: Mapped[str | None] = mapped_column(String(64))
    industries: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"*\"]'::jsonb")
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[RuleVersionModel]] = relationship(back_populates="rule")


class RuleVersionModel(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_rule_versions_rule_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rule: Mapped[RuleModel] = relationship(back_populates="versions")


class ScoreModel(Base, TimestampMixin):
    __tablename__ = "scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    auto_fail_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stage_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    call: Mapped[CallModel] = relationship(back_populates="scores")
    violations: Mapped[list[ViolationModel]] = relationship(back_populates="score")


class ViolationModel(Base, TimestampMixin):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    score_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    auto_fail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    score: Mapped[ScoreModel] = relationship(back_populates="violations")


class RootCauseModel(Base, TimestampMixin):
    __tablename__ = "root_causes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scores.id", ondelete="SET NULL")
    )
    verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_cause_code: Mapped[str | None] = mapped_column(String(64))
    causes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class CoachingPlanModel(Base, TimestampMixin):
    __tablename__ = "coaching_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_tips: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class RevenueLeakModel(Base, TimestampMixin):
    __tablename__ = "revenue_leaks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scores.id", ondelete="SET NULL")
    )
    verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    components: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_logs_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GoldenCallModel(Base, TimestampMixin):
    __tablename__ = "golden_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expected_score: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    transcript_text: Mapped[str | None] = mapped_column(Text)


class AppealModel(Base, TimestampMixin):
    __tablename__ = "appeals"
    __table_args__ = (Index("idx_appeals_status", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    reason_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class CalibrationSessionModel(Base, TimestampMixin):
    __tablename__ = "calibration_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class CalibrationItemModel(Base, TimestampMixin):
    __tablename__ = "calibration_items"
    __table_args__ = (
        UniqueConstraint("session_id", "call_id", "reviewer_user_id", name="uq_calibration_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calibration_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    human_score: Mapped[float | None] = mapped_column(Float)
    ai_score: Mapped[float | None] = mapped_column(Float)
    agreement: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    stage_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class ConversationDnaModel(Base, TimestampMixin):
    __tablename__ = "conversation_dna"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emotion_timeline: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class DatasetExportModel(Base, TimestampMixin):
    __tablename__ = "dataset_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False, default="scoring")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sample_rows: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
