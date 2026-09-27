from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from itertools import islice
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, Result, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models import Proxy, ProxyLease

CHUNK_SIZE = 500


def chunked[T](items: Iterable[T], size: int = CHUNK_SIZE) -> Iterator[list[T]]:
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


def rowcount(result: Result[Any]) -> int:
    return int(cast("CursorResult[Any]", result).rowcount)


def active_lease_condition(now: datetime) -> ColumnElement[bool]:
    return and_(ProxyLease.released_at.is_(None), ProxyLease.expires_at > now)


def has_active_lease(now: datetime) -> ColumnElement[bool]:
    return exists().where(ProxyLease.proxy_id == Proxy.id, active_lease_condition(now))


def active_lease_count_subquery(now: datetime) -> Any:
    return (
        select(func.count(ProxyLease.id))
        .where(ProxyLease.proxy_id == Proxy.id, active_lease_condition(now))
        .correlate(Proxy)
        .scalar_subquery()
    )


async def count_active_leases(session: AsyncSession, proxy_id: int, now: datetime) -> int:
    count = await session.scalar(
        select(func.count(ProxyLease.id)).where(ProxyLease.proxy_id == proxy_id, active_lease_condition(now))
    )
    return int(count or 0)


async def active_leases_by_proxy(session: AsyncSession, ids: Sequence[int], now: datetime) -> dict[int, int]:
    counts: dict[int, int] = {}
    for chunk in chunked(ids):
        rows = await session.execute(
            select(ProxyLease.proxy_id, func.count(ProxyLease.id))
            .where(ProxyLease.proxy_id.in_(chunk), active_lease_condition(now))
            .group_by(ProxyLease.proxy_id)
        )
        counts.update({int(proxy_id): int(count) for proxy_id, count in rows})
    return counts


async def select_ids(session: AsyncSession, statement: Select[int]) -> list[int]:
    return list(await session.scalars(statement))
