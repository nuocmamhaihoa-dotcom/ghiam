"""Sổ dữ liệu đã quét: chỉ thêm, không sửa, không xoá.

Ba hướng đủ cùng một nội dung, mỗi hướng một file riêng:
- bảng scan_facts trong cơ sở chính
- cơ sở SQLite riêng (mặc định scan_vault/scan_facts.db)
- nhật ký JSONL đã fsync (mặc định scan_log/scan_facts.jsonl)

Mất một hoặc hai hướng thì lần mở máy dựng lại từ hướng còn. Đặt
CONTROL_SCAN_MIRROR và CONTROL_SCAN_LOG trỏ sang hai thư mục khác thư mục
dữ liệu (hoặc hai đĩa khác) để xoá một chỗ không kéo theo hai chỗ kia.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

_TIME_TEXT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9:.+\-Zz]*)?$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_facts (
  fact_key TEXT PRIMARY KEY,
  recorded_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  name_key TEXT NOT NULL,
  name TEXT NOT NULL,
  contact_name TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  kept INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scan_facts_person
  ON scan_facts(name_key, recorded_at);
CREATE INDEX IF NOT EXISTS idx_scan_facts_kind
  ON scan_facts(kind, kept, name_key);
CREATE TRIGGER IF NOT EXISTS scan_facts_no_delete
BEFORE DELETE ON scan_facts
BEGIN
  SELECT RAISE(ABORT, 'Dữ liệu đã quét không được xoá');
END;
CREATE TRIGGER IF NOT EXISTS scan_facts_no_update
BEFORE UPDATE ON scan_facts
BEGIN
  SELECT RAISE(ABORT, 'Dữ liệu đã quét không được sửa');
END;
"""


def locations(db_path: Path) -> tuple[Path, Path, Path]:
    """Bản SQLite thứ hai, nhật ký JSONL, và dấu kiểm tra độ dài nhật ký."""
    mirror_env = os.environ.get("CONTROL_SCAN_MIRROR", "").strip()
    log_env = os.environ.get("CONTROL_SCAN_LOG", "").strip()
    mirror = Path(mirror_env) if mirror_env else db_path.parent / "scan_vault" / "scan_facts.db"
    log = Path(log_env) if log_env else db_path.parent / "scan_log" / "scan_facts.jsonl"
    return mirror, log, log.with_name(log.name + ".seal")


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(scan_facts)")}
    if "source" not in columns:
        conn.execute("ALTER TABLE scan_facts ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_facts_source ON scan_facts(source, recorded_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_facts_time ON scan_facts(recorded_at, fact_key)"
    )


