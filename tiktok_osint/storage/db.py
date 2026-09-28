from __future__ import annotations

from sqlalchemy import create_engine, event
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


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def build_repository(database_url: str) -> Repository:
    from tiktok_osint.storage.repo import Repository

    engine = make_engine(database_url)
    init_db(engine)
    return Repository(session_factory(engine))
