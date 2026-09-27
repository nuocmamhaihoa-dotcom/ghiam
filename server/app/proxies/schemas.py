from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.models import ProxyHealth, ProxyKind, ProxyProtocol, RotationMode, RotationState
from app.proxies.parser import ImportDefaults

PoolName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[\w.\-]+$")]
RotationMethod = Literal["GET", "POST"]
DuplicatePolicy = Literal["skip", "update"]
MAX_IMPORT_TEXT_LENGTH = 8_000_000


class ImportDefaultsIn(BaseModel):
    kind: ProxyKind = ProxyKind.STATIC
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    pool: PoolName = "default"
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    rotation_interval_sec: int = Field(default=0, ge=0, le=86_400)
    rotation_cooldown_sec: int = Field(default=60, ge=0, le=86_400)
    rotation_method: RotationMethod = "GET"
    rotate_on_block: bool = True

    def to_defaults(self) -> ImportDefaults:
        return ImportDefaults(
            kind=self.kind,
            protocol=self.protocol,
            pool=self.pool,
            max_concurrency=self.max_concurrency,
            rotation_interval_sec=self.rotation_interval_sec,
            rotation_cooldown_sec=self.rotation_cooldown_sec,
            rotation_method=self.rotation_method,
            rotate_on_block=self.rotate_on_block,
        )


class ImportPreviewIn(BaseModel):
    text: str = Field(max_length=MAX_IMPORT_TEXT_LENGTH)
    defaults: ImportDefaultsIn = Field(default_factory=ImportDefaultsIn)
    on_duplicate: DuplicatePolicy = "skip"


class ImportIn(ImportPreviewIn):
    check_after_import: bool = True


PreviewStatus = Literal["new", "update", "duplicate", "invalid"]


class PreviewLineOut(BaseModel):
    line_no: int
    status: PreviewStatus
    display: str
    kind: ProxyKind | None = None
    protocol: ProxyProtocol | None = None
    pool: str | None = None
    rotation_mode: RotationMode | None = None
    rotation_url: str | None = None
    max_concurrency: int | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    duplicate_of_line: int | None = None
    existing_id: int | None = None


class PreviewSummary(BaseModel):
    total: int = 0
    new: int = 0
    update: int = 0
    duplicate: int = 0
    invalid: int = 0
    with_warnings: int = 0
    static: int = 0
    rotating: int = 0


class PreviewOut(BaseModel):
    summary: PreviewSummary
    lines: list[PreviewLineOut]
    truncated: bool


class ImportOut(BaseModel):
    created: int
    updated: int
    skipped: int
    invalid: int
    total: int
    check_scheduled: int
    errors: list[PreviewLineOut]


class ProxyFilterIn(BaseModel):
    kind: ProxyKind | None = None
    health: ProxyHealth | None = None
    pool: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    rotation_state: RotationState | None = None
    leased: bool | None = None
    quarantined: bool | None = None
    q: str | None = Field(default=None, max_length=200)


ProxySort = Literal["-id", "id", "latency", "-latency", "last_checked", "-last_checked", "last_used", "host"]


class ProxyListQuery(ProxyFilterIn):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)
    sort: ProxySort = "-id"


class ProxyOut(BaseModel):
    id: int
    kind: ProxyKind
    protocol: ProxyProtocol
    host: str
    port: int
    username: str
    has_password: bool
    display: str
    pool: str
    note: str | None
    enabled: bool
    max_concurrency: int
    active_leases: int

    rotation_mode: RotationMode
    rotation_url: str | None
    rotation_method: str
    rotation_interval_sec: int
    rotation_cooldown_sec: int
    rotate_on_block: bool
    rotation_state: RotationState
    cooldown_remaining_sec: int
    last_rotated_at: datetime | None
    last_rotation_attempt_at: datetime | None
    last_rotation_ok: bool | None
    last_rotation_message: str | None
    rotation_count: int

    health: ProxyHealth
    check_in_progress: bool
    last_checked_at: datetime | None
    last_check_error: str | None
    latency_ms: int | None
    exit_ip: str | None
    country: str | None
    isp: str | None

    success_count: int
    failure_count: int
    consecutive_failures: int
    quarantined_until: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProxyListOut(BaseModel):
    items: list[ProxyOut]
    total: int
    page: int
    page_size: int


class PoolCount(BaseModel):
    pool: str
    total: int


class ProxyStatsOut(BaseModel):
    total: int
    enabled: int
    alive: int
    dead: int
    unchecked: int
    static: int
    rotating: int
    leased_proxies: int
    active_leases: int
    rotating_now: int
    rotation_pending: int
    quarantined: int
    pools: list[PoolCount]


class ProxyUpdateIn(BaseModel):
    kind: ProxyKind | None = None
    pool: PoolName | None = None
    note: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    rotation_url: str | None = Field(default=None, max_length=2000)
    rotation_method: RotationMethod | None = None
    rotation_interval_sec: int | None = Field(default=None, ge=0, le=86_400)
    rotation_cooldown_sec: int | None = Field(default=None, ge=0, le=86_400)
    rotate_on_block: bool | None = None


class ProxyDetailOut(ProxyOut):
    rotation_url_full: str | None


BulkAction = Literal["enable", "disable", "delete", "check", "rotate", "set_pool", "reset_stats"]


class BulkIn(BaseModel):
    action: BulkAction
    ids: list[int] | None = Field(default=None, max_length=100_000)
    filter: ProxyFilterIn | None = None
    pool: PoolName | None = None


class BulkOut(BaseModel):
    action: BulkAction
    matched: int
    affected: int
    message: str


class CheckOut(BaseModel):
    ok: bool
    latency_ms: int | None
    exit_ip: str | None
    country: str | None
    isp: str | None
    error: str | None
    proxy: ProxyOut | None


class RotateOut(BaseModel):
    status: Literal["rotated", "failed", "started", "pending", "skipped"]
    message: str
    proxy: ProxyOut | None


ExportFormat = Literal["url", "colon"]


class LeaseIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    pool: str | None = Field(default=None, max_length=64)
    kind: ProxyKind | None = None
    ttl_sec: int | None = Field(default=None, ge=30, le=86_400)
    job_ref: str | None = Field(default=None, max_length=128)
    exclude_ids: list[int] = Field(default_factory=list, max_length=200)


class PlaywrightProxyOut(BaseModel):
    server: str
    username: str | None
    password: str | None


class LeasedProxyOut(BaseModel):
    id: int
    kind: ProxyKind
    protocol: ProxyProtocol
    host: str
    port: int
    username: str | None
    password: str | None
    url: str
    pool: str
    exit_ip: str | None
    country: str | None
    playwright: PlaywrightProxyOut


class LeaseOut(BaseModel):
    lease_id: str | None
    expires_at: datetime | None
    proxy: LeasedProxyOut | None
    retry_after_sec: int | None
    message: str | None = None


class RenewIn(BaseModel):
    ttl_sec: int | None = Field(default=None, ge=30, le=86_400)


class RenewOut(BaseModel):
    lease_id: str
    expires_at: datetime


class ReleaseIn(BaseModel):
    # "cancelled": agent dừng giữa chừng hoặc lỗi phía máy PC, không chấm điểm proxy.
    outcome: Literal["ok", "blocked", "failed", "cancelled"]
    detail: str | None = Field(default=None, max_length=500)
    request_rotation: bool = False


class ReleaseOut(BaseModel):
    released: bool
    rotation_scheduled: bool
    quarantined_until: datetime | None
