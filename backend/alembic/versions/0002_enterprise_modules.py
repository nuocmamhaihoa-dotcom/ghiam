"""Add appeals, calibration, conversation DNA, dataset exports."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_enterprise_modules"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appeals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("rule_code", sa.String(64), nullable=False),
        sa.Column("rule_title", sa.String(255), nullable=False, server_default=""),
        sa.Column("current_verdict", sa.String(64), nullable=False),
        sa.Column("proposed_verdict", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default="other"),
        sa.Column("reason_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_quote", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_appeals_call_id", "appeals", ["call_id"])
    op.create_index("ix_appeals_agent_user_id", "appeals", ["agent_user_id"])
    op.create_index("idx_appeals_status", "appeals", ["status"])

    op.create_table(
        "calibration_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "calibration_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calibration_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("human_score", sa.Float()),
        sa.Column("ai_score", sa.Float()),
        sa.Column("agreement", sa.Boolean()),
        sa.Column("notes", sa.Text()),
        sa.Column("stage_scores", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "call_id", "reviewer_user_id", name="uq_calibration_item"),
    )
    op.create_index("ix_calibration_items_session_id", "calibration_items", ["session_id"])
    op.create_index("ix_calibration_items_call_id", "calibration_items", ["call_id"])
    op.create_index("ix_calibration_items_reviewer_user_id", "calibration_items", ["reviewer_user_id"])

    op.create_table(
        "conversation_dna",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("emotion_timeline", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversation_dna_call_id", "conversation_dna", ["call_id"], unique=True)

    op.create_table(
        "dataset_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("dataset_type", sa.String(64), nullable=False, server_default="scoring"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sample_rows", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dataset_exports")
    op.drop_index("ix_conversation_dna_call_id", table_name="conversation_dna")
    op.drop_table("conversation_dna")
    op.drop_table("calibration_items")
    op.drop_table("calibration_sessions")
    op.drop_table("appeals")
