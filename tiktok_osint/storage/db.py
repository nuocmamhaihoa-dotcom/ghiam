from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tiktok_osint.storage.orm import Base


def make_engine(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args, poolclass=NullPool, future=True)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _migrate_sqlite(engine)


def _migrate_sqlite(engine: Engine) -> None:
    """Apply additive compatibility changes for existing VPS SQLite databases."""
    columns = {column["name"] for column in inspect(engine).get_columns("official_sync_sessions")}
    additions = {
        "status": "status VARCHAR(32) NOT NULL DEFAULT 'recording'",
        "checkpoint_json": "checkpoint_json TEXT NOT NULL DEFAULT '{}'",
        "updated_at": "updated_at DATETIME",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE official_sync_sessions ADD COLUMN {definition}"))
        connection.execute(
            text(
                "UPDATE official_sync_sessions "
                "SET updated_at = COALESCE(updated_at, created_at), "
                "status = COALESCE(status, 'recording'), "
                "checkpoint_json = COALESCE(checkpoint_json, '{}')"
            )
        )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def build_repository(database_url: str) -> Repository:
    from tiktok_osint.storage.repo import Repository

    engine = make_engine(database_url)
    init_db(engine)
    return Repository(session_factory(engine))
