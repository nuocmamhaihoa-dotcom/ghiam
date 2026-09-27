"""Nghiệp vụ kho proxy: xem trước/nhập hàng loạt, danh sách, sửa, thao tác hàng loạt, xuất file."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Delete, Update, and_, delete, func, not_, or_, select, true, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.crypto import SecretBox, SecretDecryptionError
from app.errors import AppError, ConflictError, NotFoundError, PayloadTooLargeError
from app.models import Proxy, ProxyHealth, ProxyKind, ProxyLease, ProxyProtocol, RotationMode, RotationState
from app.proxies import queries
from app.proxies.credentials import build_credentials
from app.proxies.parser import (
    DEFAULT_MAX_CONCURRENCY,
    EndpointKey,
    ImportDefaults,
    ParsedLine,
    ParsedProxy,
    ProxyParseError,
    display_endpoint,
    endpoint_key,
    has_session_placeholder,
    host_for_url,
    mask_secret_url,
    normalize_host,
    parse_port,
    parse_proxy_text,
    validate_rotation_url,
)
from app.proxies.rotation import new_session_id
from app.proxies.schemas import (
    BulkAction,
    DuplicatePolicy,
    ExportFormat,
    ImportIn,
    ImportOut,
    PoolCount,
    PreviewLineOut,
    PreviewOut,
    PreviewStatus,
    PreviewSummary,
    ProxyDetailOut,
    ProxyFilterIn,
    ProxyListOut,
    ProxyListQuery,
    ProxyOut,
    ProxySort,
    ProxyStatsOut,
    ProxyUpdateIn,
)
from app.timeutil import utcnow

PREVIEW_LINE_LIMIT = 2000
IMPORT_ERROR_LIMIT = 1000
RAW_LINE_PREVIEW_LIMIT = 200
UNDECRYPTABLE_URL = "(không giải mã được, hãy nhập lại link)"
ROTATABLE_MODES = frozenset({RotationMode.URL, RotationMode.SESSION})

_CREDENTIAL_FORBIDDEN_RE = re.compile(r"[\s|]")
_COLON_EXPORT_UNSAFE_RE = re.compile(r"[\s|@]")

SORTS: dict[ProxySort, tuple[Any, ...]] = {
    "-id": (Proxy.id.desc(),),
    "id": (Proxy.id.asc(),),
    "latency": (Proxy.latency_ms.asc().nulls_last(), Proxy.id.desc()),
    "-latency": (Proxy.latency_ms.desc().nulls_last(), Proxy.id.desc()),
    "last_checked": (Proxy.last_checked_at.asc().nulls_first(), Proxy.id.desc()),
    "-last_checked": (Proxy.last_checked_at.desc().nulls_last(), Proxy.id.desc()),
    "last_used": (Proxy.last_used_at.desc().nulls_last(), Proxy.id.desc()),
    "host": (Proxy.host.asc(), Proxy.port.asc(), Proxy.id.asc()),
}

_COPIED_FIELDS = (
    "id",
    "kind",
    "protocol",
    "host",
    "port",
    "username",
    "pool",
    "note",
    "enabled",
    "max_concurrency",
    "rotation_method",
    "rotation_interval_sec",
    "rotation_cooldown_sec",
    "rotate_on_block",
    "rotation_state",
    "last_rotated_at",
    "last_rotation_attempt_at",
    "last_rotation_ok",
    "last_rotation_message",
    "rotation_count",
    "health",
    "check_in_progress",
    "last_checked_at",
    "last_check_error",
    "latency_ms",
    "exit_ip",
    "country",
    "isp",
    "success_count",
    "failure_count",
    "consecutive_failures",
    "quarantined_until",
    "last_used_at",
    "created_at",
    "updated_at",
)


# ---------------------------------------------------------------------------
# Hiển thị
# ---------------------------------------------------------------------------


def cooldown_remaining(proxy: Proxy, now: datetime) -> int:
    if proxy.last_rotation_attempt_at is None or proxy.rotation_cooldown_sec <= 0:
        return 0
    elapsed = (now - proxy.last_rotation_attempt_at).total_seconds()
    return max(0, math.ceil(proxy.rotation_cooldown_sec - elapsed))


def _decrypt_url(box: SecretBox, token: str | None) -> str | None:
    try:
        return box.decrypt(token)
    except SecretDecryptionError:
        return UNDECRYPTABLE_URL


def _proxy_fields(proxy: Proxy, *, active_leases: int, rotation_url: str | None, now: datetime) -> dict[str, Any]:
    has_password = proxy.password_enc is not None
    data = {name: getattr(proxy, name) for name in _COPIED_FIELDS}
    data.update(
        has_password=has_password,
        display=display_endpoint(proxy.protocol, proxy.host, proxy.port, proxy.username, has_password=has_password),
        active_leases=active_leases,
        rotation_mode=proxy.rotation_mode,
        rotation_url=(
            mask_secret_url(rotation_url) if rotation_url and rotation_url != UNDECRYPTABLE_URL else rotation_url
        ),
        cooldown_remaining_sec=cooldown_remaining(proxy, now),
    )
    return data


def to_proxy_out(proxy: Proxy, *, active_leases: int, box: SecretBox, now: datetime) -> ProxyOut:
    rotation_url = _decrypt_url(box, proxy.rotation_url_enc)
    return ProxyOut.model_validate(
        _proxy_fields(proxy, active_leases=active_leases, rotation_url=rotation_url, now=now)
    )


def to_proxy_detail(proxy: Proxy, *, active_leases: int, box: SecretBox, now: datetime) -> ProxyDetailOut:
    rotation_url = _decrypt_url(box, proxy.rotation_url_enc)
    data = _proxy_fields(proxy, active_leases=active_leases, rotation_url=rotation_url, now=now)
    return ProxyDetailOut.model_validate({**data, "rotation_url_full": rotation_url})


async def proxy_out(session: AsyncSession, proxy: Proxy, box: SecretBox) -> ProxyOut:
    now = utcnow()
    active = await queries.count_active_leases(session, proxy.id, now)
    return to_proxy_out(proxy, active_leases=active, box=box, now=now)


async def proxy_detail(session: AsyncSession, proxy: Proxy, box: SecretBox) -> ProxyDetailOut:
    now = utcnow()
    active = await queries.count_active_leases(session, proxy.id, now)
    return to_proxy_detail(proxy, active_leases=active, box=box, now=now)


async def get_proxy(session: AsyncSession, proxy_id: int, *, refresh: bool = False) -> Proxy:
    proxy = await session.get(Proxy, proxy_id, populate_existing=refresh)
    if proxy is None:
        raise NotFoundError(f"Không tìm thấy proxy #{proxy_id}")
    return proxy


# ---------------------------------------------------------------------------
# Nhập hàng loạt
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedLine:
    parsed: ParsedLine
    status: PreviewStatus
    duplicate_of_line: int | None = None
    existing: Proxy | None = None


@dataclass(slots=True)
class ImportPlan:
    lines: list[PlannedLine]

    def summary(self) -> PreviewSummary:
        summary = PreviewSummary(total=len(self.lines))
        for item in self.lines:
            setattr(summary, item.status, getattr(summary, item.status) + 1)
            proxy = item.parsed.proxy
            if proxy is not None and item.parsed.warnings:
                summary.with_warnings += 1
            if proxy is not None and item.status in ("new", "update"):
                if proxy.kind == ProxyKind.STATIC:
                    summary.static += 1
                else:
                    summary.rotating += 1
        return summary


@dataclass(slots=True)
class ImportResult:
    out: ImportOut
    check_ids: list[int]


def count_import_lines(text: str) -> int:
    count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff").strip()
        if line and not line.startswith(("#", "//")):
            count += 1
    return count


async def _load_existing(session: AsyncSession, keys: set[EndpointKey]) -> dict[EndpointKey, Proxy]:
    found: dict[EndpointKey, Proxy] = {}
    pairs = sorted({(key[1], key[2]) for key in keys})
    for chunk in queries.chunked(pairs):
        rows = await session.scalars(select(Proxy).where(tuple_(Proxy.host, Proxy.port).in_(chunk)))
        for proxy in rows:
            key = endpoint_key(proxy.protocol, proxy.host, proxy.port, proxy.username)
            if key in keys:
                found[key] = proxy
    return found


async def plan_import(
    session: AsyncSession,
    text: str,
    defaults: ImportDefaults,
    on_duplicate: DuplicatePolicy,
    *,
    max_lines: int,
) -> ImportPlan:
    if count_import_lines(text) > max_lines:
        raise PayloadTooLargeError(f"Mỗi lần chỉ nhập tối đa {max_lines} dòng, hãy chia nhỏ danh sách")
    parsed_lines = await asyncio.to_thread(parse_proxy_text, text, defaults)
    existing = await _load_existing(
        session, {line.proxy.endpoint_key for line in parsed_lines if line.proxy is not None}
    )
    first_seen: dict[EndpointKey, int] = {}
    planned: list[PlannedLine] = []
    for line in parsed_lines:
        if line.proxy is None:
            planned.append(PlannedLine(line, "invalid"))
            continue
        key = line.proxy.endpoint_key
        if key in first_seen:
            planned.append(PlannedLine(line, "duplicate", duplicate_of_line=first_seen[key]))
            continue
        first_seen[key] = line.line_no
        match = existing.get(key)
        if match is None:
            planned.append(PlannedLine(line, "new"))
        else:
            planned.append(PlannedLine(line, "update" if on_duplicate == "update" else "duplicate", existing=match))
    return ImportPlan(planned)


def _clip_raw(raw: str) -> str:
    return raw if len(raw) <= RAW_LINE_PREVIEW_LIMIT else f"{raw[:RAW_LINE_PREVIEW_LIMIT]}…"


def _preview_line(item: PlannedLine) -> PreviewLineOut:
    parsed = item.parsed
    proxy = parsed.proxy
    existing_id = item.existing.id if item.existing is not None else None
    if proxy is None:
        return PreviewLineOut(
            line_no=parsed.line_no,
            status=item.status,
            display=_clip_raw(parsed.raw),
            error=parsed.error,
            warnings=list(parsed.warnings),
        )
    return PreviewLineOut(
        line_no=parsed.line_no,
        status=item.status,
        display=display_endpoint(
            proxy.protocol, proxy.host, proxy.port, proxy.username, has_password=proxy.password is not None
        ),
        kind=proxy.kind,
        protocol=proxy.protocol,
        pool=proxy.pool,
        rotation_mode=proxy.rotation_mode,
        rotation_url=mask_secret_url(proxy.rotation_url) if proxy.rotation_url else None,
        max_concurrency=proxy.max_concurrency,
        warnings=list(parsed.warnings),
        duplicate_of_line=item.duplicate_of_line,
        existing_id=existing_id,
    )


def _preview_priority(item: PlannedLine) -> int:
    if item.status == "invalid":
        return 0
    if item.parsed.warnings:
        return 1
    return 2 if item.status in ("duplicate", "update") else 3


def build_preview(plan: ImportPlan) -> PreviewOut:
    ranked = sorted(plan.lines, key=lambda item: (_preview_priority(item), item.parsed.line_no))
    chosen = sorted(ranked[:PREVIEW_LINE_LIMIT], key=lambda item: item.parsed.line_no)
    return PreviewOut(
        summary=plan.summary(),
        lines=[_preview_line(item) for item in chosen],
        truncated=len(plan.lines) > PREVIEW_LINE_LIMIT,
    )


def _reset_health(proxy: Proxy) -> None:
    proxy.health = ProxyHealth.UNCHECKED
    proxy.last_check_error = None
    proxy.latency_ms = None
    proxy.consecutive_failures = 0
    proxy.quarantined_until = None


def _new_proxy(parsed: ParsedProxy, box: SecretBox) -> Proxy:
    return Proxy(
        kind=parsed.kind.value,
        protocol=parsed.protocol.value,
        host=parsed.host,
        port=parsed.port,
        username=parsed.username,
        password_enc=box.encrypt(parsed.password),
        uses_session=parsed.uses_session,
        session_id=new_session_id() if parsed.uses_session else None,
        pool=parsed.pool,
        max_concurrency=parsed.max_concurrency,
        rotation_url_enc=box.encrypt(parsed.rotation_url),
        rotation_method=parsed.rotation_method,
        rotation_interval_sec=parsed.rotation_interval_sec,
        rotation_cooldown_sec=parsed.rotation_cooldown_sec,
        rotate_on_block=parsed.rotate_on_block,
    )


def _apply_parsed(proxy: Proxy, parsed: ParsedProxy, box: SecretBox) -> None:
    try:
        credentials_changed = box.decrypt(proxy.password_enc) != parsed.password
    except SecretDecryptionError:
        credentials_changed = True
    if credentials_changed:
        proxy.password_enc = box.encrypt(parsed.password)
        _reset_health(proxy)
    proxy.kind = parsed.kind.value
    proxy.uses_session = parsed.uses_session
    if not parsed.uses_session:
        proxy.session_id = None
    elif proxy.session_id is None:
        proxy.session_id = new_session_id()
    proxy.pool = parsed.pool
    proxy.max_concurrency = parsed.max_concurrency
    proxy.rotation_url_enc = box.encrypt(parsed.rotation_url)
    proxy.rotation_method = parsed.rotation_method
    proxy.rotation_interval_sec = parsed.rotation_interval_sec
    proxy.rotation_cooldown_sec = parsed.rotation_cooldown_sec
    proxy.rotate_on_block = parsed.rotate_on_block


async def preview_import(
    session: AsyncSession, text: str, defaults: ImportDefaults, on_duplicate: DuplicatePolicy, *, max_lines: int
) -> PreviewOut:
    plan = await plan_import(session, text, defaults, on_duplicate, max_lines=max_lines)
    return build_preview(plan)


async def import_proxies(session: AsyncSession, data: ImportIn, box: SecretBox, *, max_lines: int) -> ImportResult:
    plan = await plan_import(session, data.text, data.defaults.to_defaults(), data.on_duplicate, max_lines=max_lines)
    created: list[Proxy] = []
    updated: list[Proxy] = []
    for item in plan.lines:
        parsed = item.parsed.proxy
        if parsed is None:
            continue
        if item.status == "new":
            proxy = _new_proxy(parsed, box)
            session.add(proxy)
            created.append(proxy)
        elif item.status == "update" and item.existing is not None:
            _apply_parsed(item.existing, parsed, box)
            updated.append(item.existing)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Một số proxy vừa được thêm bởi thao tác khác, hãy nhập lại") from exc
    summary = plan.summary()
    out = ImportOut(
        created=len(created),
        updated=len(updated),
        skipped=summary.duplicate,
        invalid=summary.invalid,
        total=summary.total,
        check_scheduled=0,
        errors=[_preview_line(item) for item in plan.lines if item.status == "invalid"][:IMPORT_ERROR_LIMIT],
    )
    return ImportResult(out=out, check_ids=[proxy.id for proxy in created + updated])


# ---------------------------------------------------------------------------
# Danh sách, lọc, thống kê
# ---------------------------------------------------------------------------


def _hostport_term(term: str) -> tuple[str, int] | None:
    host_part, separator, port_part = term.rpartition(":")
    if not separator:
        return None
    host = normalize_host(host_part)
    port = parse_port(port_part)
    if host is None or port is None:
        return None
    return host[0], port


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_condition(term: str) -> ColumnElement[bool]:
    hostport = _hostport_term(term)
    if hostport is not None:
        return and_(Proxy.host == hostport[0], Proxy.port == hostport[1])
    pattern = _like_pattern(term)
    columns = (Proxy.host, Proxy.username, Proxy.exit_ip, Proxy.note, Proxy.pool, Proxy.isp)
    conditions: list[ColumnElement[bool]] = [column.ilike(pattern, escape="\\") for column in columns]
    if term.isdigit() and len(term) <= 9:
        number = int(term)
        conditions += [Proxy.id == number, Proxy.port == number]
    return or_(*conditions)


def quarantined_condition(now: datetime) -> ColumnElement[bool]:
    return and_(Proxy.quarantined_until.is_not(None), Proxy.quarantined_until > now)


def apply_filter[SelectT: Select[Any]](statement: SelectT, filters: ProxyFilterIn, now: datetime) -> SelectT:
    conditions: list[ColumnElement[bool]] = []
    if filters.kind is not None:
        conditions.append(Proxy.kind == filters.kind)
    if filters.health is not None:
        conditions.append(Proxy.health == filters.health)
    if filters.pool:
        conditions.append(Proxy.pool == filters.pool)
    if filters.enabled is not None:
        conditions.append(Proxy.enabled == filters.enabled)
    if filters.rotation_state is not None:
        conditions.append(Proxy.rotation_state == filters.rotation_state)
    if filters.leased is not None:
        leased = queries.has_active_lease(now)
        conditions.append(leased if filters.leased else not_(leased))
    if filters.quarantined is not None:
        quarantined = quarantined_condition(now)
        conditions.append(quarantined if filters.quarantined else not_(quarantined))
    if filters.q and filters.q.strip():
        conditions.append(_search_condition(filters.q.strip()))
    return statement.where(*conditions) if conditions else statement


async def list_proxies(session: AsyncSession, query: ProxyListQuery, box: SecretBox) -> ProxyListOut:
    now = utcnow()
    total = await session.scalar(apply_filter(select(func.count(Proxy.id)), query, now))
    rows = list(
        await session.scalars(
            apply_filter(select(Proxy), query, now)
            .order_by(*SORTS[query.sort])
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    )
    counts = await queries.active_leases_by_proxy(session, [proxy.id for proxy in rows], now)
    return ProxyListOut(
        items=[to_proxy_out(proxy, active_leases=counts.get(proxy.id, 0), box=box, now=now) for proxy in rows],
        total=int(total or 0),
        page=query.page,
        page_size=query.page_size,
    )


async def proxy_stats(session: AsyncSession) -> ProxyStatsOut:
    now = utcnow()
    counted = func.count(Proxy.id)
    row = (
        await session.execute(
            select(
                counted,
                counted.filter(Proxy.enabled == true()),
                counted.filter(Proxy.health == ProxyHealth.ALIVE),
                counted.filter(Proxy.health == ProxyHealth.DEAD),
                counted.filter(Proxy.health == ProxyHealth.UNCHECKED),
                counted.filter(Proxy.kind == ProxyKind.STATIC),
                counted.filter(Proxy.kind == ProxyKind.ROTATING),
                counted.filter(Proxy.rotation_state == RotationState.ROTATING),
                counted.filter(Proxy.rotation_state == RotationState.PENDING),
                counted.filter(quarantined_condition(now)),
            )
        )
    ).one()
    leased_proxies, active_leases = (
        await session.execute(
            select(func.count(func.distinct(ProxyLease.proxy_id)), func.count(ProxyLease.id)).where(
                queries.active_lease_condition(now)
            )
        )
    ).one()
    pools = await session.execute(select(Proxy.pool, func.count(Proxy.id)).group_by(Proxy.pool).order_by(Proxy.pool))
    return ProxyStatsOut(
        total=row[0],
        enabled=row[1],
        alive=row[2],
        dead=row[3],
        unchecked=row[4],
        static=row[5],
        rotating=row[6],
        rotating_now=row[7],
        rotation_pending=row[8],
        quarantined=row[9],
        leased_proxies=leased_proxies,
        active_leases=active_leases,
        pools=[PoolCount(pool=pool, total=total) for pool, total in pools],
    )


# ---------------------------------------------------------------------------
# Sửa, xoá, thao tác hàng loạt
# ---------------------------------------------------------------------------


def _check_credential(value: str, label: str, *, allow_colon: bool) -> None:
    if _CREDENTIAL_FORBIDDEN_RE.search(value):
        raise AppError(f"{label} không được chứa dấu cách hoặc ký tự |")
    if not allow_colon and ":" in value:
        raise AppError(f"{label} không được chứa dấu ':'")


async def update_proxy(session: AsyncSession, proxy_id: int, data: ProxyUpdateIn, box: SecretBox) -> Proxy:
    proxy = await get_proxy(session, proxy_id)
    fields = data.model_fields_set

    username = proxy.username
    if "username" in fields and data.username is not None:
        username = data.username.strip()
        _check_credential(username, "Tên đăng nhập", allow_colon=False)
    password_changed = "password" in fields
    password = (data.password or None) if password_changed else box.decrypt(proxy.password_enc)
    if password_changed and password is not None:
        _check_credential(password, "Mật khẩu", allow_colon=True)
    if password is not None and not username:
        raise AppError("Có mật khẩu nhưng thiếu tên đăng nhập")
    if username != proxy.username:
        conflict = await session.scalar(
            select(Proxy.id).where(
                Proxy.protocol == proxy.protocol,
                Proxy.host == proxy.host,
                Proxy.port == proxy.port,
                Proxy.username == username,
                Proxy.id != proxy.id,
            )
        )
        if conflict is not None:
            raise ConflictError(f"Đã có proxy #{conflict} cùng host, cổng và tên đăng nhập")

    rotation_url_enc = proxy.rotation_url_enc
    if "rotation_url" in fields:
        rotation_url = (data.rotation_url or "").strip() or None
        if rotation_url is not None:
            try:
                rotation_url = validate_rotation_url(rotation_url, [])
            except ProxyParseError as exc:
                raise AppError(str(exc)) from exc
        rotation_url_enc = box.encrypt(rotation_url)

    credentials_changed = username != proxy.username or password_changed
    proxy.username = username
    if password_changed:
        proxy.password_enc = box.encrypt(password)
    proxy.rotation_url_enc = rotation_url_enc
    if data.kind is not None:
        proxy.kind = data.kind.value
    if data.pool is not None:
        proxy.pool = data.pool
    if "note" in fields:
        proxy.note = (data.note or "").strip() or None
    if data.enabled is not None:
        proxy.enabled = data.enabled
    if data.max_concurrency is not None:
        proxy.max_concurrency = data.max_concurrency
    if data.rotation_method is not None:
        proxy.rotation_method = data.rotation_method
    if data.rotation_interval_sec is not None:
        proxy.rotation_interval_sec = data.rotation_interval_sec
    if data.rotation_cooldown_sec is not None:
        proxy.rotation_cooldown_sec = data.rotation_cooldown_sec
    if data.rotate_on_block is not None:
        proxy.rotate_on_block = data.rotate_on_block

    proxy.uses_session = has_session_placeholder(username, password)
    if not proxy.uses_session:
        proxy.session_id = None
    elif proxy.session_id is None:
        proxy.session_id = new_session_id()
    if credentials_changed:
        _reset_health(proxy)
    if proxy.rotation_state == RotationState.PENDING and proxy.rotation_mode not in ROTATABLE_MODES:
        proxy.rotation_state = RotationState.IDLE
        proxy.rotation_requested_at = None
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Đã có proxy cùng host, cổng và tên đăng nhập") from exc
    return proxy


async def delete_proxy(session: AsyncSession, proxy_id: int) -> None:
    result = await session.execute(delete(Proxy).where(Proxy.id == proxy_id))
    if queries.rowcount(result) == 0:
        raise NotFoundError(f"Không tìm thấy proxy #{proxy_id}")
    await session.commit()


async def resolve_bulk_ids(session: AsyncSession, ids: list[int] | None, filters: ProxyFilterIn | None) -> list[int]:
    if ids is not None:
        found: list[int] = []
        for chunk in queries.chunked(sorted(set(ids))):
            found += await queries.select_ids(session, select(Proxy.id).where(Proxy.id.in_(chunk)))
        return sorted(found)
    if filters is not None:
        statement = apply_filter(select(Proxy.id), filters, utcnow()).order_by(Proxy.id)
        return await queries.select_ids(session, statement)
    raise AppError("Hãy chọn proxy hoặc dùng bộ lọc")


async def apply_bulk(session: AsyncSession, action: BulkAction, ids: list[int], pool: str | None) -> int:
    if action == "set_pool" and not pool:
        raise AppError("Hãy nhập tên pool mới")
    affected = 0
    for chunk in queries.chunked(ids):
        statement: Delete | Update
        if action == "delete":
            statement = delete(Proxy).where(Proxy.id.in_(chunk))
        elif action in ("enable", "disable"):
            enabled = action == "enable"
            statement = update(Proxy).where(Proxy.id.in_(chunk), Proxy.enabled != enabled).values(enabled=enabled)
        elif action == "set_pool":
            statement = update(Proxy).where(Proxy.id.in_(chunk), Proxy.pool != pool).values(pool=pool)
        elif action == "reset_stats":
            statement = (
                update(Proxy)
                .where(Proxy.id.in_(chunk))
                .values(success_count=0, failure_count=0, consecutive_failures=0, quarantined_until=None)
            )
        else:
            raise AppError(f"Thao tác '{action}' không xử lý ở đây")
        result = await session.execute(statement.execution_options(synchronize_session=False))
        affected += queries.rowcount(result)
    await session.commit()
    return affected


# ---------------------------------------------------------------------------
# Xuất file
# ---------------------------------------------------------------------------


def _export_options(proxy: Proxy, box: SecretBox, *, include_protocol: bool) -> list[str]:
    kind = ProxyKind(proxy.kind)
    options: list[str] = []
    if kind == ProxyKind.ROTATING:
        rotation_url = box.decrypt(proxy.rotation_url_enc)
        if rotation_url:
            options.append(rotation_url)
    options.append("type=4g" if kind == ProxyKind.ROTATING else "type=static")
    if include_protocol:
        options.append(f"protocol={proxy.protocol}")
    if proxy.pool != ImportDefaults().pool:
        options.append(f"pool={proxy.pool}")
    if proxy.max_concurrency != DEFAULT_MAX_CONCURRENCY[kind]:
        options.append(f"concurrency={proxy.max_concurrency}")
    if kind == ProxyKind.ROTATING:
        if proxy.rotation_interval_sec:
            options.append(f"interval={proxy.rotation_interval_sec}")
        if proxy.rotation_cooldown_sec != ImportDefaults().rotation_cooldown_sec:
            options.append(f"cooldown={proxy.rotation_cooldown_sec}")
        if proxy.rotation_method != "GET":
            options.append(f"method={proxy.rotation_method}")
    return options


def export_line(proxy: Proxy, box: SecretBox, *, fmt: ExportFormat, with_options: bool) -> str:
    password = box.decrypt(proxy.password_enc)
    colon = (
        fmt == "colon"
        and (not proxy.username or password is not None)
        and ":" not in proxy.username
        and not _COLON_EXPORT_UNSAFE_RE.search(f"{proxy.username}{password or ''}")
    )
    if colon:
        endpoint = f"{host_for_url(proxy.host)}:{proxy.port}"
        if proxy.username:
            endpoint += f":{proxy.username}:{password}"
    else:
        endpoint = build_credentials(proxy.protocol, proxy.host, proxy.port, proxy.username, password, None).url
    parts = [endpoint]
    if with_options:
        parts += _export_options(proxy, box, include_protocol=colon and proxy.protocol != ProxyProtocol.HTTP)
    return "|".join(parts)


async def export_proxies(
    session: AsyncSession, filters: ProxyFilterIn, box: SecretBox, *, fmt: ExportFormat, with_options: bool
) -> str:
    statement = apply_filter(select(Proxy), filters, utcnow()).order_by(Proxy.id)
    lines = [export_line(proxy, box, fmt=fmt, with_options=with_options) for proxy in await session.scalars(statement)]
    return "".join(f"{line}\n" for line in lines)
