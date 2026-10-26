from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


PHONE_IN_CHUNK = 800
PHONE_TEMP_KEYS = 1600
PHONE_KEY_SYNC_ROWS = 200_000
PHONE_KEY_BATCH = 50_000
_PHONE_KEY_STARTED: set[str] = set()
_PHONE_KEY_LOCK = threading.Lock()


def phone_key(value: Any) -> str:
    """Khóa so khớp một SĐT: 0901…, 84901…, +84 901…, 00901…, 901… cùng một khóa."""
    text = str(value or "")
    digits = text if text.isdigit() else "".join(character for character in text if character.isdigit())
    if not (8 <= len(digits) <= 15):
        return digits
    if digits.startswith("0084") and len(digits) >= 13:
        rest = digits[4:]
    elif digits.startswith("084") and len(digits) >= 12:
        rest = digits[3:]
    elif digits.startswith("84") and len(digits) >= 11:
        rest = digits[2:]
    elif digits.startswith("00"):
        trimmed = digits.lstrip("0")
        return trimmed if len(trimmed) == 9 and trimmed[:1] in "35789" else digits
    elif digits.startswith("0") and len(digits) >= 10:
        return digits[1:]
    else:
        return digits
    return rest[1:] if rest.startswith("0") else rest


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=FILE;")
    conn.execute("PRAGMA cache_size=-8000;")
    conn.execute("PRAGMA busy_timeout=8000;")
    return conn


