from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from control_plane import scan_vault

# Một người là một kết quả. Kho dừng nhận người mới khi đủ số này.
PEOPLE_CAPACITY = 50_000_000
people_write_lock = threading.RLock()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-128000;")  # ~128MB page cache
    conn.execute("PRAGMA mmap_size=268435456;")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
              machine_id TEXT PRIMARY KEY,
              hostname TEXT,
              payload_json TEXT NOT NULL,
              registered_at TEXT,
              last_seen_at TEXT,
              status TEXT
            );
            CREATE TABLE IF NOT EXISTS comments (
              comment_id TEXT PRIMARY KEY,
              machine_id TEXT NOT NULL,
              post_url TEXT,
              post_id TEXT,
              text TEXT,
              author_name TEXT,
              author_id TEXT,
              created_time TEXT,
              first_seen_at TEXT,
              synced_at TEXT NOT NULL,
              raw_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_comments_machine ON comments(machine_id);
            CREATE INDEX IF NOT EXISTS idx_comments_synced ON comments(synced_at);
            CREATE TABLE IF NOT EXISTS transfer_stats (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              machine_id TEXT,
              kind TEXT,
              bytes INTEGER,
              ms INTEGER,
              at TEXT
            );
            CREATE TABLE IF NOT EXISTS proxies (
              endpoint TEXT PRIMARY KEY,
              proxy_type TEXT NOT NULL DEFAULT 'static',
              status TEXT NOT NULL DEFAULT 'unknown',
              latency_ms INTEGER,
              exit_ip TEXT,
              error TEXT,
              last_checked_at TEXT,
              ok_count INTEGER NOT NULL DEFAULT 0,
              fail_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_proxies_status ON proxies(status);
            CREATE TABLE IF NOT EXISTS operator_actions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              actor TEXT NOT NULL,
              source TEXT NOT NULL,
              kind TEXT NOT NULL,
              summary TEXT NOT NULL,
              detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_actions_at ON operator_actions(at);
            CREATE TABLE IF NOT EXISTS input_recordings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              actor TEXT NOT NULL,
              title TEXT NOT NULL,
              event_count INTEGER NOT NULL,
              steps_json TEXT NOT NULL,
              events_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS script_clips (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              name TEXT NOT NULL,
              steps_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS script_scenarios (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              name TEXT NOT NULL,
              parts_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS saved_people (
              name_key TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              contact_name TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_people_ready
              ON saved_people(updated_at DESC, name_key DESC)
              WHERE contact_name != '' AND username != '';
            CREATE INDEX IF NOT EXISTS idx_people_username
              ON saved_people(lower(username));
            CREATE TABLE IF NOT EXISTS people_meta (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              total INTEGER NOT NULL,
              ready INTEGER NOT NULL,
              duplicates INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scan_duplicates (
              name_key TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              contact_name TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_duplicates_recent
              ON scan_duplicates(updated_at DESC, name_key DESC);
            CREATE TABLE IF NOT EXISTS screen_lines (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              line TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_jobs (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              size INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL,
              percent INTEGER NOT NULL DEFAULT 0,
              task TEXT NOT NULL DEFAULT '',
              problems_json TEXT NOT NULL DEFAULT '[]',
              error TEXT NOT NULL DEFAULT '',
              worker TEXT NOT NULL DEFAULT '',
              path TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,
              started_at REAL,
              finished_at REAL,
              saved_people INTEGER NOT NULL DEFAULT 0,
              seen_contacts INTEGER NOT NULL DEFAULT 0,
              seen_accounts INTEGER NOT NULL DEFAULT 0,
              parent_id TEXT NOT NULL DEFAULT '',
              part_label TEXT NOT NULL DEFAULT '',
              part_ids_json TEXT NOT NULL DEFAULT '[]',
              frames_json TEXT NOT NULL DEFAULT '[]',
              upload_id TEXT NOT NULL DEFAULT '',
              rev INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_video_jobs_state_created
              ON video_jobs(state, created_at);
            """
        )
        _ensure_people_extras(conn)
        _ensure_video_counts(conn)
        scan_vault.ensure(conn)
        conn.commit()
    scan_vault.startup(db_path)


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_agent(db_path: Path, machine_id: str, payload: dict[str, Any], status: str) -> None:
    now = payload.get("last_seen_at") or payload.get("ts")
    with session(db_path) as conn:
        row = conn.execute("SELECT registered_at FROM agents WHERE machine_id=?", (machine_id,)).fetchone()
        registered = row["registered_at"] if row else now
        conn.execute(
            """
            INSERT INTO agents(machine_id, hostname, payload_json, registered_at, last_seen_at, status)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(machine_id) DO UPDATE SET
              hostname=excluded.hostname,
              payload_json=excluded.payload_json,
              last_seen_at=excluded.last_seen_at,
              status=excluded.status
            """,
            (
                machine_id,
                payload.get("hostname"),
                json.dumps(payload, ensure_ascii=False),
                registered,
                now,
                status,
            ),
        )


def list_agents(db_path: Path) -> list[dict[str, Any]]:
    with session(db_path) as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY last_seen_at DESC").fetchall()
    out = []
    for r in rows:
        item = json.loads(r["payload_json"] or "{}")
        item.update(
            {
                "machine_id": r["machine_id"],
                "hostname": r["hostname"],
                "registered_at": r["registered_at"],
                "last_seen_at": r["last_seen_at"],
                "status": r["status"],
            }
        )
        out.append(item)
    return out


def upsert_comments(db_path: Path, machine_id: str, comments: list[dict[str, Any]], synced_at: str) -> int:
    inserted = 0
    with session(db_path) as conn:
        for c in comments:
            cid = str(c.get("comment_id") or "")
            if not cid:
                continue
            before = conn.execute("SELECT 1 FROM comments WHERE comment_id=?", (cid,)).fetchone()
            conn.execute(
                """
                INSERT INTO comments(
                  comment_id, machine_id, post_url, post_id, text, author_name, author_id,
                  created_time, first_seen_at, synced_at, raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(comment_id) DO UPDATE SET
                  text=excluded.text,
                  synced_at=excluded.synced_at,
                  raw_json=excluded.raw_json
                """,
                (
                    cid,
                    machine_id,
                    c.get("post_url"),
                    str(c.get("post_id") or ""),
                    c.get("text"),
                    c.get("author_name"),
                    c.get("author_id"),
                    c.get("created_time"),
                    c.get("first_seen_at"),
                    synced_at,
                    json.dumps(c, ensure_ascii=False),
                ),
            )
            if before is None:
                inserted += 1
    return inserted


def record_action(
    db_path: Path,
    *,
    at: str,
    actor: str,
    source: str,
    kind: str,
    summary: str,
    detail: str | None = None,
) -> int:
    with session(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO operator_actions(at, actor, source, kind, summary, detail)
            VALUES(?,?,?,?,?,?)
            """,
            (at, actor[:80], source[:40], kind[:40], summary[:500], (detail[:2000] if detail else None)),
        )
        conn.execute(
            """
            DELETE FROM operator_actions
            WHERE id NOT IN (
              SELECT id FROM operator_actions ORDER BY id DESC LIMIT 5000
            )
            """
        )
        return int(cur.lastrowid)


def list_actions(
    db_path: Path,
    *,
    limit: int = 50,
    q: str = "",
    kind: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    clauses: list[str] = []
    params: list[Any] = []
    needle = q.strip()
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        clauses.append(
            "(summary LIKE ? ESCAPE '\\' OR IFNULL(detail,'') LIKE ? ESCAPE '\\' "
            "OR kind LIKE ? ESCAPE '\\' OR actor LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like])
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with session(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, at, actor, source, kind, summary, detail
            FROM operator_actions
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def save_recording(
    db_path: Path,
    *,
    at: str,
    actor: str,
    title: str,
    steps: list[str],
    events: list[dict[str, Any]],
) -> int:
    with session(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO input_recordings(at, actor, title, event_count, steps_json, events_json)
            VALUES(?,?,?,?,?,?)
            """,
            (
                at,
                actor[:80],
                title[:200],
                len(events),
                json.dumps(steps, ensure_ascii=False),
                json.dumps(events, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            DELETE FROM input_recordings
            WHERE id NOT IN (
              SELECT id FROM input_recordings ORDER BY id DESC LIMIT 100
            )
            """
        )
        return int(cur.lastrowid)


def list_recordings(db_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    with session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, at, actor, title, event_count, steps_json
            FROM input_recordings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["steps"] = json.loads(item.pop("steps_json") or "[]")
        except json.JSONDecodeError:
            item["steps"] = []
            item.pop("steps_json", None)
        items.append(item)
    return items


def get_recording(db_path: Path, recording_id: int) -> dict[str, Any] | None:
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT id, at, actor, title, event_count, steps_json, events_json FROM input_recordings WHERE id=?",
            (recording_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["steps"] = json.loads(item.pop("steps_json") or "[]")
    except json.JSONDecodeError:
        item["steps"] = []
        item.pop("steps_json", None)
    try:
        item["events"] = json.loads(item.pop("events_json") or "[]")
    except json.JSONDecodeError:
        item["events"] = []
        item.pop("events_json", None)
    return item



def record_transfer(db_path: Path, machine_id: str, kind: str, nbytes: int, ms: int, at: str) -> None:
    with session(db_path) as conn:
        conn.execute(
            "INSERT INTO transfer_stats(machine_id, kind, bytes, ms, at) VALUES(?,?,?,?,?)",
            (machine_id, kind, nbytes, ms, at),
        )


def transfer_summary(db_path: Path) -> dict[str, Any]:
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS bytes, COALESCE(AVG(ms),0) AS avg_ms FROM transfer_stats"
        ).fetchone()
        comments = conn.execute("SELECT COUNT(*) AS n FROM comments").fetchone()["n"]
        agents = conn.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"]
    return {
        "agents": agents,
        "comments": comments,
        "transfers": row["n"],
        "bytes_total": row["bytes"],
        "avg_transfer_ms": round(row["avg_ms"], 1),
    }


def list_comments(
    db_path: Path,
    *,
    limit: int = 100,
    offset: int = 0,
    q: str = "",
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    like = f"%{q.strip()}%" if q and q.strip() else None
    with session(db_path) as conn:
        if like:
            rows = conn.execute(
                """
                SELECT comment_id, machine_id, post_url, post_id, text, author_name, author_id,
                       created_time, first_seen_at, synced_at
                FROM comments
                WHERE text LIKE ? OR author_name LIKE ? OR post_url LIKE ? OR comment_id LIKE ?
                ORDER BY synced_at DESC
                LIMIT ? OFFSET ?
                """,
                (like, like, like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT comment_id, machine_id, post_url, post_id, text, author_name, author_id,
                       created_time, first_seen_at, synced_at
                FROM comments
                ORDER BY synced_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def upsert_proxy_endpoints(db_path: Path, endpoints: list[str], proxy_type: str = "static") -> int:
    n = 0
    with session(db_path) as conn:
        for ep in endpoints:
            ep = ep.strip()
            if not ep or ep.startswith("#"):
                continue
            conn.execute(
                """
                INSERT INTO proxies(endpoint, proxy_type, status)
                VALUES(?,?, 'unknown')
                ON CONFLICT(endpoint) DO UPDATE SET proxy_type=excluded.proxy_type
                """,
                (ep, proxy_type),
            )
            n += 1
    return n


def update_proxy_check(
    db_path: Path,
    endpoint: str,
    *,
    status: str,
    latency_ms: int | None,
    exit_ip: str | None,
    error: str | None,
    checked_at: str,
) -> None:
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT ok_count, fail_count FROM proxies WHERE endpoint=?", (endpoint,)
        ).fetchone()
        ok = int(row["ok_count"]) if row else 0
        fail = int(row["fail_count"]) if row else 0
        if status == "live":
            ok += 1
        else:
            fail += 1
        conn.execute(
            """
            INSERT INTO proxies(endpoint, proxy_type, status, latency_ms, exit_ip, error, last_checked_at, ok_count, fail_count)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
              status=excluded.status,
              latency_ms=excluded.latency_ms,
              exit_ip=excluded.exit_ip,
              error=excluded.error,
              last_checked_at=excluded.last_checked_at,
              ok_count=excluded.ok_count,
              fail_count=excluded.fail_count
            """,
            (endpoint, "static", status, latency_ms, exit_ip, error, checked_at, ok, fail),
        )


def list_proxies(
    db_path: Path,
    *,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 2000))
    with session(db_path) as conn:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM proxies WHERE status=?
                ORDER BY last_checked_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proxies ORDER BY status ASC, endpoint ASC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def proxy_summary(db_path: Path) -> dict[str, Any]:
    with session(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM proxies").fetchone()["n"]
        live = conn.execute("SELECT COUNT(*) AS n FROM proxies WHERE status='live'").fetchone()["n"]
        die = conn.execute("SELECT COUNT(*) AS n FROM proxies WHERE status='die'").fetchone()["n"]
        unknown = conn.execute(
            "SELECT COUNT(*) AS n FROM proxies WHERE status='unknown' OR status IS NULL"
        ).fetchone()["n"]
        last = conn.execute(
            "SELECT MAX(last_checked_at) AS t FROM proxies"
        ).fetchone()["t"]
    return {
        "total": total,
        "live": live,
        "die": die,
        "unknown": unknown,
        "last_checked_at": last,
    }


def _person_from_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "nameKey": row["name_key"],
        "name": row["name"],
        "contactName": row["contact_name"],
        "username": row["username"],
    }


def _load_people_map(conn: sqlite3.Connection, keys: list[str]) -> dict[str, sqlite3.Row]:
    found: dict[str, sqlite3.Row] = {}
    unique: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    for start in range(0, len(unique), 400):
        chunk = unique[start : start + 400]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT name_key, name, contact_name, username FROM saved_people WHERE name_key IN ({marks})",
            chunk,
        ).fetchall()
        for row in rows:
            found[row["name_key"]] = row
    return found


def _ensure_people_extras(conn: sqlite3.Connection) -> None:
    """Bảng trùng và cột đếm cho kho đã tạo từ bản trước."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_duplicates (
          name_key TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          contact_name TEXT NOT NULL DEFAULT '',
          username TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_duplicates_recent
          ON scan_duplicates(updated_at DESC, name_key DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_people_username
          ON saved_people(lower(username))
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(people_meta)").fetchall()}
    if columns and "duplicates" not in columns:
        conn.execute("ALTER TABLE people_meta ADD COLUMN duplicates INTEGER NOT NULL DEFAULT 0")


def _ensure_video_counts(conn: sqlite3.Connection) -> None:
    """Số người thấy trong danh bạ và số người có tài khoản, cho sổ video đã tạo từ bản trước."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(video_jobs)").fetchall()}
    if not columns:
        return
    if "seen_contacts" not in columns:
        conn.execute("ALTER TABLE video_jobs ADD COLUMN seen_contacts INTEGER NOT NULL DEFAULT 0")
    if "seen_accounts" not in columns:
        conn.execute("ALTER TABLE video_jobs ADD COLUMN seen_accounts INTEGER NOT NULL DEFAULT 0")


def _ensure_people_meta(conn: sqlite3.Connection) -> tuple[int, int, int]:
    _ensure_people_extras(conn)
    row = conn.execute("SELECT total, ready, duplicates FROM people_meta WHERE id=1").fetchone()
    if row is not None:
        return int(row["total"]), int(row["ready"]), int(row["duplicates"] or 0)
    total = int(conn.execute("SELECT COUNT(*) AS n FROM saved_people").fetchone()["n"])
    ready = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM saved_people WHERE contact_name != '' AND username != ''"
        ).fetchone()["n"]
    )
    duplicates = int(conn.execute("SELECT COUNT(*) AS n FROM scan_duplicates").fetchone()["n"])
    conn.execute(
        "INSERT INTO people_meta(id, total, ready, duplicates) VALUES(1, ?, ?, ?)",
        (total, ready, duplicates),
    )
    return total, ready, duplicates


def people_counts(db_path: Path) -> tuple[int, int]:
    """Tổng dòng trong kho, và số người đủ ba cột."""
    with session(db_path) as conn:
        total, ready, _duplicates = _ensure_people_meta(conn)
        return total, ready


def duplicate_count(db_path: Path) -> int:
    """Số người đã quét lại khi kho đã có dòng đủ ba cột."""
    with session(db_path) as conn:
        _total, _ready, duplicates = _ensure_people_meta(conn)
        return duplicates


def people_by_keys(db_path: Path, keys: list[str]) -> dict[str, dict[str, str]]:
    """Đọc đúng các khóa được hỏi. Không kéo cả kho vào bộ nhớ."""
    with session(db_path) as conn:
        found = _load_people_map(conn, keys)
    return {key: _person_from_row(row) for key, row in found.items()}


def people_by_usernames(db_path: Path, usernames: list[str]) -> dict[str, dict[str, str]]:
    """Người đủ ba cột, khóa là username đã hạ chữ. Không kéo cả kho."""
    unique: list[str] = []
    seen: set[str] = set()
    for username in usernames:
        folded = username.casefold()
        if folded and folded not in seen:
            seen.add(folded)
            unique.append(folded)
    found: dict[str, dict[str, str]] = {}
    if not unique:
        return found
    with session(db_path) as conn:
        for start in range(0, len(unique), 400):
            chunk = unique[start : start + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""
                SELECT name_key, name, contact_name, username
                FROM saved_people
                WHERE lower(username) IN ({marks})
                  AND contact_name != '' AND username != ''
                """,
                chunk,
            ).fetchall()
            for row in rows:
                found[str(row["username"]).casefold()] = _person_from_row(row)
    return found


def _cursor_parts(cursor: str) -> tuple[str, str] | None:
    text = cursor.strip()
    if "\n" not in text:
        return None
    updated_at, name_key = text.split("\n", 1)
    if not updated_at or not name_key:
        return None
    return updated_at, name_key


def list_people_page(
    db_path: Path, *, limit: int = 50, cursor: str = ""
) -> tuple[list[dict[str, str]], str]:
    """Một trang người đủ ba cột, mới nhất trước. Con trỏ lấy trang sau."""
    size = max(1, min(int(limit), 200))
    where = "WHERE contact_name != '' AND username != ''"
    params: list[Any] = []
    parsed = _cursor_parts(cursor[:300])
    if parsed is not None:
        updated_at, name_key = parsed
        where += " AND (updated_at < ? OR (updated_at = ? AND name_key < ?))"
        params.extend([updated_at, updated_at, name_key])
    params.append(size + 1)
    with session(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT name_key, name, contact_name, username, updated_at
            FROM saved_people
            {where}
            ORDER BY updated_at DESC, name_key DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    page = rows[:size]
    next_cursor = ""
    if len(rows) > size and page:
        last = page[-1]
        next_cursor = f"{last['updated_at']}\n{last['name_key']}"
    return [_person_from_row(row) for row in page], next_cursor


def list_duplicates_page(
    db_path: Path, *, limit: int = 50, cursor: str = ""
) -> tuple[list[dict[str, str]], str]:
    """Một trang dữ liệu trùng, lần quét mới nhất trước."""
    size = max(1, min(int(limit), 200))
    where = ""
    params: list[Any] = []
    parsed = _cursor_parts(cursor[:300])
    if parsed is not None:
        updated_at, name_key = parsed
        where = "WHERE updated_at < ? OR (updated_at = ? AND name_key < ?)"
        params.extend([updated_at, updated_at, name_key])
    params.append(size + 1)
    with session(db_path) as conn:
        _ensure_people_extras(conn)
        rows = conn.execute(
            f"""
            SELECT name_key, name, contact_name, username, updated_at
            FROM scan_duplicates
            {where}
            ORDER BY updated_at DESC, name_key DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    page = rows[:size]
    next_cursor = ""
    if len(rows) > size and page:
        last = page[-1]
        next_cursor = f"{last['updated_at']}\n{last['name_key']}"
    return [_person_from_row(row) for row in page], next_cursor


def _load_duplicate_map(conn: sqlite3.Connection, keys: list[str]) -> dict[str, sqlite3.Row]:
    found: dict[str, sqlite3.Row] = {}
    unique: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    for start in range(0, len(unique), 400):
        chunk = unique[start : start + 400]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""
            SELECT name_key, name, contact_name, username
            FROM scan_duplicates WHERE name_key IN ({marks})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            found[row["name_key"]] = row
    return found


def _scan_source(row: dict[str, Any]) -> str:
    return " ".join(str(row.get("source") or "").split())[:80]


def _remember_scan(
    conn: sqlite3.Connection,
    fresh: list[dict[str, Any]],
    *,
    recorded_at: str,
    kind: str,
    name_key: str,
    name: str,
    contact_name: str,
    username: str,
    source: str,
    kept: bool,
    same_content: bool,
) -> None:
    """Ghi sổ vĩnh viễn. Cùng chữ và cùng máy thì không thêm dòng mới."""
    if same_content and scan_vault.already_noted(
        conn,
        kind=kind,
        name_key=name_key,
        name=name,
        contact_name=contact_name,
        username=username,
        source=source,
    ):
        return
    noted = scan_vault.remember(
        conn,
        recorded_at=recorded_at,
        kind=kind,
        name_key=name_key,
        name=name,
        contact_name=contact_name,
        username=username,
        kept=kept,
        source=source,
    )
    if noted is not None:
        fresh.append(noted)


def save_duplicates(db_path: Path, rows: list[dict[str, str]], updated_at: str) -> list[str]:
    """Một dòng cho mỗi người đã có trong kho. Lần quét sau ghi đè dòng trùng đó.

    Mỗi lần nội dung đổi vẫn được thêm vào sổ vĩnh viễn, kể cả khi bảng đang dùng đã đủ chỗ.
    """
    skipped: list[str] = []
    fresh: list[dict[str, Any]] = []
    with people_write_lock:
        with session(db_path) as conn:
            conn.execute("PRAGMA synchronous=FULL;")
            _total, _ready, duplicates = _ensure_people_meta(conn)
            current = _load_duplicate_map(conn, [str(row.get("nameKey") or "") for row in rows])
            for row in rows:
                key = str(row.get("nameKey") or "")
                if not key:
                    continue
                name = row.get("name") or ""
                contact_name = row.get("contactName") or ""
                username = row.get("username") or ""
                source = _scan_source(row)
                old = current.get(key)
                same = bool(
                    old
                    and old["name"] == name
                    and old["contact_name"] == contact_name
                    and old["username"] == username
                )
                if same:
                    _remember_scan(
                        conn,
                        fresh,
                        recorded_at=updated_at,
                        kind="duplicate",
                        name_key=key,
                        name=name,
                        contact_name=contact_name,
                        username=username,
                        source=source,
                        kept=True,
                        same_content=True,
                    )
                    continue
                kept = True
                if old is None:
                    if duplicates >= PEOPLE_CAPACITY:
                        skipped.append(key)
                        kept = False
                    else:
                        duplicates += 1
                if kept:
                    conn.execute(
                        """
                        INSERT INTO scan_duplicates(name_key, name, contact_name, username, updated_at)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(name_key) DO UPDATE SET
                          name=excluded.name,
                          contact_name=excluded.contact_name,
                          username=excluded.username,
                          updated_at=excluded.updated_at
                        """,
                        (key, name, contact_name, username, updated_at),
                    )
                    current[key] = {
                        "name_key": key,
                        "name": name,
                        "contact_name": contact_name,
                        "username": username,
                    }
                _remember_scan(
                    conn,
                    fresh,
                    recorded_at=updated_at,
                    kind="duplicate",
                    name_key=key,
                    name=name,
                    contact_name=contact_name,
                    username=username,
                    source=source,
                    kept=kept,
                    same_content=False,
                )
            conn.execute("UPDATE people_meta SET duplicates=? WHERE id=1", (duplicates,))
        scan_vault.replicate(db_path, fresh)
    return skipped


def save_people(db_path: Path, rows: list[dict[str, str]], updated_at: str) -> list[str]:
    """Ghi các dòng được đưa vào. Trả về khóa mới bị bỏ vì kho đang dùng đã đủ.

    Kho đang dùng vẫn dừng ở PEOPLE_CAPACITY. Sổ vĩnh viễn vẫn giữ dòng bị bỏ.
    """
    skipped: list[str] = []
    fresh: list[dict[str, Any]] = []
    with people_write_lock:
        with session(db_path) as conn:
            conn.execute("PRAGMA synchronous=FULL;")
            total, ready, _duplicates = _ensure_people_meta(conn)
            current = _load_people_map(
                conn, [str(row.get("nameKey") or "") for row in rows]
            )
            for row in rows:
                key = str(row.get("nameKey") or "")
                if not key:
                    continue
                contact_name = row.get("contactName") or ""
                username = row.get("username") or ""
                name = row.get("name") or ""
                source = _scan_source(row)
                old = current.get(key)
                same = bool(
                    old
                    and old["name"] == name
                    and old["contact_name"] == contact_name
                    and old["username"] == username
                )
                if same:
                    _remember_scan(
                        conn,
                        fresh,
                        recorded_at=updated_at,
                        kind="person",
                        name_key=key,
                        name=name,
                        contact_name=contact_name,
                        username=username,
                        source=source,
                        kept=True,
                        same_content=True,
                    )
                    continue
                now_ready = bool(contact_name and username)
                kept = True
                if old is None:
                    if total >= PEOPLE_CAPACITY:
                        skipped.append(key)
                        kept = False
                    else:
                        total += 1
                        if now_ready:
                            ready += 1
                elif kept:
                    was_ready = bool(old["contact_name"] and old["username"])
                    if was_ready != now_ready:
                        ready += 1 if now_ready else -1
                if kept:
                    conn.execute(
                        """
                        INSERT INTO saved_people(name_key, name, contact_name, username, updated_at)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(name_key) DO UPDATE SET
                          name=excluded.name,
                          contact_name=excluded.contact_name,
                          username=excluded.username,
                          updated_at=excluded.updated_at
                        """,
                        (key, name, contact_name, username, updated_at),
                    )
                    current[key] = {
                        "name_key": key,
                        "name": name,
                        "contact_name": contact_name,
                        "username": username,
                    }
                _remember_scan(
                    conn,
                    fresh,
                    recorded_at=updated_at,
                    kind="person",
                    name_key=key,
                    name=name,
                    contact_name=contact_name,
                    username=username,
                    source=source,
                    kept=kept,
                    same_content=False,
                )
            ready = max(0, ready)
            conn.execute(
                """
                INSERT INTO people_meta(id, total, ready) VALUES(1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET total=excluded.total, ready=excluded.ready
                """,
                (total, ready),
            )
        scan_vault.replicate(db_path, fresh)
    return skipped


def append_screen_line(db_path: Path, *, at: str, line: str) -> bool:
    """Store one new on-screen line. The same line as the latest row is skipped."""
    text = " ".join(line.split())[:180]
    if not text:
        return False
    with session(db_path) as conn:
        latest = conn.execute("SELECT line FROM screen_lines ORDER BY id DESC LIMIT 1").fetchone()
        if latest is not None and latest["line"] == text:
            return False
        conn.execute("INSERT INTO screen_lines(at, line) VALUES(?, ?)", (at, text))
        conn.execute(
            """
            DELETE FROM screen_lines
            WHERE id NOT IN (
              SELECT id FROM screen_lines ORDER BY id DESC LIMIT 100
            )
            """
        )
    return True


def list_screen_lines(db_path: Path, *, limit: int = 30) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    with session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, at, line FROM screen_lines ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"id": int(row["id"]), "at": row["at"], "line": row["line"]} for row in reversed(rows)]
