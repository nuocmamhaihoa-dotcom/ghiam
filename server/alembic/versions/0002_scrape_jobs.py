"""Job quét comment Facebook: scrape_jobs, scrape_posts, scrape_attempts, comments

Revision ID: 0002_scrape_jobs
Revises: 0001_proxy_pool
Create Date: 2026-10-01 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_scrape_jobs"
down_revision: str | None = "0001_proxy_pool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC_DATETIME = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "scrape_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_comments", sa.Integer(), nullable=False),
        sa.Column("include_replies", sa.Boolean(), nullable=False),
        sa.Column("max_replies_per_comment", sa.Integer(), nullable=False),
        sa.Column("sort", sa.String(length=16), nullable=False),
        sa.Column("proxy_pool", sa.String(length=64), nullable=True),
        sa.Column("proxy_kind", sa.String(length=16), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("time_budget_sec", sa.Integer(), nullable=False),
        sa.Column("max_parallel", sa.Integer(), nullable=False),
        sa.Column("author_mode", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("started_at", UTC_DATETIME, nullable=True),
        sa.Column("finished_at", UTC_DATETIME, nullable=True),
        sa.Column("last_claimed_at", UTC_DATETIME, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scrape_jobs")),
    )
    op.create_index(op.f("ix_scrape_jobs_status"), "scrape_jobs", ["status"], unique=False)

    op.create_table(
        "scrape_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("not_before", UTC_DATETIME, nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("comments_count", sa.Integer(), nullable=False),
        sa.Column("comments_reported", sa.Integer(), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.Column("blocked_proxy_ids", sa.Text(), nullable=False),
        sa.Column("started_at", UTC_DATETIME, nullable=True),
        sa.Column("finished_at", UTC_DATETIME, nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["scrape_jobs.id"], name=op.f("fk_scrape_posts_job_id_scrape_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scrape_posts")),
        sa.UniqueConstraint("job_id", "url", name="uq_scrape_posts_job_url"),
    )
    op.create_index("ix_scrape_posts_status_not_before", "scrape_posts", ["status", "not_before"], unique=False)
    op.create_index("ix_scrape_posts_job_status", "scrape_posts", ["job_id", "status"], unique=False)

    op.create_table(
        "scrape_attempts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("proxy_lease_id", sa.String(length=32), nullable=True),
        sa.Column("proxy_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", UTC_DATETIME, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("comments_count", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("bytes_transferred", sa.Integer(), nullable=False),
        sa.Column("started_at", UTC_DATETIME, nullable=False),
        sa.Column("finished_at", UTC_DATETIME, nullable=True),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["scrape_posts.id"],
            name=op.f("fk_scrape_attempts_post_id_scrape_posts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scrape_attempts")),
    )
    op.create_index(op.f("ix_scrape_attempts_post_id"), "scrape_attempts", ["post_id"], unique=False)
    op.create_index(op.f("ix_scrape_attempts_worker_id"), "scrape_attempts", ["worker_id"], unique=False)
    op.create_index(op.f("ix_scrape_attempts_expires_at"), "scrape_attempts", ["expires_at"], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("parent_external_id", sa.String(length=128), nullable=True),
        sa.Column("author", sa.String(length=300), nullable=False),
        sa.Column("author_id", sa.String(length=128), nullable=True),
        sa.Column("author_url", sa.String(length=500), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("time", UTC_DATETIME, nullable=True),
        sa.Column("time_raw", sa.String(length=80), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("likes_raw", sa.String(length=32), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", UTC_DATETIME, nullable=False),
        sa.Column("last_seen_at", UTC_DATETIME, nullable=False),
        sa.Column("attempt_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["scrape_jobs.id"], name=op.f("fk_comments_job_id_scrape_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["post_id"], ["scrape_posts.id"], name=op.f("fk_comments_post_id_scrape_posts"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
        sa.UniqueConstraint("post_id", "external_id", name="uq_comments_post_external"),
    )
    op.create_index("ix_comments_job_id_id", "comments", ["job_id", "id"], unique=False)
    op.create_index("ix_comments_post_time", "comments", ["post_id", "time"], unique=False)


def downgrade() -> None:
    op.drop_table("comments")
    op.drop_table("scrape_attempts")
    op.drop_table("scrape_posts")
    op.drop_table("scrape_jobs")
