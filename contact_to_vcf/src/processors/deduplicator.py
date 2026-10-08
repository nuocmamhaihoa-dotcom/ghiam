"""SQLite index of phones already exported in this job."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class Deduplicator:
    """Remember phones on disk so a multi-million-row job does not keep them in RAM."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA temp_store=MEMORY")
        self.connection.execute("PRAGMA cache_size=-65536")
        self.connection.execute("CREATE TABLE IF NOT EXISTS seen (phone TEXT PRIMARY KEY)")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS job_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)"
        )
        self._transaction = False

    def begin(self) -> None:
        if not self._transaction:
            self.connection.execute("BEGIN")
            self._transaction = True

    def is_duplicate(self, phone: str) -> bool:
        if not self._transaction:
            self.begin()
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO seen(phone) VALUES (?)",
            (phone,),
        )
        return cursor.rowcount == 0

    def save_state(self, payload: str) -> None:
        """Store the resume checkpoint in the same transaction as new phones."""
        if not self._transaction:
            self.begin()
        self.connection.execute(
            "INSERT INTO job_state(id, payload) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
            (payload,),
        )

    def commit(self) -> None:
        if self._transaction:
            self.connection.execute("COMMIT")
            self._transaction = False

    def rollback(self) -> None:
        if self._transaction:
            self.connection.execute("ROLLBACK")
            self._transaction = False

    def close(self) -> None:
        self.rollback()
        self.connection.close()
