from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        options: dict[str, Any] = {"echo": echo}
        if url.startswith("sqlite"):
            options["connect_args"] = {"timeout": 30}
        else:
            options.update(pool_size=20, max_overflow=30, pool_pre_ping=True)
        self.engine: AsyncEngine = create_async_engine(url, **options)
        if self.is_sqlite:
            event.listen(self.engine.sync_engine, "connect", _apply_sqlite_pragmas)
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(self.engine, expire_on_commit=False)
        # SQLite không có SELECT ... FOR UPDATE SKIP LOCKED: tuần tự hoá các thao tác cấp phát proxy.
        self.lease_lock = asyncio.Lock()

    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    async def dispose(self) -> None:
        await self.engine.dispose()
