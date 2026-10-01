from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, MetaData, String, Text, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.timeutil import utcnow


class UTCDateTime(TypeDecorator[datetime]):
    """Luôn trả về datetime UTC có múi giờ; SQLite không lưu múi giờ nên lưu dạng naive UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UTCDateTime()}  # noqa: RUF012


class ProxyKind(StrEnum):
    STATIC = "static"
    ROTATING = "rotating"


class ProxyProtocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"
    SOCKS5 = "socks5"


class ProxyHealth(StrEnum):
    UNCHECKED = "unchecked"
    ALIVE = "alive"
    DEAD = "dead"


class RotationState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    ROTATING = "rotating"


class RotationMode(StrEnum):
    NONE = "none"
    URL = "url"
    SESSION = "session"
    PROVIDER = "provider"


class LeaseOutcome(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class JobStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PostStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptOutcome(StrEnum):
    DONE = "done"
    NOT_AVAILABLE = "not_available"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AuthorMode(StrEnum):
    FULL = "full"
    NAME = "name"
    ANON = "anon"


def resolve_rotation_mode(
    kind: str, *, has_rotation_url: bool, uses_session: bool, rotation_interval_sec: int
) -> RotationMode:
    if kind != ProxyKind.ROTATING:
        return RotationMode.NONE
    if has_rotation_url:
        return RotationMode.URL
    if uses_session:
        return RotationMode.SESSION
    if rotation_interval_sec > 0:
        return RotationMode.PROVIDER
    return RotationMode.NONE


class Proxy(Base):
    __tablename__ = "proxies"
    __table_args__ = (UniqueConstraint("protocol", "host", "port", "username", name="uq_proxies_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    protocol: Mapped[str] = mapped_column(String(8))
    host: Mapped[str] = mapped_column(String(255), index=True)
    port: Mapped[int] = mapped_column(Integer)
    # Chuỗi rỗng thay vì NULL để ràng buộc UNIQUE hoạt động với proxy không có tài khoản.
    username: Mapped[str] = mapped_column(String(255), default="")
    password_enc: Mapped[str | None] = mapped_column(Text, default=None)
    uses_session: Mapped[bool] = mapped_column(Boolean, default=False)
    session_id: Mapped[str | None] = mapped_column(String(64), default=None)
    pool: Mapped[str] = mapped_column(String(64), default="default", index=True)
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)

    rotation_url_enc: Mapped[str | None] = mapped_column(Text, default=None)
    rotation_method: Mapped[str] = mapped_column(String(8), default="GET")
    rotation_interval_sec: Mapped[int] = mapped_column(Integer, default=0)
    rotation_cooldown_sec: Mapped[int] = mapped_column(Integer, default=60)
    rotate_on_block: Mapped[bool] = mapped_column(Boolean, default=True)
    rotation_state: Mapped[str] = mapped_column(String(16), default=RotationState.IDLE, index=True)
    rotation_requested_at: Mapped[datetime | None] = mapped_column(default=None)
    last_rotation_attempt_at: Mapped[datetime | None] = mapped_column(default=None)
    last_rotated_at: Mapped[datetime | None] = mapped_column(default=None)
    last_rotation_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    last_rotation_message: Mapped[str | None] = mapped_column(String(500), default=None)
    rotation_count: Mapped[int] = mapped_column(Integer, default=0)

    health: Mapped[str] = mapped_column(String(16), default=ProxyHealth.UNCHECKED, index=True)
    check_in_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(default=None)
    last_check_error: Mapped[str | None] = mapped_column(String(500), default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    exit_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    country: Mapped[str | None] = mapped_column(String(8), default=None)
    isp: Mapped[str | None] = mapped_column(String(255), default=None)

    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_until: Mapped[datetime | None] = mapped_column(default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None, index=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    @property
    def rotation_mode(self) -> RotationMode:
        return resolve_rotation_mode(
            self.kind,
            has_rotation_url=self.rotation_url_enc is not None,
            uses_session=self.uses_session,
            rotation_interval_sec=self.rotation_interval_sec,
        )


class ProxyLease(Base):
    __tablename__ = "proxy_leases"
    __table_args__ = (Index("ix_proxy_leases_active", "proxy_id", "released_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    proxy_id: Mapped[int] = mapped_column(ForeignKey("proxies.id", ondelete="CASCADE"))
    worker_id: Mapped[str] = mapped_column(String(128), index=True)
    job_ref: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    released_at: Mapped[datetime | None] = mapped_column(default=None)
    outcome: Mapped[str | None] = mapped_column(String(16), default=None)
    detail: Mapped[str | None] = mapped_column(String(500), default=None)


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.RUNNING, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    max_comments: Mapped[int] = mapped_column(Integer, default=200)
    include_replies: Mapped[bool] = mapped_column(Boolean, default=True)
    max_replies_per_comment: Mapped[int] = mapped_column(Integer, default=20)
    sort: Mapped[str] = mapped_column(String(16), default="newest")
    proxy_pool: Mapped[str | None] = mapped_column(String(64), default=None)
    proxy_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    time_budget_sec: Mapped[int] = mapped_column(Integer, default=180)
    max_parallel: Mapped[int] = mapped_column(Integer, default=2)
    author_mode: Mapped[str] = mapped_column(String(16), default=AuthorMode.FULL)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    last_claimed_at: Mapped[datetime | None] = mapped_column(default=None)


class ScrapePost(Base):
    __tablename__ = "scrape_posts"
    __table_args__ = (
        UniqueConstraint("job_id", "url", name="uq_scrape_posts_job_url"),
        Index("ix_scrape_posts_status_not_before", "status", "not_before"),
        Index("ix_scrape_posts_job_status", "job_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(1000))
    platform: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=PostStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    comments_reported: Mapped[int | None] = mapped_column(Integer, default=None)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    blocked_proxy_ids: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class ScrapeAttempt(Base):
    __tablename__ = "scrape_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("scrape_posts.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[str] = mapped_column(String(128), index=True)
    proxy_lease_id: Mapped[str | None] = mapped_column(String(32), default=None)
    proxy_id: Mapped[int | None] = mapped_column(Integer, default=None)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str | None] = mapped_column(String(16), default=None)
    detail: Mapped[str | None] = mapped_column(String(500), default=None)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    bytes_transferred: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class Comment(Base):
    """Comment công khai đã đọc được. Khoá tự tăng là khoá nội bộ; external_id là mã trên Facebook."""

    __tablename__ = "comments"
    __table_args__ = (
        UniqueConstraint("post_id", "external_id", name="uq_comments_post_external"),
        Index("ix_comments_job_id_id", "job_id", "id"),
        Index("ix_comments_post_time", "post_id", "time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id", ondelete="CASCADE"))
    post_id: Mapped[int] = mapped_column(ForeignKey("scrape_posts.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    parent_external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    author: Mapped[str] = mapped_column(String(300))
    author_id: Mapped[str | None] = mapped_column(String(128), default=None)
    author_url: Mapped[str | None] = mapped_column(String(500), default=None)
    text: Mapped[str] = mapped_column(Text)
    time: Mapped[datetime | None] = mapped_column(default=None)
    time_raw: Mapped[str | None] = mapped_column(String(80), default=None)
    likes: Mapped[int | None] = mapped_column(Integer, default=None)
    likes_raw: Mapped[str | None] = mapped_column(String(32), default=None)
    reply_count: Mapped[int | None] = mapped_column(Integer, default=None)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    attempt_id: Mapped[str | None] = mapped_column(String(32), default=None)
