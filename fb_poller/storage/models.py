from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tier(str, enum.Enum):
    hot = "hot"
    warm = "warm"
    cold = "cold"


class PostStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    guest_hidden = "guest_hidden"
    invalid = "invalid"


class ProxyType(str, enum.Enum):
    static = "static"
    mobile_4g = "4g"


class ProxyHealth(str, enum.Enum):
    ok = "ok"
    sick = "sick"


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(16), default=Tier.cold.value, index=True)
    status: Mapped[str] = mapped_column(String(32), default=PostStatus.active.value, index=True)

    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    hot_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    watermark_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recent_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    fail_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_new_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    guest_visible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (UniqueConstraint("comment_id", name="uq_comment_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    author_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PollRun(Base):
    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    tier: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted_new: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Proxy(Base):
    __tablename__ = "proxies"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_proxy_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    proxy_type: Mapped[str] = mapped_column(String(16), default=ProxyType.static.value)
    health: Mapped[str] = mapped_column(String(16), default=ProxyHealth.ok.value, index=True)
    assigned_worker: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_ban_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success_ema: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
