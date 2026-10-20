from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_lock = threading.Lock()
TERMINAL = {"done", "error", "cancelled"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def session(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db(path: Path) -> None:
    with session(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              role TEXT NOT NULL,
              body TEXT NOT NULL,
              state TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              message_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workers (
              hostname TEXT PRIMARY KEY,
              cpu_count INTEGER NOT NULL,
              ram_bytes INTEGER NOT NULL,
              ram_free_bytes INTEGER NOT NULL,
              last_seen TEXT NOT NULL,
              attached INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS studies (
              message_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            );
            """
        )


def create_session(path: Path) -> dict:
    item = {"id": new_id(), "created_at": now()}
    with session(path) as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at) VALUES (?, ?)",
            (item["id"], item["created_at"]),
        )
    return item


def list_sessions(path: Path) -> list[dict]:
    with session(path) as conn:
        rows = conn.execute("SELECT id, created_at FROM sessions ORDER BY created_at DESC, id DESC").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            first = conn.execute(
                "SELECT body FROM messages WHERE session_id = ? ORDER BY created_at, id LIMIT 1",
                (item["id"],),
            ).fetchone()
            title = "Đoạn chat mới"
            if first and str(first["body"]).strip():
                title = " ".join(str(first["body"]).split())[:48]
            item["title"] = title
            items.append(item)
    return items


def get_session(path: Path, session_id: str) -> dict | None:
    with session(path) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def add_message(path: Path, session_id: str, body: str, state: str = "queued") -> dict:
    item = {
        "id": new_id(),
        "session_id": session_id,
        "role": "user",
        "body": body,
        "state": state,
        "created_at": now(),
    }
    with session(path) as conn:
        conn.execute(
            """
            INSERT INTO messages (id, session_id, role, body, state, created_at)
            VALUES (:id, :session_id, :role, :body, :state, :created_at)
            """,
            item,
        )
    return item


def get_message(path: Path, message_id: str) -> dict | None:
    with session(path) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def set_message_state(path: Path, message_id: str, state: str) -> bool:
    with session(path) as conn:
        row = conn.execute("SELECT state FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None or row["state"] in TERMINAL:
            return False
        conn.execute("UPDATE messages SET state = ? WHERE id = ?", (state, message_id))
    return True


def add_event(path: Path, message_id: str, kind: str, payload: str) -> None:
    payload = payload[:65536]
    with session(path) as conn:
        conn.execute(
            "INSERT INTO events (id, message_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id(), message_id, kind, payload, now()),
        )


def list_messages(path: Path, session_id: str) -> list[dict]:
    with session(path) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            events = conn.execute(
                "SELECT kind, payload, created_at FROM events WHERE message_id = ? ORDER BY created_at, id",
                (item["id"],),
            ).fetchall()
            item["events"] = [dict(event) for event in events]
            items.append(item)
    return items


def unstudied_messages(path: Path, limit: int = 2) -> list[dict]:
    with session(path) as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.session_id, m.body
            FROM messages m
            LEFT JOIN studies s ON s.message_id = m.id
            WHERE m.state = 'done' AND m.role = 'user' AND s.message_id IS NULL
            ORDER BY m.created_at, m.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            events = conn.execute(
                "SELECT payload FROM events WHERE message_id = ? AND kind = 'text' ORDER BY created_at, id",
                (item["id"],),
            ).fetchall()
            item["answer"] = "\n".join(event["payload"] for event in events if event["payload"])[:1200]
            items.append(item)
    return items


def mark_studied(path: Path, message_id: str) -> None:
    with session(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO studies (message_id, created_at) VALUES (?, ?)",
            (message_id, now()),
        )


def prior_dialogue(path: Path, session_id: str, before_id: str, limit: int = 8) -> list[dict]:
    dialogue: list[dict] = []
    for item in list_messages(path, session_id):
        if item["id"] == before_id:
            break
        dialogue.append({"role": "user", "content": str(item["body"])[:2000]})
        texts = [event["payload"] for event in item["events"] if event["kind"] == "text" and event["payload"]]
        if texts:
            dialogue.append({"role": "assistant", "content": "\n".join(texts)[:2000]})
    if limit > 0:
        dialogue = dialogue[-limit * 2 :]
    return dialogue


def has_live_chat(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with session(path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM messages
                WHERE role = 'user' AND state IN ('queued', 'running')
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def oldest_queued(path: Path) -> dict | None:
    with session(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM messages
            WHERE state = 'queued' AND role = 'user'
            ORDER BY created_at, id
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def upsert_worker(
    path: Path,
    hostname: str,
    cpu_count: int,
    ram_bytes: int,
    ram_free_bytes: int,
    attached: bool,
) -> None:
    seen = now()
    with session(path) as conn:
        conn.execute(
            """
            INSERT INTO workers (hostname, cpu_count, ram_bytes, ram_free_bytes, last_seen, attached)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(hostname) DO UPDATE SET
              cpu_count = excluded.cpu_count,
              ram_bytes = excluded.ram_bytes,
              ram_free_bytes = excluded.ram_free_bytes,
              last_seen = excluded.last_seen,
              attached = excluded.attached
            """,
            (hostname, cpu_count, ram_bytes, ram_free_bytes, seen, 1 if attached else 0),
        )


def set_attached(path: Path, hostname: str, attached: bool) -> None:
    with session(path) as conn:
        conn.execute(
            "UPDATE workers SET attached = ?, last_seen = ? WHERE hostname = ?",
            (1 if attached else 0, now(), hostname),
        )


def detach_all(path: Path) -> None:
    with session(path) as conn:
        conn.execute("UPDATE workers SET attached = 0")


def list_workers(path: Path) -> list[dict]:
    with session(path) as conn:
        rows = conn.execute("SELECT * FROM workers ORDER BY hostname").fetchall()
    moment = datetime.now(timezone.utc)
    views = []
    for row in rows:
        item = dict(row)
        seen = datetime.fromisoformat(item["last_seen"])
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = (moment - seen).total_seconds()
        online = item["attached"] == 1 and age <= 35
        item["status"] = "online" if online else "offline"
        views.append(item)
    return views
