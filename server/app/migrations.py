from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _upgrade(connection: Connection, revision: str) -> None:
    command.upgrade(alembic_config(connection), revision)


async def upgrade_database(engine: AsyncEngine, revision: str = "head") -> None:
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, revision)
