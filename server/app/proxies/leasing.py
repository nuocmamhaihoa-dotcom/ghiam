"""Cho máy PC (agent) thuê proxy có thời hạn: thuê, gia hạn, trả kèm kết quả sử dụng."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, or_, select, true, update

from app.config import Settings
from app.crypto import SecretBox
from app.db import Database
from app.errors import GoneError, NotFoundError
from app.models import LeaseOutcome, Proxy, ProxyHealth, ProxyKind, ProxyLease, RotationState
from app.proxies import queries
from app.proxies.credentials import ProxyCredentials, resolve_credentials
from app.proxies.rotation import new_session_id
from app.proxies.schemas import (
    LeasedProxyOut,
    LeaseIn,
    LeaseOut,
    PlaywrightProxyOut,
    ReleaseIn,
    ReleaseOut,
    RenewOut,
)
from app.proxies.service import ROTATABLE_MODES
from app.timeutil import utcnow

LEASE_CANDIDATES = 5
RETRY_AFTER_SEC = 10
MAX_BACKOFF_EXPONENT = 20


@asynccontextmanager
async def serialized(db: Database) -> AsyncIterator[None]:
    """SQLite không có SELECT ... FOR UPDATE: tuần tự hoá các thao tác thuê/trả trong tiến trình."""
    if db.is_sqlite:
        async with db.lease_lock:
            yield
    else:
        yield


def _available_conditions(now: datetime, data: LeaseIn) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        Proxy.enabled == true(),
        Proxy.health == ProxyHealth.ALIVE,
        Proxy.rotation_state == RotationState.IDLE,
        or_(Proxy.quarantined_until.is_(None), Proxy.quarantined_until <= now),
        queries.active_lease_count_subquery(now) < Proxy.max_concurrency,
    ]
    if data.pool:
        conditions.append(Proxy.pool == data.pool)
    if data.kind is not None:
        conditions.append(Proxy.kind == data.kind)
    if data.exclude_ids:
        conditions.append(Proxy.id.not_in(data.exclude_ids))
    return conditions


def _leased_proxy_out(proxy: Proxy, credentials: ProxyCredentials) -> LeasedProxyOut:
    return LeasedProxyOut(
        id=proxy.id,
        kind=ProxyKind(proxy.kind),
        protocol=credentials.protocol,
        host=credentials.host,
        port=credentials.port,
        username=credentials.username,
        password=credentials.password,
        url=credentials.url,
        pool=proxy.pool,
        exit_ip=proxy.exit_ip,
        country=proxy.country,
        playwright=PlaywrightProxyOut(
            server=credentials.server, username=credentials.username, password=credentials.password
        ),
    )


async def _unavailable_message(db: Database, data: LeaseIn, now: datetime) -> str:
    async with db.sessionmaker() as session:
        conditions: list[ColumnElement[bool]] = [Proxy.enabled == true(), Proxy.health == ProxyHealth.ALIVE]
        if data.pool:
            conditions.append(Proxy.pool == data.pool)
        if data.kind is not None:
            conditions.append(Proxy.kind == data.kind)
        alive = await session.scalar(select(func.count(Proxy.id)).where(*conditions))
    scope = f" trong pool '{data.pool}'" if data.pool else ""
    if not alive:
        return f"Chưa có proxy nào đang sống{scope}"
    return f"Tất cả {alive} proxy sống{scope} đang bận, đang đổi IP hoặc đang bị cách ly"


async def acquire_lease(db: Database, box: SecretBox, settings: Settings, data: LeaseIn) -> LeaseOut:
    now = utcnow()
    ttl = min(data.ttl_sec or settings.proxy_lease_default_ttl_sec, settings.proxy_lease_max_ttl_sec)
    async with serialized(db), db.sessionmaker() as session:
        statement = (
            select(Proxy)
            .where(*_available_conditions(now, data))
            .order_by(Proxy.last_used_at.asc().nulls_first(), Proxy.id.asc())
            .limit(LEASE_CANDIDATES)
        )
        if not db.is_sqlite:
            statement = statement.with_for_update(skip_locked=True, of=Proxy)
        chosen: Proxy | None = None
        for candidate in await session.scalars(statement):
            # Postgres: đếm lại sau khi đã khoá dòng để không vượt max_concurrency khi nhiều máy thuê cùng lúc.
            if (
                db.is_sqlite
                or await queries.count_active_leases(session, candidate.id, now) < candidate.max_concurrency
            ):
                chosen = candidate
                break
        if chosen is None:
            await session.rollback()
            return LeaseOut(
                lease_id=None,
                expires_at=None,
                proxy=None,
                retry_after_sec=RETRY_AFTER_SEC,
                message=await _unavailable_message(db, data, now),
            )
        if chosen.uses_session and not chosen.session_id:
            chosen.session_id = new_session_id()
        chosen.last_used_at = now
        lease = ProxyLease(
            id=secrets.token_hex(16),
            proxy_id=chosen.id,
            worker_id=data.worker_id,
            job_ref=data.job_ref,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        session.add(lease)
        credentials = resolve_credentials(chosen, box)
        await session.commit()
    return LeaseOut(
        lease_id=lease.id,
        expires_at=lease.expires_at,
        proxy=_leased_proxy_out(chosen, credentials),
        retry_after_sec=None,
    )


async def renew_lease(db: Database, settings: Settings, lease_id: str, ttl_sec: int | None) -> RenewOut:
    now = utcnow()
    ttl = min(ttl_sec or settings.proxy_lease_default_ttl_sec, settings.proxy_lease_max_ttl_sec)
    async with serialized(db), db.sessionmaker() as session:
        lease = await session.get(ProxyLease, lease_id, with_for_update=not db.is_sqlite)
        if lease is None:
            raise NotFoundError("Không tìm thấy lượt thuê proxy")
        if lease.released_at is not None or lease.expires_at <= now:
            raise GoneError("Lượt thuê proxy đã kết thúc, hãy thuê proxy mới")
        lease.expires_at = now + timedelta(seconds=ttl)
        await session.commit()
        return RenewOut(lease_id=lease.id, expires_at=lease.expires_at)


def _should_rotate(proxy: Proxy, data: ReleaseIn) -> bool:
    if proxy.kind != ProxyKind.ROTATING or proxy.rotation_mode not in ROTATABLE_MODES:
        return False
    return data.request_rotation or (data.outcome == "blocked" and proxy.rotate_on_block)


def _record_outcome(proxy: Proxy, outcome: str, *, will_rotate: bool, now: datetime, settings: Settings) -> None:
    if outcome == LeaseOutcome.OK:
        proxy.success_count += 1
        proxy.consecutive_failures = 0
        return
    proxy.failure_count += 1
    proxy.consecutive_failures += 1
    over = proxy.consecutive_failures - settings.proxy_failure_threshold
    if over < 0 or will_rotate:
        return
    seconds = min(
        settings.proxy_quarantine_base_sec * 2 ** min(over, MAX_BACKOFF_EXPONENT), settings.proxy_quarantine_max_sec
    )
    if seconds > 0:
        proxy.quarantined_until = now + timedelta(seconds=seconds)


async def release_lease(db: Database, settings: Settings, lease_id: str, data: ReleaseIn) -> ReleaseOut:
    now = utcnow()
    async with serialized(db), db.sessionmaker() as session:
        lease = await session.get(ProxyLease, lease_id, with_for_update=not db.is_sqlite)
        if lease is None:
            raise NotFoundError("Không tìm thấy lượt thuê proxy")
        if lease.released_at is not None and lease.outcome != LeaseOutcome.EXPIRED:
            return ReleaseOut(released=False, rotation_scheduled=False, quarantined_until=None)
        proxy = await session.get(Proxy, lease.proxy_id, with_for_update=not db.is_sqlite)
        lease.released_at = now
        lease.outcome = data.outcome
        lease.detail = data.detail
        rotation_scheduled = False
        quarantined_until: datetime | None = None
        if proxy is not None:
            will_rotate = _should_rotate(proxy, data)
            _record_outcome(proxy, data.outcome, will_rotate=will_rotate, now=now, settings=settings)
            if will_rotate:
                if proxy.rotation_state == RotationState.IDLE:
                    proxy.rotation_state = RotationState.PENDING
                    proxy.rotation_requested_at = now
                rotation_scheduled = True
            if proxy.quarantined_until is not None and proxy.quarantined_until > now:
                quarantined_until = proxy.quarantined_until
        await session.commit()
    return ReleaseOut(released=True, rotation_scheduled=rotation_scheduled, quarantined_until=quarantined_until)


async def reap_expired_leases(db: Database, now: datetime) -> int:
    async with serialized(db), db.sessionmaker() as session:
        result = await session.execute(
            update(ProxyLease)
            .where(ProxyLease.released_at.is_(None), ProxyLease.expires_at <= now)
            .values(released_at=now, outcome=LeaseOutcome.EXPIRED)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return queries.rowcount(result)


async def purge_old_leases(db: Database, before: datetime) -> int:
    async with serialized(db), db.sessionmaker() as session:
        result = await session.execute(
            delete(ProxyLease)
            .where(ProxyLease.released_at.is_not(None), ProxyLease.released_at < before)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return queries.rowcount(result)