def apply_heavy_pragmas(conn: sqlite3.Connection) -> None:
    """Nạp hàng loạt trên VPS ~2GB. Cache và mmap lớn làm tiến trình bị giết giữa chừng."""
    conn.execute("PRAGMA temp_store=FILE;")
    conn.execute("PRAGMA cache_size=-16384;")
    conn.execute("PRAGMA mmap_size=0;")
    conn.execute("PRAGMA wal_autocheckpoint=1000;")
    conn.execute("PRAGMA busy_timeout=60000;")
    conn.execute("PRAGMA journal_size_limit=67108864;")


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
            CREATE TABLE IF NOT EXISTS customers (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              address TEXT NOT NULL DEFAULT '',
              admin_address TEXT NOT NULL DEFAULT '',
              order_content TEXT NOT NULL DEFAULT '',
              cod_amount TEXT NOT NULL DEFAULT '',
              facebook_uid TEXT NOT NULL DEFAULT '',
              tiktok_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
            CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
            CREATE INDEX IF NOT EXISTS idx_customers_facebook ON customers(facebook_uid);
            CREATE INDEX IF NOT EXISTS idx_customers_tiktok ON customers(tiktok_id);
            CREATE INDEX IF NOT EXISTS idx_customers_created ON customers(created_at);
            CREATE TABLE IF NOT EXISTS kv_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS staff_accounts (
              id TEXT PRIMARY KEY,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              display_name TEXT NOT NULL DEFAULT '',
              role TEXT NOT NULL,
              daily_lookup_limit INTEGER,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS staff_sessions (
              token TEXT PRIMARY KEY,
              account_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_staff_sessions_account ON staff_sessions(account_id);
            CREATE TABLE IF NOT EXISTS lookup_usage (
              account_id TEXT NOT NULL,
              day TEXT NOT NULL,
              phone_key TEXT NOT NULL,
              at TEXT NOT NULL,
              PRIMARY KEY (account_id, day, phone_key)
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL,
              at TEXT NOT NULL,
              ok INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_attempts_user ON login_attempts(username, at);
            """
        )
        _ensure_customer_columns(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_created ON customers(created_at)")
        conn.commit()
        ready = _phone_key_flag(conn)
    if not ready:
        ensure_phone_keys(db_path)


def _ensure_customer_columns(conn: sqlite3.Connection) -> None:
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
    for name in ("admin_address", "order_content", "cod_amount", "phone_key"):
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE customers ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")


def _phone_key_flag(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM kv_meta WHERE key = 'phone_key_ready'").fetchone()
    return bool(row) and str(row[0]) == "1"


def phone_keys_ready(conn: sqlite3.Connection) -> bool:
    try:
        return _phone_key_flag(conn)
    except sqlite3.OperationalError:
        return False


def backfill_phone_keys(db_path: Path, batch: int = PHONE_KEY_BATCH, pause: float = 0.0) -> int:
    """Điền phone_key cho kho cũ theo từng lô rowid, commit từng lô để không khóa kho lâu."""
    conn = connect(db_path)
    changed = 0
    try:
        conn.execute("PRAGMA busy_timeout=60000;")
        conn.create_function("khach_phone_key", 1, phone_key, deterministic=True)
        top = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM customers").fetchone()[0] or 0)
        start = 0
        while start < top:
            cur = conn.execute(
                "UPDATE customers SET phone_key = khach_phone_key(phone) "
                "WHERE rowid > ? AND rowid <= ? AND phone_key != khach_phone_key(phone)",
                (start, start + batch),
            )
            changed += max(0, cur.rowcount or 0)
            conn.commit()
            start += batch
            if pause:
                time.sleep(pause)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_phone_key ON customers(phone_key)")
        conn.execute("INSERT OR REPLACE INTO kv_meta(key, value) VALUES('phone_key_ready', '1')")
        conn.commit()
    finally:
        conn.close()
    return changed


def _backfill_in_background(db_path: Path) -> None:
    try:
        backfill_phone_keys(db_path, pause=0.05)
    except Exception:
        pass
    finally:
        with _PHONE_KEY_LOCK:
            _PHONE_KEY_STARTED.discard(str(db_path))


def ensure_phone_keys(db_path: Path) -> None:
    """Kho nhỏ: điền phone_key ngay. Kho lớn: điền ở luồng nền; trong lúc đó tra theo biến thể số cũ."""
    key = str(db_path)
    with _PHONE_KEY_LOCK:
        if key in _PHONE_KEY_STARTED:
            return
        _PHONE_KEY_STARTED.add(key)
    background = False
    try:
        conn = connect(db_path)
        try:
            if _phone_key_flag(conn):
                return
            rows = int(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] or 0)
        finally:
            conn.close()
        if rows <= PHONE_KEY_SYNC_ROWS:
            backfill_phone_keys(db_path)
            return
        threading.Thread(
            target=_backfill_in_background,
            args=(db_path,),
            name="khach-phone-key",
            daemon=True,
        ).start()
        background = True
    except Exception:
        return
    finally:
        if not background:
            with _PHONE_KEY_LOCK:
                _PHONE_KEY_STARTED.discard(key)


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
            (at, actor[:80], source[:40], kind[:40], summary[:500], (detail[:50000] if detail else None)),
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


def _load_steps(raw: str) -> list[dict[str, Any]]:
    try:
        steps = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return steps if isinstance(steps, list) else []


def _load_parts(raw: str) -> list[dict[str, Any]]:
    try:
        parts = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return parts if isinstance(parts, list) else []


def _scenario_clip_ids(parts: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for part in parts:
        if not isinstance(part, dict):
            continue
        try:
            clip_id = int(part.get("clipId") or 0)
        except (TypeError, ValueError):
            continue
        if clip_id > 0:
            ids.add(clip_id)
    return ids


def save_clip(db_path: Path, *, at: str, name: str, steps: list[dict[str, Any]]) -> int:
    with session(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO script_clips(at, name, steps_json) VALUES(?,?,?)",
            (at, name[:80], json.dumps(steps, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def update_clip(db_path: Path, clip_id: int, *, at: str, name: str, steps: list[dict[str, Any]]) -> bool:
    with session(db_path) as conn:
        cur = conn.execute(
            "UPDATE script_clips SET at=?, name=?, steps_json=? WHERE id=?",
            (at, name[:80], json.dumps(steps, ensure_ascii=False), clip_id),
        )
        return cur.rowcount > 0


def list_clips(db_path: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    with session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, at, name, steps_json FROM script_clips ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        scenarios = conn.execute("SELECT name, parts_json FROM script_scenarios").fetchall()
    used: dict[int, list[str]] = {}
    for scenario in scenarios:
        for clip_id in _scenario_clip_ids(_load_parts(scenario["parts_json"])):
            used.setdefault(clip_id, [])
            if scenario["name"] not in used[clip_id]:
                used[clip_id].append(scenario["name"])
    items: list[dict[str, Any]] = []
    for row in rows:
        steps = _load_steps(row["steps_json"])
        clip_id = int(row["id"])
        names = used.get(clip_id, [])
        items.append(
            {
                "id": clip_id,
                "at": row["at"],
                "name": row["name"],
                "steps": steps,
                "scenarioCount": len(names),
                "scenarioNames": names,
            }
        )
    return items


def get_clip(db_path: Path, clip_id: int) -> dict[str, Any] | None:
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT id, at, name, steps_json FROM script_clips WHERE id=?",
            (clip_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "at": row["at"], "name": row["name"], "steps": _load_steps(row["steps_json"])}


def delete_clip(db_path: Path, clip_id: int) -> str:
    with session(db_path) as conn:
        row = conn.execute("SELECT id FROM script_clips WHERE id=?", (clip_id,)).fetchone()
        if row is None:
            return "missing"
        scenarios = conn.execute("SELECT name, parts_json FROM script_scenarios").fetchall()
        for scenario in scenarios:
            if clip_id in _scenario_clip_ids(_load_parts(scenario["parts_json"])):
                return "used"
        conn.execute("DELETE FROM script_clips WHERE id=?", (clip_id,))
    return "deleted"


def save_scenario(db_path: Path, *, at: str, name: str, parts: list[dict[str, Any]]) -> int:
    with session(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO script_scenarios(at, name, parts_json) VALUES(?,?,?)",
            (at, name[:80], json.dumps(parts, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def update_scenario(db_path: Path, scenario_id: int, *, at: str, name: str, parts: list[dict[str, Any]]) -> bool:
    with session(db_path) as conn:
        cur = conn.execute(
            "UPDATE script_scenarios SET at=?, name=?, parts_json=? WHERE id=?",
            (at, name[:80], json.dumps(parts, ensure_ascii=False), scenario_id),
        )
        return cur.rowcount > 0


def list_scenarios(db_path: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    with session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, at, name, parts_json FROM script_scenarios ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        names = {int(row["id"]): row["name"] for row in conn.execute("SELECT id, name FROM script_clips")}
    items: list[dict[str, Any]] = []
    for row in rows:
        parts = []
        for part in _load_parts(row["parts_json"]):
            if not isinstance(part, dict):
                continue
            clip_id = int(part.get("clipId") or 0)
            parts.append(
                {
                    "clipId": clip_id,
                    "clipName": names.get(clip_id, ""),
                    "fills": part.get("fills") or {},
                }
            )
        items.append({"id": int(row["id"]), "at": row["at"], "name": row["name"], "parts": parts})
    return items


def get_scenario(db_path: Path, scenario_id: int) -> dict[str, Any] | None:
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT id, at, name, parts_json FROM script_scenarios WHERE id=?",
            (scenario_id,),
        ).fetchone()
        if row is None:
            return None
        names = {int(item["id"]): item["name"] for item in conn.execute("SELECT id, name FROM script_clips")}
    parts = []
    for part in _load_parts(row["parts_json"]):
        if not isinstance(part, dict):
            continue
        clip_id = int(part.get("clipId") or 0)
        parts.append(
            {
                "clipId": clip_id,
                "clipName": names.get(clip_id, ""),
                "fills": part.get("fills") or {},
            }
        )
    return {"id": int(row["id"]), "at": row["at"], "name": row["name"], "parts": parts}


def delete_scenario(db_path: Path, scenario_id: int) -> bool:
    with session(db_path) as conn:
        cur = conn.execute("DELETE FROM script_scenarios WHERE id=?", (scenario_id,))
        return cur.rowcount > 0


def clips_by_id(db_path: Path, clip_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    wanted = [clip_id for clip_id in clip_ids if clip_id > 0]
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    with session(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, steps_json FROM script_clips WHERE id IN ({placeholders})",
            wanted,
        ).fetchall()
    return {int(row["id"]): _load_steps(row["steps_json"]) for row in rows}


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


def list_people(db_path: Path) -> list[dict[str, str]]:
    with session(db_path) as conn:
        rows = conn.execute(
            "SELECT name_key, name, contact_name, username FROM saved_people ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {
            "nameKey": row["name_key"],
            "name": row["name"],
            "contactName": row["contact_name"],
            "username": row["username"],
        }
        for row in rows
    ]


_CUSTOMER_SELECT = """
    SELECT id, name, phone, address, admin_address, order_content, cod_amount,
           facebook_uid, tiktok_id, created_at, updated_at
    FROM customers
"""


def customer_from_sql_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": row["id"],
        "name": row["name"],
        "phone": row["phone"],
        "address": row["address"],
        "adminAddress": row["admin_address"],
        "orderContent": row["order_content"],
        "codAmount": row["cod_amount"],
        "facebookUid": row["facebook_uid"],
        "tiktokId": row["tiktok_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


_FILLED_COLUMNS = {
    "name": "name",
    "phone": "phone",
    "address": "address",
    "adminAddress": "admin_address",
    "orderContent": "order_content",
    "codAmount": "cod_amount",
    "facebookUid": "facebook_uid",
    "tiktokId": "tiktok_id",
}


_BROWSE_SELECT = (
    "SELECT rowid AS _rowid, id, name, phone, address, admin_address, order_content, "
    "cod_amount, facebook_uid, tiktok_id, created_at, updated_at FROM customers"
)
_BROWSE_BUDGET = 30_000
_BROWSE_BATCH = 4_000


def _browse_page(
    found: list[sqlite3.Row],
    *,
    limit: int,
    direction: str,
    cursor: int | None,
    reached_end: bool,
) -> tuple[list[dict[str, str]], int | None, int | None, bool]:
    page = found[:limit]
    has_more = len(found) > limit or not reached_end
    if not has_more:
        return [customer_from_sql_row(row) for row in page], None, None, False
    if len(found) > limit:
        mark = int(page[-1]["_rowid"])
    else:
        mark = int(cursor or (page[-1]["_rowid"] if page else 0))
    if not mark:
        return [customer_from_sql_row(row) for row in page], None, None, False
    if direction == "asc":
        return [customer_from_sql_row(row) for row in page], None, mark, True
    return [customer_from_sql_row(row) for row in page], mark, None, True


def _row_has_fields(row: sqlite3.Row, filled: list[str]) -> bool:
    for field in filled:
        column = _FILLED_COLUMNS.get(field)
        if column and not str(row[column] or ""):
            return False
    return True


def _row_matches_query(row: sqlite3.Row, query: str) -> bool:
    needle = " ".join(str(query or "").split())
    if not needle:
        return True
    number = _phone_query(needle)
    if number:
        variants = set(_phone_variants(number) or [number])
        return str(row["phone"] or "") in variants
    folded = needle.casefold()
    return any(folded in str(row[column] or "").casefold() for column in _FILLED_COLUMNS.values())


def _scan_customers(
    conn: sqlite3.Connection,
    *,
    direction: str,
    cursor: int | None,
    limit: int,
    filled: list[str],
    query: str,
    budget: int,
) -> tuple[list[sqlite3.Row], int | None, bool]:
    """Đọc từng cụm theo rowid rồi lọc trong bộ nhớ. Không bắt SQLite quét cả kho để tìm ô có dữ liệu."""
    matches: list[sqlite3.Row] = []
    examined = 0
    last = cursor
    reached_end = False
    compare = "rowid > ?" if direction == "asc" else "rowid < ?"
    order = "ASC" if direction == "asc" else "DESC"
    while examined < budget and len(matches) <= limit:
        batch = min(_BROWSE_BATCH, budget - examined)
        params: list[Any] = []
        where = ""
        if last:
            where = f" WHERE {compare}"
            params.append(int(last))
        params.append(batch)
        rows = conn.execute(
            f"{_BROWSE_SELECT}{where} ORDER BY rowid {order} LIMIT ?",
            params,
        ).fetchall()
        if not rows:
            reached_end = True
            break
        examined += len(rows)
        last = int(rows[-1]["_rowid"])
        for row in rows:
            if not _row_has_fields(row, filled) or not _row_matches_query(row, query):
                continue
            matches.append(row)
            if len(matches) > limit:
                last = int(row["_rowid"])
                break
        if len(matches) > limit or len(rows) < batch:
            reached_end = len(rows) < batch and len(matches) <= limit
            break
    return matches, last, reached_end


def browse_customers(
    db_path: Path,
    *,
    limit: int,
    before: int | None = None,
    after: int | None = None,
    filled: list[str] | None = None,
    query: str = "",
    budget: int = _BROWSE_BUDGET,
) -> tuple[list[dict[str, str]], int | None, int | None, bool]:
    """Lật kho theo rowid. Có lọc thì mỗi lần chỉ đọc một đoạn, không sắp cả bảng."""
    size = max(1, min(int(limit), 2000))
    wanted = [field for field in (filled or []) if field in _FILLED_COLUMNS]
    needle = " ".join(str(query or "").split())
    direction = "asc" if after else "desc"
    cursor = int(after or before or 0) or None
    if not wanted and not needle:
        compare = "rowid > ?" if direction == "asc" else "rowid < ?"
        order = "ASC" if direction == "asc" else "DESC"
        params: list[Any] = []
        where = ""
        if cursor:
            where = f" WHERE {compare}"
            params.append(cursor)
        params.append(size + 1)
        with session(db_path) as conn:
            found = conn.execute(
                f"{_BROWSE_SELECT}{where} ORDER BY rowid {order} LIMIT ?",
                params,
            ).fetchall()
        reached_end = len(found) <= size
        return _browse_page(found, limit=size, direction=direction, cursor=cursor, reached_end=reached_end)
    with session(db_path) as conn:
        found, last, reached_end = _scan_customers(
            conn,
            direction=direction,
            cursor=cursor,
            limit=size,
            filled=wanted,
            query=needle,
            budget=max(size + 1, int(budget)),
        )
        if direction == "desc" and not before and not found and not reached_end:
            found, last, reached_end = _scan_customers(
                conn,
                direction="asc",
                cursor=None,
                limit=size,
                filled=wanted,
                query=needle,
                budget=max(size + 1, int(budget)),
            )
            direction = "asc"
    return _browse_page(found, limit=size, direction=direction, cursor=last, reached_end=reached_end)


def list_customers(db_path: Path, limit: int | None = None) -> list[dict[str, str]]:
    """Có giới hạn thì lấy khách ghi sau cùng theo rowid. Sắp theo updated_at phải đọc cả kho."""
    with session(db_path) as conn:
        if limit is None:
            rows = conn.execute(
                _CUSTOMER_SELECT + " ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                _CUSTOMER_SELECT + " ORDER BY rowid DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
    return [customer_from_sql_row(row) for row in rows]


def iter_customers(db_path: Path, chunk: int = 500) -> Iterator[dict[str, str]]:
    """Đọc kho từng lô. Không tải 1,3 triệu dòng vào RAM cùng lúc."""
    sql = _CUSTOMER_SELECT + " ORDER BY updated_at DESC, created_at DESC"
    size = max(50, int(chunk))
    with session(db_path) as conn:
        cursor = conn.execute(sql)
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                break
            for row in rows:
                yield customer_from_sql_row(row)


def customers_by_phone(conn: sqlite3.Connection, phone: str) -> list[dict[str, str]]:
    if not phone:
        return []
    found = customers_matching_phones(conn, [phone])
    return found.get(phone) or []


def _phone_variants(value: str) -> list[str]:
    raw = "".join(character for character in str(value or "") if character.isdigit())
    digits = raw
    if not (8 <= len(digits) <= 15):
        return []
    if digits.startswith("00") and not digits.startswith("0084"):
        trimmed = digits.lstrip("0")
        if len(trimmed) == 9 and trimmed[:1] in "35789":
            digits = "0" + trimmed
    keys = [digits]
    if raw and raw != digits:
        keys.append(raw)
    if digits.startswith("0084") and len(digits) >= 13:
        rest = digits[4:]
        keys.append(rest if rest.startswith("0") else "0" + rest)
        keys.append(rest[1:] if rest.startswith("0") else rest)
    elif digits.startswith("084") and len(digits) >= 12:
        rest = digits[3:]
        keys.append(rest if rest.startswith("0") else "0" + rest)
        keys.append(rest[1:] if rest.startswith("0") else rest)
    elif digits.startswith("84") and len(digits) >= 11:
        rest = digits[2:]
        keys.append(rest if rest.startswith("0") else "0" + rest)
        keys.append(rest[1:] if rest.startswith("0") else rest)
    elif digits.startswith("0") and len(digits) >= 10:
        rest = digits[1:]
        keys.extend([rest, "84" + rest, "084" + rest, "0084" + rest, "00" + rest, "00" + digits])
    elif len(digits) == 9:
        keys.extend(["0" + digits, "84" + digits])
    seen: set[str] = set()
    unique: list[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def _unique_phone_keys(phones: Iterable[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for phone in phones:
        if not phone:
            continue
        for key in _phone_variants(phone) or [phone]:
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def _fetch_customers_by_keys(conn: sqlite3.Connection, keys: list[str]) -> list[dict[str, str]]:
    if not keys:
        return []
    if len(keys) >= PHONE_TEMP_KEYS:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _khach_phone_q (phone TEXT NOT NULL PRIMARY KEY)")
        conn.execute("DELETE FROM _khach_phone_q")
        conn.executemany("INSERT OR IGNORE INTO _khach_phone_q(phone) VALUES (?)", ((key,) for key in keys))
        rows = conn.execute(
            _CUSTOMER_SELECT
            + " WHERE phone IN (SELECT phone FROM _khach_phone_q) ORDER BY created_at ASC, id ASC"
        ).fetchall()
        return [customer_from_sql_row(row) for row in rows]
    found: list[dict[str, str]] = []
    for start in range(0, len(keys), PHONE_IN_CHUNK):
        chunk = keys[start : start + PHONE_IN_CHUNK]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            _CUSTOMER_SELECT + f" WHERE phone IN ({marks}) ORDER BY created_at ASC, id ASC",
            chunk,
        ).fetchall()
        found.extend(customer_from_sql_row(row) for row in rows)
    return found


def _index_customers_by_phone_keys(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    for item in rows:
        stored = item.get("phone") or ""
        keys = _phone_variants(stored) or ([stored] if stored else [])
        if stored and stored not in keys:
            keys.append(stored)
        for key in keys:
            bucket = index.setdefault(key, [])
            if any(existing.get("id") == item["id"] for existing in bucket):
                continue
            bucket.append(item)
    return index


def _customers_for_phone_from_index(
    index: dict[str, list[dict[str, str]]],
    phone: str,
) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for key in _phone_variants(phone) or [phone]:
        for item in index.get(key) or []:
            customer_id = str(item.get("id") or "")
            if customer_id in seen_ids:
                continue
            seen_ids.add(customer_id)
            matches.append(item)
    return matches


def _fetch_by_phone_key(conn: sqlite3.Connection, keys: list[str]) -> list[sqlite3.Row]:
    select = _CUSTOMER_SELECT.replace("SELECT id,", "SELECT phone_key, id,", 1)
    if len(keys) >= PHONE_TEMP_KEYS:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _khach_key_q (k TEXT NOT NULL PRIMARY KEY)")
        conn.execute("DELETE FROM _khach_key_q")
        conn.executemany("INSERT OR IGNORE INTO _khach_key_q(k) VALUES (?)", ((key,) for key in keys))
        return conn.execute(select + " WHERE phone_key IN (SELECT k FROM _khach_key_q)").fetchall()
    rows: list[sqlite3.Row] = []
    for start in range(0, len(keys), PHONE_IN_CHUNK):
        chunk = keys[start : start + PHONE_IN_CHUNK]
        marks = ",".join("?" * len(chunk))
        rows.extend(conn.execute(select + f" WHERE phone_key IN ({marks})", chunk).fetchall())
    return rows


def _customers_by_phone_key(conn: sqlite3.Connection, wanted: list[str]) -> dict[str, list[dict[str, str]]]:
    key_of = {phone: phone_key(phone) or phone for phone in wanted}
    keys = list(dict.fromkeys(key_of.values()))
    by_key: dict[str, list[dict[str, str]]] = {}
    for row in _fetch_by_phone_key(conn, keys):
        by_key.setdefault(str(row["phone_key"]), []).append(customer_from_sql_row(row))
    for bucket in by_key.values():
        if len(bucket) > 1:
            bucket.sort(key=lambda item: (item.get("createdAt") or "", item.get("id") or ""))
    return {phone: list(by_key.get(key_of[phone]) or []) for phone in wanted}


def customers_matching_phones(
    conn: sqlite3.Connection,
    phones: list[str],
) -> dict[str, list[dict[str, str]]]:
    """Một lần đọc SQLite cho cả lô số. Trả về map số hỏi → khách khớp (cả biến thể 0/84)."""
    wanted: list[str] = []
    seen: set[str] = set()
    for phone in phones:
        if not phone or phone in seen:
            continue
        seen.add(phone)
        wanted.append(phone)
    if not wanted:
        return {}
    if phone_keys_ready(conn):
        return _customers_by_phone_key(conn, wanted)
    found: dict[str, list[dict[str, str]]] = {phone: [] for phone in wanted}
    keys = _unique_phone_keys(wanted)
    if not keys:
        return found
    index = _index_customers_by_phone_keys(_fetch_customers_by_keys(conn, keys))
    for phone in wanted:
        found[phone] = _customers_for_phone_from_index(index, phone)
    return found


def customers_for_phones(db_path: Path, phones: list[str]) -> dict[str, list[dict[str, str]]]:
    """Tìm khách theo list số, không tải cả kho vào RAM."""
    with session(db_path) as conn:
        apply_heavy_pragmas(conn)
        return customers_matching_phones(conn, phones)


@contextmanager
def lookup_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Kết nối đọc cho tra cứu nhiều lô; commit sau mỗi lô để không giữ ảnh chụp WAL quá lâu."""
    conn = connect(db_path)
    try:
        apply_heavy_pragmas(conn)
        yield conn
    finally:
        conn.close()


def _phone_query(needle: str) -> str:
    """Ô tìm chỉ gồm một số điện thoại (có thể có dấu cách, chấm, +84) thì trả về số đó."""
    if not needle or any(not (character.isdigit() or character in " .-+()") for character in needle):
        return ""
    digits = "".join(character for character in needle if character.isdigit())
    return digits if 9 <= len(digits) <= 15 else ""


def search_customers(db_path: Path, query: str, limit: int = 2000) -> tuple[list[dict[str, str]], int]:
    """Tìm khách bằng SQL, không tải cả kho vào RAM. Tìm bằng cả số điện thoại thì dò thẳng chỉ mục."""
    needle = " ".join(str(query or "").split())
    if not needle:
        return [], 0
    limit = max(1, int(limit))
    number = _phone_query(needle)
    if number:
        with session(db_path) as conn:
            if phone_keys_ready(conn):
                rows = customers_matching_phones(conn, [number]).get(number) or []
                if rows:
                    return rows[:limit], len(rows)
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    likes = [f"%{escaped}%"]
    folded = needle.casefold()
    if folded != needle:
        folded_esc = folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        likes.append(f"%{folded_esc}%")
    fields = (
        "name",
        "phone",
        "address",
        "admin_address",
        "order_content",
        "cod_amount",
        "facebook_uid",
        "tiktok_id",
    )
    clauses: list[str] = []
    params: list[Any] = []
    for pattern in likes:
        clauses.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in fields) + ")")
        params.extend([pattern] * len(fields))
    variants = _phone_variants(needle)
    if variants:
        clauses.append("phone IN (" + ",".join("?" * len(variants)) + ")")
        params.extend(variants)
    where = " OR ".join(clauses)
    with session(db_path) as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM customers WHERE {where}", params).fetchone()["n"])
        rows = conn.execute(
            _CUSTOMER_SELECT + f" WHERE {where} ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [customer_from_sql_row(row) for row in rows], total


_CUSTOMERS_TOTAL_KEY = "customers_total"


def _read_customer_total(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM kv_meta WHERE key = ?", (_CUSTOMERS_TOTAL_KEY,)).fetchone()
    if row is None:
        return None
    try:
        return max(0, int(row["value"]))
    except (TypeError, ValueError):
        return None


def _write_customer_total(conn: sqlite3.Connection, total: int) -> None:
    conn.execute(
        """
        INSERT INTO kv_meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (_CUSTOMERS_TOTAL_KEY, str(max(0, int(total)))),
    )


def _bump_customer_total(conn: sqlite3.Connection, delta: int) -> None:
    current = _read_customer_total(conn)
    if current is None or not delta:
        return
    _write_customer_total(conn, current + delta)


def _existing_customer_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        if not chunk:
            continue
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(f"SELECT id FROM customers WHERE id IN ({marks})", chunk).fetchall()
        found.update(str(row["id"]) for row in rows)
    return found


def _customer_total_delta(conn: sqlite3.Connection, dirty_ids: list[str], removed_ids: list[str]) -> int:
    dirty = set(dirty_ids)
    removed = set(removed_ids)
    existing = _existing_customer_ids(conn, list(dirty | removed))
    added = sum(1 for item in dirty if item not in existing)
    gone = sum(1 for item in removed if item in existing and item not in dirty)
    return added - gone


def cached_customer_total(db_path: Path) -> int | None:
    with session(db_path) as conn:
        return _read_customer_total(conn)


def set_customer_total(db_path: Path, total: int) -> None:
    with session(db_path) as conn:
        _write_customer_total(conn, total)


def empty_phone_count(db_path: Path) -> int:
    with session(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM customers WHERE phone = ''").fetchone()
    return int(row["n"] if row else 0)


def page_warehouse_stats(db_path: Path) -> dict[str, int | bool]:
    """Số liệu cho trang kho. Kho lớn dùng tổng đã lưu, không quét lại hàng chục triệu dòng."""
    total = cached_customer_total(db_path)
    if total is None and db_path.stat().st_size < 500_000_000:
        total = customer_count(db_path)
        set_customer_total(db_path, total)
    if total is None:
        return {"total": 0, "uniquePhones": 0, "emptyPhone": 0, "duplicatePhones": 0, "known": False}
    empty = empty_phone_count(db_path)
    unique = max(0, total - empty)
    return {
        "total": total,
        "uniquePhones": unique,
        "emptyPhone": empty,
        "duplicatePhones": max(0, total - unique - empty),
        "known": True,
    }


def write_customers(conn: sqlite3.Connection, rows: list[dict[str, Any]], removed_ids: list[str]) -> None:
    dirty = [row for row in rows if row.get("write", True)]
    dirty_ids = [str(row.get("id") or "") for row in dirty if str(row.get("id") or "")]
    removed = [str(item) for item in removed_ids if str(item or "")]
    if not dirty_ids and not removed:
        return
    delta = _customer_total_delta(conn, dirty_ids, removed)
    if removed:
        conn.executemany("DELETE FROM customers WHERE id=?", [(customer_id,) for customer_id in removed])
    if not dirty:
        _bump_customer_total(conn, delta)
        return
    conn.executemany(
        """
        INSERT INTO customers(
          id, name, phone, phone_key, address, admin_address, order_content, cod_amount,
          facebook_uid, tiktok_id, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          phone=excluded.phone,
          phone_key=excluded.phone_key,
          address=excluded.address,
          admin_address=excluded.admin_address,
          order_content=excluded.order_content,
          cod_amount=excluded.cod_amount,
          facebook_uid=excluded.facebook_uid,
          tiktok_id=excluded.tiktok_id,
          created_at=excluded.created_at,
          updated_at=excluded.updated_at
        """,
        [
            (
                row["id"],
                row.get("name") or "",
                row.get("phone") or "",
                phone_key(row.get("phone") or ""),
                row.get("address") or "",
                row.get("adminAddress") or "",
                row.get("orderContent") or "",
                row.get("codAmount") or "",
                row.get("facebookUid") or "",
                row.get("tiktokId") or "",
                row.get("createdAt") or "",
                row.get("updatedAt") or "",
            )
            for row in dirty
        ],
    )
    _bump_customer_total(conn, delta)


def replace_customers(db_path: Path, rows: list[dict[str, Any]], removed_ids: list[str]) -> None:
    with session(db_path) as conn:
        write_customers(conn, rows, removed_ids)


def delete_customers_without_phone(db_path: Path) -> int:
    """Xóa các hàng không có số điện thoại. Không đụng khách đã có SĐT."""
    with session(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM customers WHERE phone = ''").fetchone()
        count = int(row["n"] or 0)
        if count:
            conn.execute("DELETE FROM customers WHERE phone = ''")
            _bump_customer_total(conn, -count)
    return count


def customer_count(db_path: Path) -> int:
    with session(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()
    return int(row["n"] if row else 0)


def warehouse_stats(db_path: Path) -> dict[str, int]:
    """Đếm kho một lần: tổng hàng, SĐT khác nhau, hàng không có SĐT."""
    with session(db_path) as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN phone = '' THEN 1 ELSE 0 END) AS empty_phone,
              COUNT(DISTINCT CASE WHEN phone != '' THEN phone END) AS unique_phones
            FROM customers
            """
        ).fetchone()
    total = int(row["total"] or 0)
    empty = int(row["empty_phone"] or 0)
    unique = int(row["unique_phones"] or 0)
    return {
        "total": total,
        "uniquePhones": unique,
        "emptyPhone": empty,
        "duplicatePhones": max(0, total - unique - empty),
    }


def customers_created_between(db_path: Path, start: str, end: str) -> int:
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM customers WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        ).fetchone()
    return int(row["n"] if row else 0)


def save_people(db_path: Path, rows: list[dict[str, str]], updated_at: str) -> None:
    with session(db_path) as conn:
        current = {
            row["name_key"]: row
            for row in conn.execute(
                "SELECT name_key, name, contact_name, username FROM saved_people"
            ).fetchall()
        }
        for row in rows:
            old = current.get(row["nameKey"])
            contact_name = row.get("contactName") or ""
            username = row.get("username") or ""
            if (
                old
                and old["name"] == row["name"]
                and old["contact_name"] == contact_name
                and old["username"] == username
            ):
                continue
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
                (
                    row["nameKey"],
                    row["name"],
                    row.get("contactName") or "",
                    row.get("username") or "",
                    updated_at,
                ),
            )
