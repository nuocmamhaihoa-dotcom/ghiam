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
    EXPIRED = "expired"


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
