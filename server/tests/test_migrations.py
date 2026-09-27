from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.migrations import alembic_config, upgrade_database
from app.models import Base
from tests.conftest import BOTH_BACKENDS


def _schema_diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


def _tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _downgrade(connection: Connection) -> None:
    command.downgrade(alembic_config(connection), "base")


async def _run[T](engine: AsyncEngine, function: Callable[[Connection], T]) -> T:
    async with engine.begin() as connection:
        return await connection.run_sync(function)


@BOTH_BACKENDS
async def test_migrations_create_exactly_the_model_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        await upgrade_database(engine)
        assert await _run(engine, _schema_diff) == []
        assert await _run(engine, _tables) == {"alembic_version", "proxies", "proxy_leases"}

        await upgrade_database(engine)
        await _run(engine, _downgrade)
        assert await _run(engine, _tables) == {"alembic_version"}

        await upgrade_database(engine)
        assert await _run(engine, _schema_diff) == []
    finally:
        await engine.dispose()