def remember(
    conn: sqlite3.Connection,
    *,
    recorded_at: str,
    kind: str,
    name_key: str,
    name: str,
    contact_name: str,
    username: str,
    kept: bool,
    source: str = "",
) -> dict[str, Any] | None:
    """Thêm một lần quét vào giao dịch đang mở. Dòng đã có thì bỏ qua."""
    ensure(conn)
    fact = _fact(
        recorded_at=recorded_at,
        kind=kind,
        name_key=name_key,
        name=name,
        contact_name=contact_name,
        username=username,
        kept=kept,
        source=source,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO scan_facts(
          fact_key, recorded_at, kind, name_key, name, contact_name, username, kept, source
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        _tuple(fact),
    )
    changed = conn.execute("SELECT changes()").fetchone()
    if changed is None or int(changed[0]) != 1:
        return None
    return fact


def replicate(db_path: Path, facts: list[dict[str, Any]]) -> None:
    """Ghi các dòng mới ra hai hướng còn lại, rồi bù nếu một hướng đang thiếu."""
    # db.py gọi module này. Khoá lấy lúc chạy để không vòng import lúc nạp file.
    from control_plane.db import people_write_lock

    with people_write_lock:
        if facts:
            _append_copies(db_path, facts)
        if not _aligned(db_path):
            _heal(db_path)
        if not _aligned(db_path):
            raise RuntimeError("Chưa lưu đủ ba hướng của dữ liệu đã quét")


def startup(db_path: Path) -> None:
    """Dựng lại sổ và các dòng người đang thiếu trước khi nhận việc mới."""
    # db.py gọi module này. Khoá lấy lúc chạy để không vòng import lúc nạp file.
    from control_plane.db import people_write_lock

    with people_write_lock:
        if not _aligned(db_path):
            _heal(db_path)
        _restore_working(db_path)
        if not _aligned(db_path):
            raise RuntimeError("Chưa lưu đủ ba hướng của dữ liệu đã quét")


def list_facts(db_path: Path) -> list[dict[str, Any]]:
    with _open(db_path) as conn:
        ensure(conn)
        rows = conn.execute(
            """
            SELECT fact_key, recorded_at, kind, name_key, name, contact_name, username, kept, source
            FROM scan_facts
            ORDER BY recorded_at ASC, fact_key ASC
            """
        ).fetchall()
    return [_public(row) for row in rows]


def already_noted(
    conn: sqlite3.Connection,
    *,
    kind: str,
    name_key: str,
    name: str,
    contact_name: str,
    username: str,
    source: str,
) -> bool:
    """Cùng nội dung và cùng máy đã có trong sổ thì không ghi thêm một dòng."""
    ensure(conn)
    row = conn.execute(
        """
        SELECT 1 FROM scan_facts
        WHERE kind = ? AND name_key = ? AND name = ? AND contact_name = ?
          AND username = ? AND source = ?
        LIMIT 1
        """,
        (kind, name_key, name, contact_name, username, _clean_source(source)),
    ).fetchone()
    return row is not None


def list_scans(
    db_path: Path,
    *,
    limit: int = 50,
    cursor: str = "",
    source: str = "",
    since: str = "",
    until: str = "",
) -> dict[str, Any]:
    """Một trang kết quả đã quét, mới nhất trước. Lọc theo máy và khoảng ngày."""
    size = max(1, min(int(limit), 200))
    phone = _clean_source(source)
    start, end = _time_bounds(since, until)
    filters: list[str] = []
    filter_params: list[Any] = []
    if phone:
        filters.append("source = ?")
        filter_params.append(phone)
    if start:
        filters.append("recorded_at >= ?")
        filter_params.append(start)
    if end:
        filters.append("recorded_at < ?")
        filter_params.append(end)
    parsed = _scan_cursor(cursor[:300])
    clauses = list(filters)
    params = list(filter_params)
    if parsed is not None:
        recorded_at, fact_key = parsed
        clauses.append("(recorded_at < ? OR (recorded_at = ? AND fact_key < ?))")
        params.extend([recorded_at, recorded_at, fact_key])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    count_where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(size + 1)
    with _open(db_path) as conn:
        ensure(conn)
        total = int(
            conn.execute(f"SELECT COUNT(*) AS n FROM scan_facts {count_where}", filter_params).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT fact_key, recorded_at, kind, name_key, name, contact_name, username, kept, source
            FROM scan_facts
            {where}
            ORDER BY recorded_at DESC, fact_key DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        phones = [
            str(row["source"])
            for row in conn.execute(
                """
                SELECT source FROM scan_facts
                WHERE source != ''
                GROUP BY source
                ORDER BY source COLLATE NOCASE
                LIMIT 200
                """
            )
        ]
    page = rows[:size]
    next_cursor = ""
    if len(rows) > size and page:
        last = page[-1]
        next_cursor = f"{last['recorded_at']}\n{last['fact_key']}"
    return {
        "count": total,
        "items": [_scan_item(row) for row in page],
        "cursor": next_cursor,
        "phones": phones,
    }


def iter_scan_csv(db_path: Path) -> Iterator[bytes]:
    """Toàn bộ sổ quét, cũ trước. File CSV có dấu để Excel đọc tiếng Việt."""
    yield "\ufeff".encode("utf-8")
    yield _csv_line(["Thời gian", "Máy iPhone", "Loại", "Tên", "Tên danh bạ", "Tài khoản", "Giữ"])
    conn = _connect(db_path)
    try:
        ensure(conn)
        cursor = conn.execute(
            """
            SELECT recorded_at, kind, name, contact_name, username, kept, source
            FROM scan_facts
            ORDER BY recorded_at ASC, fact_key ASC
            """
        )
        while True:
            batch = cursor.fetchmany(200)
            if not batch:
                break
            chunks: list[bytes] = []
            for row in batch:
                kind = "trùng" if str(row["kind"]) == "duplicate" else "người mới"
                kept = "có" if int(row["kept"]) else "kho đầy"
                phone = str(row["source"] or "") or "Chưa ghi tên máy"
                chunks.append(
                    _csv_line(
                        [
                            str(row["recorded_at"] or ""),
                            phone,
                            kind,
                            str(row["name"] or ""),
                            str(row["contact_name"] or ""),
                            str(row["username"] or ""),
                            kept,
                        ]
                    )
                )
            yield b"".join(chunks)
    finally:
        conn.close()


def _csv_cell(value: str) -> str:
    text = str(value or "")
    if text[:1] in "=+-@\t\r":
        return "'" + text
    return text


def _csv_line(fields: list[str]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow([_csv_cell(item) for item in fields])
    return buffer.getvalue().encode("utf-8")


def _fact(
    *,
    recorded_at: str,
    kind: str,
    name_key: str,
    name: str,
    contact_name: str,
    username: str,
    kept: bool,
    source: str = "",
) -> dict[str, Any]:
    kept_bit = 1 if kept else 0
    phone = _clean_source(source)
    raw = "\n".join(
        [recorded_at, kind, name_key, name, contact_name, username, str(kept_bit), phone]
    )
    return {
        "factKey": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "recordedAt": recorded_at,
        "kind": kind,
        "nameKey": name_key,
        "name": name,
        "contactName": contact_name,
        "username": username,
        "kept": kept_bit,
        "source": phone,
    }


def _tuple(fact: dict[str, Any]) -> tuple[Any, ...]:
    return (
        fact["factKey"],
        fact["recordedAt"],
        fact["kind"],
        fact["nameKey"],
        fact["name"],
        fact["contactName"],
        fact["username"],
        int(fact["kept"]),
        _clean_source(str(fact.get("source") or "")),
    )


def _public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "factKey": row["fact_key"],
        "recordedAt": row["recorded_at"],
        "kind": row["kind"],
        "nameKey": row["name_key"],
        "name": row["name"],
        "contactName": row["contact_name"],
        "username": row["username"],
        "kept": int(row["kept"]),
        "source": str(row["source"] or ""),
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    return conn


@contextmanager
def _open(path: Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(path)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert(conn: sqlite3.Connection, fact: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO scan_facts(
          fact_key, recorded_at, kind, name_key, name, contact_name, username, kept, source
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        _tuple(fact),
    )


def _tail(conn: sqlite3.Connection) -> tuple[int, str]:
    ensure(conn)
    count = int(conn.execute("SELECT COUNT(*) AS n FROM scan_facts").fetchone()["n"])
    if count == 0:
        return 0, ""
    row = conn.execute(
        """
        SELECT fact_key FROM scan_facts
        ORDER BY recorded_at DESC, fact_key DESC
        LIMIT 1
        """
    ).fetchone()
    return count, str(row["fact_key"]) if row else ""


def _aligned(db_path: Path) -> bool:
    mirror, log, seal = locations(db_path)
    with _open(db_path) as conn:
        primary_count, primary_last = _tail(conn)
    if primary_count == 0 and not mirror.exists() and not log.exists():
        return True
    if not seal.is_file() or not mirror.is_file() or not log.is_file():
        return False
    try:
        info = json.loads(seal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if info.get("rows") != primary_count or info.get("last") != primary_last:
        return False
    try:
        if log.stat().st_size != info.get("bytes"):
            return False
    except OSError:
        return False
    with _open(mirror) as conn:
        mirror_count, mirror_last = _tail(conn)
    return mirror_count == primary_count and mirror_last == primary_last


def _append_copies(db_path: Path, facts: list[dict[str, Any]]) -> None:
    mirror, log, _seal = locations(db_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        for fact in facts:
            handle.write(json.dumps(fact, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    with _open(mirror) as conn:
        ensure(conn)
        for fact in facts:
            _insert(conn, fact)
        conn.commit()
    _write_seal(db_path)


def _heal(db_path: Path) -> None:
    mirror, log, _seal = locations(db_path)
    from_log = _read_log(log)
    found: dict[str, dict[str, Any]] = dict(from_log)
    if mirror.is_file():
        with _open(mirror) as conn:
            found.update(_load(conn))
    with _open(db_path) as conn:
        found.update(_load(conn))
        for fact in found.values():
            _insert(conn, fact)
        conn.commit()
    with _open(mirror) as conn:
        ensure(conn)
        for fact in found.values():
            _insert(conn, fact)
        conn.commit()
    missing = [fact for key, fact in found.items() if key not in from_log]
    missing.sort(key=lambda item: (str(item["recordedAt"]), str(item["factKey"])))
    if missing:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            for fact in missing:
                handle.write(json.dumps(fact, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    elif not log.exists():
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
    _write_seal(db_path)


def _load(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    ensure(conn)
    rows = conn.execute(
        """
        SELECT fact_key, recorded_at, kind, name_key, name, contact_name, username, kept, source
        FROM scan_facts
        """
    ).fetchall()
    return {str(row["fact_key"]): _public(row) for row in rows}


def _read_log(log: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if not log.is_file():
        return found
    with log.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            key = str(item.get("factKey") or "")
            if key:
                item["source"] = _clean_source(str(item.get("source") or ""))
                found[key] = item
    return found


def _clean_source(value: str) -> str:
    return " ".join(str(value or "").split())[:80]


def _time_bounds(since: str, until: str) -> tuple[str, str]:
    """Mốc đầu gồm trong ngày. Mốc cuối là đầu ngày hôm sau, không gồm."""
    start = _time_text(since)
    end_text = _time_text(until)
    end = ""
    if len(end_text) == 10:
        year, month, day = (int(end_text[0:4]), int(end_text[5:7]), int(end_text[8:10]))
        try:
            end = (date(year, month, day) + timedelta(days=1)).isoformat()
        except ValueError:
            end = ""
    elif end_text:
        end = end_text
    return start, end


def _time_text(value: str) -> str:
    text = str(value or "").strip()[:40]
    if text and _TIME_TEXT.fullmatch(text):
        return text
    return ""


def _scan_cursor(cursor: str) -> tuple[str, str] | None:
    text = cursor.strip()
    if "\n" not in text:
        return None
    recorded_at, fact_key = text.split("\n", 1)
    if not recorded_at or not fact_key:
        return None
    return recorded_at, fact_key


def _scan_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "recordedAt": row["recorded_at"],
        "kind": row["kind"],
        "name": row["name"],
        "contactName": row["contact_name"],
        "username": row["username"],
        "source": str(row["source"] or ""),
        "kept": int(row["kept"]),
    }


def _write_seal(db_path: Path) -> None:
    mirror, log, seal = locations(db_path)
    with _open(db_path) as conn:
        count, last = _tail(conn)
    seal.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bytes": log.stat().st_size if log.is_file() else 0,
        "rows": count,
        "last": last,
    }
    seal.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    if mirror.is_file():
        return
    with _open(mirror) as conn:
        ensure(conn)
        conn.commit()


def _restore_working(db_path: Path) -> None:
    """Dòng người mất khỏi bảng đang dùng thì dựng lại từ lần quét mới nhất."""
    with _open(db_path) as conn:
        ensure(conn)
        _restore_kind(conn, "person", "saved_people")
        _restore_kind(conn, "duplicate", "scan_duplicates")
        _refresh_meta(conn)
        conn.commit()


def _restore_kind(conn: sqlite3.Connection, kind: str, table: str) -> None:
    rows = conn.execute(
        """
        SELECT name_key, name, contact_name, username, recorded_at
        FROM scan_facts
        WHERE kind = ? AND kept = 1
        ORDER BY recorded_at ASC, fact_key ASC
        """,
        (kind,),
    ).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest[str(row["name_key"])] = row
    for key, row in latest.items():
        existing = conn.execute(
            f"SELECT 1 FROM {table} WHERE name_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            f"""
            INSERT INTO {table}(name_key, name, contact_name, username, updated_at)
            VALUES(?,?,?,?,?)
            """,
            (key, row["name"], row["contact_name"], row["username"], row["recorded_at"]),
        )


def _refresh_meta(conn: sqlite3.Connection) -> None:
    total = int(conn.execute("SELECT COUNT(*) AS n FROM saved_people").fetchone()["n"])
    ready = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n FROM saved_people
            WHERE contact_name != '' AND username != ''
            """
        ).fetchone()["n"]
    )
    duplicates = int(conn.execute("SELECT COUNT(*) AS n FROM scan_duplicates").fetchone()["n"])
    conn.execute(
        """
        INSERT INTO people_meta(id, total, ready, duplicates) VALUES(1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          total=excluded.total,
          ready=excluded.ready,
          duplicates=excluded.duplicates
        """,
        (total, ready, duplicates),
    )
