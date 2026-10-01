from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import ProxyKind
from app.proxies.schemas import LeasedProxyOut

AuthorModeIn = Literal["full", "name", "anon"]
SortIn = Literal["newest", "relevant"]
AttemptOutcomeIn = Literal["done", "not_available", "blocked", "failed", "cancelled"]
ProxyOutcomeIn = Literal["ok", "blocked", "failed", "cancelled"]


class JobOptions(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    max_comments: int = Field(default=200, ge=1, le=5000)
    include_replies: bool = True
    max_replies_per_comment: int = Field(default=20, ge=0, le=200)
    sort: SortIn = "newest"
    proxy_pool: str | None = Field(default=None, max_length=64)
    proxy_kind: ProxyKind | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)
    time_budget_sec: int = Field(default=180, ge=30, le=900)
    max_parallel: int = Field(default=2, ge=1, le=50)
    author_mode: AuthorModeIn = "full"
    priority: int = Field(default=0, ge=0, le=100)


class PreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)


class CreateJobIn(JobOptions):
    text: str = Field(min_length=1, max_length=2_000_000)


class PermalinkOut(BaseModel):
    line: int
    raw: str
    status: str
    url: str | None
    platform: str | None
    message: str | None


class PreviewOut(BaseModel):
    total: int
    new: int
    duplicate: int
    invalid: int
    unsupported: int
    lines: list[PermalinkOut]


class JobOut(BaseModel):
    id: int
    name: str
    status: str
    priority: int
    max_comments: int
    include_replies: bool
    max_replies_per_comment: int
    sort: str
    proxy_pool: str | None
    proxy_kind: str | None
    max_attempts: int
    time_budget_sec: int
    max_parallel: int
    author_mode: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    posts: int
    pending: int
    running: int
    done: int
    not_available: int
    failed: int
    cancelled: int
    comments: int


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int


class PostOut(BaseModel):
    id: int
    url: str
    platform: str
    status: str
    attempts: int
    not_before: datetime | None
    last_error: str | None
    comments_count: int
    comments_reported: int | None
    complete: bool
    stop_reason: str | None
    worker_id: str | None
    proxy_id: int | None
    started_at: datetime | None
    finished_at: datetime | None


class PostListOut(BaseModel):
    items: list[PostOut]
    total: int


class CommentIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=128)
    parent_external_id: str | None = Field(default=None, max_length=128)
    text: str = Field(min_length=1, max_length=8000)
    author: str = Field(min_length=1, max_length=300)
    author_id: str | None = Field(default=None, max_length=128)
    author_url: str | None = Field(default=None, max_length=500)
    time: datetime | None = None
    time_raw: str | None = Field(default=None, max_length=80)
    likes: int | None = Field(default=None, ge=0, le=2_000_000_000)
    likes_raw: str | None = Field(default=None, max_length=32)
    reply_count: int | None = Field(default=None, ge=0, le=1_000_000)


class CommentOut(BaseModel):
    id: int
    post_id: int
    post_url: str
    external_id: str
    parent_external_id: str | None
    text: str
    author: str
    author_id: str | None
    author_url: str | None
    time: datetime | None
    time_raw: str | None
    likes: int | None
    likes_raw: str | None
    reply_count: int | None
    first_seen_at: datetime


class CommentListOut(BaseModel):
    items: list[CommentOut]
    next_after_id: int | None


class ClaimOut(BaseModel):
    attempt_id: str | None
    expires_at: datetime | None = None
    retry_after_sec: int | None = None
    message: str | None = None
    post_id: int | None = None
    url: str | None = None
    platform: str | None = None
    max_comments: int | None = None
    include_replies: bool | None = None
    max_replies_per_comment: int | None = None
    time_budget_sec: int | None = None
    author_mode: str | None = None
    lease_id: str | None = None
    proxy: LeasedProxyOut | None = None


class ProgressIn(BaseModel):
    seq: int = Field(ge=1, le=100_000)
    comments: list[CommentIn] = Field(max_length=100)


class ProgressOut(BaseModel):
    stored: int
    duplicate: bool
    expires_at: datetime


class CompleteIn(BaseModel):
    outcome: AttemptOutcomeIn
    detail: str | None = Field(default=None, max_length=500)
    proxy_outcome: ProxyOutcomeIn
    comments_reported: int | None = Field(default=None, ge=0)
    complete: bool = False
    stop_reason: str | None = Field(default=None, max_length=32)
    pages: int = Field(default=0, ge=0, le=1000)
    bytes_transferred: int = Field(default=0, ge=0)


class CompleteOut(BaseModel):
    post_status: str
    released: bool
