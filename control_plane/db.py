from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-128000;")  # ~128MB page cache
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
            """
        )
        conn.commit()


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
