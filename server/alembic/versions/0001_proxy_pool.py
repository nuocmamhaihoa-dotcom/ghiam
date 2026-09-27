"""Kho proxy: bảng proxies và proxy_leases

Revision ID: 0001_proxy_pool
Revises:
Create Date: 2026-09-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_proxy_pool"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC_DATETIME = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "proxies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("protocol", sa.String(length=8), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=True),
        sa.Column("uses_session", sa.Boolean(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("pool", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("rotation_url_enc", sa.Text(), nullable=True),
        sa.Column("rotation_method", sa.String(length=8), nullable=False),
        sa.Column("rotation_interval_sec", sa.Integer(), nullable=False),
        sa.Column("rotation_cooldown_sec", sa.Integer(), nullable=False),
        sa.Column("rotate_on_block", sa.Boolean(), nullable=False),
        sa.Column("rotation_state", sa.String(length=16), nullable=False),
        sa.Column("rotation_requested_at", UTC_DATETIME, nullable=True),
        sa.Column("last_rotation_attempt_at", UTC_DATETIME, nullable=True),
        sa.Column("last_rotated_at", UTC_DATETIME, nullable=True),
        sa.Column("last_rotation_ok", sa.Boolean(), nullable=True),
        sa.Column("last_rotation_message", sa.String(length=500), nullable=True),
        sa.Column("rotation_count", sa.Integer(), nullable=False),
        sa.Column("health", sa.String(length=16), nullable=False),
        sa.Column("check_in_progress", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", UTC_DATETIME, nullable=True),
        sa.Column("last_check_error", sa.String(length=500), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("exit_ip", sa.String(length=64), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("isp", sa.String(length=255), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("quarantined_until", UTC_DATETIME, nullable=True),
        sa.Column("last_used_at", UTC_DATETIME, nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxies")),
        sa.UniqueConstraint("protocol", "host", "port", "username", name="uq_proxies_endpoint"),
    )
    for column in ("kind", "host", "pool", "enabled", "rotation_state", "health", "last_used_at"):
        op.create_index(op.f(f"ix_proxies_{column}"), "proxies", [column], unique=False)

    op.create_table(
        "proxy_leases",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("proxy_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("job_ref", sa.String(length=128), nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("expires_at", UTC_DATETIME, nullable=False),
        sa.Column("released_at", UTC_DATETIME, nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["proxy_id"],
            ["proxies.id"],
            name=op.f("fk_proxy_leases_proxy_id_proxies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxy_leases")),
    )
    op.create_index("ix_proxy_leases_active", "proxy_leases", ["proxy_id", "released_at"], unique=False)
    op.create_index(op.f("ix_proxy_leases_worker_id"), "proxy_leases", ["worker_id"], unique=False)
    op.create_index(op.f("ix_proxy_leases_expires_at"), "proxy_leases", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("proxy_leases")
    op.drop_table("proxies")
