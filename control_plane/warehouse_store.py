"""Kho tổng: một file SQLite dùng để tra cứu trực tiếp.

VPS chỉ giữ bản đang mở. Tải file đó về máy, và nạp lại đúng file khi cần.
Năm trường và các ô thêm được giữ nguyên từng ký tự.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from control_plane.db import session
from control_plane.warehouse import FIELDS, address_key, id_key, name_key, phone_key

FLUSH_EVERY = 8_000
EXPORT_PAGE = 8_000
_LOCK = threading.RLock()
_UNIQUE_KINDS = {"phone", "uid", "id"}
_LOOKUP_TABLE = {
    "phone": "wh_phone",
    "uid": "wh_uid",
    "id": "wh_id",
    "name": "wh_name",
    "address": "wh_address",
}
_CONTACT_COLUMNS = (
    "row_id, name, name_extra, phone, phone_extra, address, address_extra, "
    "uid, uid_extra, external_id, id_extra, source, updated_at"
)


@contextmanager
def _db(db_path: Path) -> Iterator[sqlite3.Connection]:
    with session(db_path) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        yield conn


def _cell(value: str | None) -> str | None:
    if not value:
        return None
    return value


def _sidecars(db_path: Path) -> list[Path]:
    return [Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")]


def _drop_sidecars(db_path: Path) -> None:
    for path in _sidecars(db_path):
        if path.is_file():
            path.unlink()


def _checkpoint(db_path: Path) -> None:
    if not db_path.is_file():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _copies_dir(db_path: Path) -> Path:
    return db_path.parent / f"{db_path.name}.copies"


def _retire_copies(db_path: Path) -> None:
    folder = _copies_dir(db_path)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def _create_lean(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS warehouse_contacts (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT,
          name_extra TEXT,
          phone TEXT,
          phone_extra TEXT,
          address TEXT,
          address_extra TEXT,
          uid TEXT,
          uid_extra TEXT,
          external_id TEXT,
          id_extra TEXT,
          source TEXT,
          batch_id INTEGER,
          updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS warehouse_batches (
          batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
          at TEXT NOT NULL,
          filename TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          inserted INTEGER NOT NULL DEFAULT 0,
          updated INTEGER NOT NULL DEFAULT 0,
          skipped INTEGER NOT NULL DEFAULT 0,
          reviewed INTEGER NOT NULL DEFAULT 0,
          mapping_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS warehouse_templates (
          fingerprint TEXT PRIMARY KEY,
          headers_json TEXT NOT NULL,
          mapping_json TEXT NOT NULL,
          used_count INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS warehouse_meta (
          k TEXT PRIMARY KEY,
          n INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS warehouse_review (
          review_id INTEGER PRIMARY KEY AUTOINCREMENT,
          at TEXT NOT NULL,
          reason TEXT NOT NULL,
          batch_id INTEGER,
          payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wh_phone (
          value TEXT PRIMARY KEY,
          row_id INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS wh_uid (
          value TEXT PRIMARY KEY,
          row_id INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS wh_id (
          value TEXT PRIMARY KEY,
          row_id INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS wh_name (
          value TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          PRIMARY KEY(value, row_id)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS wh_address (
          value TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          PRIMARY KEY(value, row_id)
        ) WITHOUT ROWID;
        """
    )


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _is_lean(conn: sqlite3.Connection) -> bool:
    columns = _column_names(conn, "warehouse_contacts")
    if not columns or "name_key" in columns or "created_at" in columns or "phone_key" in columns:
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wh_phone'"
    ).fetchone()
    return row is not None


def _copy_legacy_contacts(conn: sqlite3.Connection) -> None:
    columns = _column_names(conn, "warehouse_contacts")
    last = 0
    while True:
        rows = conn.execute(
            "SELECT * FROM warehouse_contacts WHERE row_id>? ORDER BY row_id LIMIT 2000",
            (last,),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            last = int(row["row_id"])

            def take(name: str) -> str | None:
                if name not in columns:
                    return None
                return _cell(row[name] or "")

            stamp = None
            if "updated_at" in columns and row["updated_at"]:
                stamp = row["updated_at"]
            elif "created_at" in columns and row["created_at"]:
                stamp = row["created_at"]
            conn.execute(
                """
                INSERT INTO warehouse_contacts_lean(
                  row_id, name, name_extra, phone, phone_extra, address, address_extra,
                  uid, uid_extra, external_id, id_extra, source, batch_id, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    last,
                    take("name"),
                    take("name_extra"),
                    take("phone"),
                    take("phone_extra"),
                    take("address"),
                    take("address_extra"),
                    take("uid"),
                    take("uid_extra"),
                    take("external_id"),
                    take("id_extra"),
                    take("source"),
                    row["batch_id"] if "batch_id" in columns else None,
                    stamp,
                ),
            )
        conn.commit()


def _ensure_schema(conn: sqlite3.Connection) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='warehouse_contacts'"
    ).fetchone()
    if exists is None:
        _create_lean(conn)
        return False
    if _is_lean(conn):
        _create_lean(conn)
        return False
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS wh_contacts_insert;
        DROP TRIGGER IF EXISTS wh_contacts_delete;
        DROP TRIGGER IF EXISTS wh_contacts_keys;
        DROP TRIGGER IF EXISTS wh_contacts_phone;
        CREATE TABLE warehouse_contacts_lean (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT,
          name_extra TEXT,
          phone TEXT,
          phone_extra TEXT,
          address TEXT,
          address_extra TEXT,
          uid TEXT,
          uid_extra TEXT,
          external_id TEXT,
          id_extra TEXT,
          source TEXT,
          batch_id INTEGER,
          updated_at TEXT
        );
        """
    )
    _copy_legacy_contacts(conn)
    conn.execute("DROP TABLE warehouse_contacts")
    conn.execute("ALTER TABLE warehouse_contacts_lean RENAME TO warehouse_contacts")
    maximum = int(conn.execute("SELECT COALESCE(MAX(row_id), 0) AS n FROM warehouse_contacts").fetchone()["n"])
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone():
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('warehouse_contacts', 'warehouse_contacts_lean')")
        conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('warehouse_contacts', ?)", (maximum,))
    conn.execute("DROP TABLE IF EXISTS warehouse_aliases")
    conn.execute("DROP TABLE IF EXISTS warehouse_changes")
    _create_lean(conn)
    _backfill_lookups(conn)
    _ensure_batch_column(conn)
    conn.execute(
        "INSERT INTO warehouse_meta(k, n) VALUES('counts_ready', 0) ON CONFLICT(k) DO UPDATE SET n=0"
    )
    return True


def _ensure_batch_column(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "warehouse_batches" not in tables:
        return
    if "reviewed" not in _column_names(conn, "warehouse_batches"):
        conn.execute("ALTER TABLE warehouse_batches ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")


def _install_count_triggers(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS wh_contacts_insert;
        DROP TRIGGER IF EXISTS wh_contacts_delete;
        DROP TRIGGER IF EXISTS wh_contacts_keys;
        DROP TRIGGER IF EXISTS wh_contacts_phone;
        DROP TRIGGER IF EXISTS wh_batches_insert;
        DROP TRIGGER IF EXISTS wh_batches_delete;
        CREATE TRIGGER wh_contacts_insert AFTER INSERT ON warehouse_contacts BEGIN
          UPDATE warehouse_meta SET n = n + 1 WHERE k = 'contacts';
          UPDATE warehouse_meta SET n = n + CASE WHEN NEW.phone IS NOT NULL AND NEW.phone != '' THEN 1 ELSE 0 END WHERE k = 'phones';
          UPDATE warehouse_meta SET n = n + CASE WHEN NEW.uid IS NOT NULL AND NEW.uid != '' THEN 1 ELSE 0 END WHERE k = 'uids';
        END;
        CREATE TRIGGER wh_contacts_delete AFTER DELETE ON warehouse_contacts BEGIN
          UPDATE warehouse_meta SET n = n - 1 WHERE k = 'contacts';
          UPDATE warehouse_meta SET n = n - CASE WHEN OLD.phone IS NOT NULL AND OLD.phone != '' THEN 1 ELSE 0 END WHERE k = 'phones';
          UPDATE warehouse_meta SET n = n - CASE WHEN OLD.uid IS NOT NULL AND OLD.uid != '' THEN 1 ELSE 0 END WHERE k = 'uids';
        END;
        CREATE TRIGGER wh_contacts_phone AFTER UPDATE OF phone, uid ON warehouse_contacts BEGIN
          UPDATE warehouse_meta SET n = n
            + CASE WHEN NEW.phone IS NOT NULL AND NEW.phone != '' THEN 1 ELSE 0 END
            - CASE WHEN OLD.phone IS NOT NULL AND OLD.phone != '' THEN 1 ELSE 0 END
            WHERE k = 'phones';
          UPDATE warehouse_meta SET n = n
            + CASE WHEN NEW.uid IS NOT NULL AND NEW.uid != '' THEN 1 ELSE 0 END
            - CASE WHEN OLD.uid IS NOT NULL AND OLD.uid != '' THEN 1 ELSE 0 END
            WHERE k = 'uids';
        END;
        CREATE TRIGGER wh_batches_insert AFTER INSERT ON warehouse_batches BEGIN
          UPDATE warehouse_meta SET n = n + 1 WHERE k = 'batches';
        END;
        CREATE TRIGGER wh_batches_delete AFTER DELETE ON warehouse_batches BEGIN
          UPDATE warehouse_meta SET n = n - 1 WHERE k = 'batches';
        END;
        """
    )


def _seed_meta_rows(conn: sqlite3.Connection) -> None:
    for key in ("contacts", "phones", "uids", "batches"):
        conn.execute("INSERT OR IGNORE INTO warehouse_meta(k, n) VALUES(?, 0)", (key,))


def _ensure_meta(conn: sqlite3.Connection) -> None:
    _seed_meta_rows(conn)
    ready = conn.execute("SELECT n FROM warehouse_meta WHERE k='counts_ready'").fetchone()
    if ready and int(ready["n"]) == 1:
        return
    total = int(conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts").fetchone()["n"])
    phones = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM warehouse_contacts WHERE phone IS NOT NULL AND phone != ''"
        ).fetchone()["n"]
    )
    uids = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM warehouse_contacts WHERE uid IS NOT NULL AND uid != ''"
        ).fetchone()["n"]
    )
    batches = int(conn.execute("SELECT COUNT(*) AS n FROM warehouse_batches").fetchone()["n"])
    for key, value in (("contacts", total), ("phones", phones), ("uids", uids), ("batches", batches)):
        conn.execute("UPDATE warehouse_meta SET n=? WHERE k=?", (value, key))
    conn.execute(
        "INSERT INTO warehouse_meta(k, n) VALUES('counts_ready', 1) ON CONFLICT(k) DO UPDATE SET n=1"
    )


def _meta_get(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT n FROM warehouse_meta WHERE k=?", (key,)).fetchone()
    return int(row["n"]) if row else 0


def init_warehouse(db_path: Path) -> None:
    with _LOCK:
        migrated = False
        with _db(db_path) as conn:
            migrated = _ensure_schema(conn)
            _ensure_batch_column(conn)
            _install_count_triggers(conn)
            _seed_meta_rows(conn)
            _ensure_meta(conn)
        if migrated and db_path.is_file() and db_path.stat().st_size < 200 * 1024 * 1024:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
        _retire_copies(db_path)


def list_templates(db_path: Path) -> dict[str, dict[str, int]]:
    with _LOCK:
        with _db(db_path) as conn:
            rows = conn.execute("SELECT fingerprint, mapping_json FROM warehouse_templates").fetchall()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        raw = json.loads(row["mapping_json"] or "{}")
        mapping = {str(key): int(value) for key, value in raw.items() if key in FIELDS}
        out[row["fingerprint"]] = mapping
    return out


def save_template(
    db_path: Path,
    fingerprint: str,
    headers: list[str],
    mapping: dict[str, int],
    updated_at: str,
) -> None:
    if not fingerprint or not mapping:
        return
    with _LOCK:
        with _db(db_path) as conn:
            conn.execute(
                """
                INSERT INTO warehouse_templates(fingerprint, headers_json, mapping_json, used_count, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                  headers_json=excluded.headers_json,
                  mapping_json=excluded.mapping_json,
                  used_count=warehouse_templates.used_count + 1,
                  updated_at=excluded.updated_at
                """,
                (
                    fingerprint,
                    json.dumps(headers, ensure_ascii=False),
                    json.dumps(mapping, ensure_ascii=False),
                    1,
                    updated_at,
                ),
            )


def _blank_row() -> dict[str, str]:
    return {
        "name": "",
        "name_extra": "",
        "phone": "",
        "phone_extra": "",
        "address": "",
        "address_extra": "",
        "uid": "",
        "uid_extra": "",
        "external_id": "",
        "id_extra": "",
    }


def _parts(text: str) -> list[str]:
    return [part.strip() for part in (text or "").split("\n") if part.strip()]


def _get_row(conn: sqlite3.Connection, row_id: int) -> dict[str, str] | None:
    row = conn.execute(
        f"SELECT {_CONTACT_COLUMNS} FROM warehouse_contacts WHERE row_id=?",
        (row_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _blank_row()
    payload["row_id"] = str(row["row_id"])
    payload["source"] = row["source"] or ""
    for key in _blank_row():
        payload[key] = row[key] or ""
    return payload


def _alias_values(row: dict[str, str] | None) -> list[tuple[str, str]]:
    if not row:
        return []
    pairs: list[tuple[str, str]] = []

    def add(kind: str, value: str) -> None:
        if value and (kind, value) not in pairs:
            pairs.append((kind, value))

    add("name", name_key(row.get("name") or ""))
    for part in _parts(row.get("name_extra") or ""):
        add("name", name_key(part))
    add("phone", row.get("phone_key") or phone_key(row.get("phone") or ""))
    for part in _parts(row.get("phone_extra") or ""):
        add("phone", phone_key(part))
    add("address", row.get("address_key") or address_key(row.get("address") or ""))
    for part in _parts(row.get("address_extra") or ""):
        add("address", address_key(part))
    add("uid", row.get("uid") or "")
    for part in _parts(row.get("uid_extra") or ""):
        add("uid", part)
    add("id", id_key(row.get("external_id") or row.get("id") or ""))
    for part in _parts(row.get("id_extra") or ""):
        add("id", id_key(part))
    return pairs


def _replace_lookups(
    conn: sqlite3.Connection,
    row_id: int,
    new_row: dict[str, str],
    old_row: dict[str, str] | None,
) -> None:
    old_pairs = set(_alias_values(old_row))
    new_pairs = set(_alias_values(new_row))
    for kind, value in old_pairs - new_pairs:
        conn.execute(
            f"DELETE FROM {_LOOKUP_TABLE[kind]} WHERE value=? AND row_id=?",
            (value, row_id),
        )
    for kind, value in new_pairs - old_pairs:
        conn.execute(
            f"INSERT INTO {_LOOKUP_TABLE[kind]}(value, row_id) VALUES(?,?)",
            (value, row_id),
        )


def _backfill_lookups(conn: sqlite3.Connection) -> None:
    for table in _LOOKUP_TABLE.values():
        conn.execute(f"DELETE FROM {table}")
    last = 0
    while True:
        rows = conn.execute(
            f"SELECT {_CONTACT_COLUMNS} FROM warehouse_contacts WHERE row_id>? ORDER BY row_id LIMIT 2000",
            (last,),
        ).fetchall()
        if not rows:
            break
        for item in rows:
            last = int(item["row_id"])
            stored = _get_row(conn, last)
            if stored:
                _replace_lookups(conn, last, stored, None)
        conn.commit()


def _id_hits(conn: sqlite3.Connection, kind: str, value: str) -> list[int]:
    if kind not in _LOOKUP_TABLE or not value:
        return []
    rows = conn.execute(
        f"SELECT row_id FROM {_LOOKUP_TABLE[kind]} WHERE value=? LIMIT 3",
        (value,),
    ).fetchall()
    return [int(row["row_id"]) for row in rows]


def find_match(conn: sqlite3.Connection, record: dict[str, str]) -> tuple[int | None, str]:
    """Trùng một giá trị của bất kỳ trường nào trên đúng một hàng thì lấy hàng đó."""
    uid = record.get("uid") or ""
    key = record.get("phone_key") or phone_key(record.get("phone") or "")
    external = id_key(record.get("id") or record.get("external_id") or "")
    person = name_key(record.get("name") or "")
    place = address_key(record.get("address") or "")
    hits: list[tuple[int, str]] = []
    if uid:
        for row_id in _id_hits(conn, "uid", uid):
            hits.append((row_id, "uid"))
    if key:
        for row_id in _id_hits(conn, "phone", key):
            hits.append((row_id, "phone"))
    if external:
        for row_id in _id_hits(conn, "id", external):
            hits.append((row_id, "id"))
    if person and len(person.split()) >= 2:
        name_hits = _id_hits(conn, "name", person)
        if len(name_hits) == 1:
            hits.append((name_hits[0], "name"))
    if place and len(place) >= 16:
        place_hits = _id_hits(conn, "address", place)
        if len(place_hits) == 1:
            hits.append((place_hits[0], "address"))
    row_ids = {row_id for row_id, _reason in hits}
    if len(row_ids) > 1:
        return None, "conflict"
    if len(row_ids) == 1:
        return hits[0][0], hits[0][1]
    return None, ""


def _merge_field(primary: str, extra: str, incoming: str, key_of) -> tuple[str, str, bool]:
    incoming = (incoming or "").strip()
    if not incoming or not key_of(incoming):
        return primary, extra, False
    owned = []
    if primary:
        owned.append(key_of(primary))
    owned.extend(key_of(part) for part in _parts(extra))
    if key_of(incoming) in owned:
        return primary, extra, False
    if not primary:
        return incoming, extra, True
    return primary, "\n".join([*_parts(extra), incoming]), True


def preview_matches(db_path: Path, records: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    with _LOCK:
        with _db(db_path) as conn:
            for record in records:
                row_id, reason = find_match(
                    conn, {**record, "phone_key": phone_key(record.get("phone") or "")}
                )
                item = dict(record)
                if row_id:
                    item["match"] = reason
                    item["matchId"] = str(row_id)
                elif reason in {"ambiguous", "conflict"}:
                    item["match"] = reason
                else:
                    item["match"] = "new"
                out.append(item)
    return out


def _incoming_fields(record: dict[str, str]) -> dict[str, str]:
    phone = record.get("phone") or ""
    external = id_key(record.get("id") or record.get("external_id") or "")
    return {
        "name": record.get("name") or "",
        "phone": phone,
        "phone_key": record.get("phone_key") or phone_key(phone),
        "address": record.get("address") or "",
        "uid": record.get("uid") or "",
        "external_id": external,
        "id": external,
    }


def _apply_incoming(existing: dict[str, str], incoming: dict[str, str]) -> tuple[dict[str, str], bool]:
    merged = dict(existing)
    changed = False
    for primary, extra, raw, key_of in (
        ("name", "name_extra", incoming["name"], name_key),
        ("phone", "phone_extra", incoming["phone"], phone_key),
        ("address", "address_extra", incoming["address"], address_key),
        ("uid", "uid_extra", incoming["uid"], lambda value: value.strip()),
        ("external_id", "id_extra", incoming["external_id"], id_key),
    ):
        next_primary, next_extra, field_changed = _merge_field(merged[primary], merged[extra], raw, key_of)
        merged[primary] = next_primary
        merged[extra] = next_extra
        changed = changed or field_changed
    merged["name_key"] = name_key(merged["name"])
    merged["phone_key"] = phone_key(merged["phone"])
    merged["address_key"] = address_key(merged["address"])
    return merged, changed


def _write_contact(
    conn: sqlite3.Connection,
    row_id: int,
    row: dict[str, str],
    at: str,
    batch_id: int,
    source: str,
    old: dict[str, str] | None,
) -> None:
    conn.execute(
        """
        UPDATE warehouse_contacts
        SET name=?, name_extra=?, phone=?, phone_extra=?,
            address=?, address_extra=?, uid=?, uid_extra=?,
            external_id=?, id_extra=?, source=?, batch_id=?, updated_at=?
        WHERE row_id=?
        """,
        (
            _cell(row["name"]),
            _cell(row["name_extra"]),
            _cell(row["phone"]),
            _cell(row["phone_extra"]),
            _cell(row["address"]),
            _cell(row["address_extra"]),
            _cell(row["uid"]),
            _cell(row["uid_extra"]),
            _cell(row["external_id"]),
            _cell(row["id_extra"]),
            _cell(source or row.get("source") or ""),
            batch_id or None,
            at,
            row_id,
        ),
    )
    _replace_lookups(conn, row_id, row, old)


def upsert_records(
    db_path: Path,
    records: list[dict[str, str]],
    *,
    filename: str,
    source: str,
    mapping: dict[str, int],
    at: str,
) -> dict[str, int]:
    inserted = 0
    updated = 0
    skipped = 0
    reviewed = 0
    with _LOCK:
        with _db(db_path) as conn:
            conn.execute(
                """
                INSERT INTO warehouse_batches(at, filename, source, inserted, updated, skipped, reviewed, mapping_json)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (at, filename, source, 0, 0, 0, 0, json.dumps(mapping, ensure_ascii=False)),
            )
            batch_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            done = 0
            for record in records:
                incoming = _incoming_fields(record)
                existing_id, reason = find_match(conn, incoming)
                if reason == "conflict":
                    payload = {**incoming, "reason": "conflict", "batchId": batch_id, "source": source}
                    conn.execute(
                        "INSERT INTO warehouse_review(at, reason, batch_id, payload_json) VALUES(?,?,?,?)",
                        (at, "conflict", batch_id, json.dumps(payload, ensure_ascii=False)),
                    )
                    reviewed += 1
                elif existing_id:
                    existing = _get_row(conn, existing_id)
                    if existing is None:
                        skipped += 1
                    else:
                        merged, changed = _apply_incoming(existing, incoming)
                        if not changed:
                            skipped += 1
                        else:
                            _write_contact(conn, existing_id, merged, at, batch_id, source or existing["source"], existing)
                            updated += 1
                else:
                    blank = _blank_row()
                    merged, _changed = _apply_incoming(blank, incoming)
                    if not any(merged[name] for name in ("name", "phone", "address", "uid", "external_id")):
                        skipped += 1
                    else:
                        conn.execute(
                            """
                            INSERT INTO warehouse_contacts(
                              name, name_extra, phone, phone_extra,
                              address, address_extra, uid, uid_extra, external_id, id_extra,
                              source, batch_id, updated_at
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                _cell(merged["name"]),
                                _cell(merged["name_extra"]),
                                _cell(merged["phone"]),
                                _cell(merged["phone_extra"]),
                                _cell(merged["address"]),
                                _cell(merged["address_extra"]),
                                _cell(merged["uid"]),
                                _cell(merged["uid_extra"]),
                                _cell(merged["external_id"]),
                                _cell(merged["id_extra"]),
                                _cell(source),
                                batch_id,
                                at,
                            ),
                        )
                        row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                        _replace_lookups(conn, row_id, merged, None)
                        inserted += 1
                done += 1
                if done % FLUSH_EVERY == 0:
                    conn.commit()
            conn.execute(
                """
                UPDATE warehouse_batches
                SET inserted=?, updated=?, skipped=?, reviewed=?
                WHERE batch_id=?
                """,
                (inserted, updated, skipped, reviewed, batch_id),
            )
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "reviewed": reviewed,
        "batchId": batch_id,
    }


def _search_clause(query: str) -> tuple[str, list[object]]:
    q = (query or "").strip()
    if not q:
        return "", []
    key = phone_key(q)
    person = name_key(q)
    place = address_key(q)
    ext = id_key(q)
    clauses: list[str] = []
    params: list[object] = []
    if key:
        clauses.append(
            "(row_id IN (SELECT row_id FROM wh_phone WHERE value=?) OR phone_extra LIKE ?)"
        )
        params.extend([key, f"%{key}%"])
    digits = "".join(ch for ch in q if ch.isdigit())
    if 5 <= len(digits) <= 20:
        clauses.append("(row_id IN (SELECT row_id FROM wh_uid WHERE value=?) OR uid_extra LIKE ?)")
        params.extend([digits, f"%{digits}%"])
    if ext.startswith("@") or (ext and ext != q and ext != key):
        clauses.append("(row_id IN (SELECT row_id FROM wh_id WHERE value=?) OR id_extra LIKE ?)")
        params.extend([ext, f"%{ext}%"])
    elif q.startswith("@"):
        folded = q.casefold()
        clauses.append("(row_id IN (SELECT row_id FROM wh_id WHERE value=?) OR id_extra LIKE ?)")
        params.extend([folded, f"%{folded}%"])
    if person:
        clauses.append(
            "(row_id IN (SELECT row_id FROM wh_name WHERE value=? OR value LIKE ?) OR name_extra LIKE ?)"
        )
        params.extend([person, person + "%", f"%{q}%"])
    if place:
        clauses.append(
            "(row_id IN (SELECT row_id FROM wh_address WHERE value=? OR value LIKE ?) OR address_extra LIKE ?)"
        )
        like = place + "%" if len(place) >= 4 else place
        params.extend([place, like, f"%{q}%"])
    if not clauses:
        return " AND 0", []
    return " AND (" + " OR ".join(clauses) + ")", params


def _fetch_page(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    after: int,
) -> list[sqlite3.Row]:
    extra, extra_params = _search_clause(query)
    sql = (
        f"SELECT {_CONTACT_COLUMNS} "
        f"FROM warehouse_contacts WHERE row_id > ?{extra} ORDER BY row_id ASC LIMIT ?"
    )
    return conn.execute(sql, [after, *extra_params, limit]).fetchall()


def _item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["row_id"],
        "name": row["name"] or "",
        "nameExtra": row["name_extra"] or "",
        "phone": row["phone"] or "",
        "phoneExtra": row["phone_extra"] or "",
        "address": row["address"] or "",
        "addressExtra": row["address_extra"] or "",
        "uid": row["uid"] or "",
        "uidExtra": row["uid_extra"] or "",
        "externalId": row["external_id"] or "",
        "idExtra": row["id_extra"] or "",
        "source": row["source"] or "",
        "updatedAt": row["updated_at"] or "",
    }


def search_contacts(
    db_path: Path,
    query: str,
    *,
    limit: int,
    after: int,
) -> dict[str, Any]:
    with _LOCK:
        with _db(db_path) as conn:
            rows = _fetch_page(conn, query, limit=limit + 1, after=after)
            total = _meta_get(conn, "contacts")
    items = [_item(row) for row in rows[:limit]]
    nxt = int(rows[limit]["row_id"]) if len(rows) > limit else 0
    return {"count": total, "items": items, "next": nxt}


def export_csv_rows(db_path: Path, query: str) -> Iterator[bytes]:
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Tên",
            "Tên thêm",
            "Số điện thoại",
            "Số điện thoại thêm",
            "Địa chỉ",
            "Địa chỉ thêm",
            "UID Facebook",
            "UID thêm",
            "ID TikTok",
            "TikTok thêm",
        ]
    )
    yield buf.getvalue().encode("utf-8")
    after = 0
    with _LOCK:
        with _db(db_path) as conn:
            while True:
                rows = _fetch_page(conn, query, limit=EXPORT_PAGE, after=after)
                if not rows:
                    break
                buf = io.StringIO()
                writer = csv.writer(buf)
                for row in rows:
                    writer.writerow(
                        [
                            row["name"] or "",
                            row["name_extra"] or "",
                            row["phone"] or "",
                            row["phone_extra"] or "",
                            row["address"] or "",
                            row["address_extra"] or "",
                            row["uid"] or "",
                            row["uid_extra"] or "",
                            row["external_id"] or "",
                            row["id_extra"] or "",
                        ]
                    )
                yield buf.getvalue().encode("utf-8")
                after = int(rows[-1]["row_id"])


def export_csv(db_path: Path, query: str) -> bytes:
    return b"".join(export_csv_rows(db_path, query))


def _storage_bytes(db_path: Path) -> int:
    total = 0
    for path in (db_path, *_sidecars(db_path)):
        if path.is_file():
            total += path.stat().st_size
    return total


def warehouse_stats(db_path: Path) -> dict[str, Any]:
    with _LOCK:
        with _db(db_path) as conn:
            _ensure_meta(conn)
            counts = {
                "contacts": _meta_get(conn, "contacts"),
                "phones": _meta_get(conn, "phones"),
                "uids": _meta_get(conn, "uids"),
                "batches": _meta_get(conn, "batches"),
            }
            reviewed = conn.execute("SELECT COUNT(*) AS n FROM warehouse_review").fetchone()
    usage = shutil.disk_usage(db_path.parent if db_path.parent.is_dir() else Path("."))
    counts["reviewed"] = int(reviewed["n"]) if reviewed else 0
    counts["bytes"] = _storage_bytes(db_path)
    counts["diskFree"] = usage.free
    counts["diskTotal"] = usage.total
    counts["storage"] = "one"
    return counts


def open_snapshot(db_path: Path) -> Iterator[bytes]:
    """Chốt WAL rồi đọc đúng file kho. Không tạo bản thứ hai trên đĩa."""

    def chunks() -> Iterator[bytes]:
        _LOCK.acquire()
        handle = None
        try:
            _checkpoint(db_path)
            handle = db_path.open("rb")
            while True:
                blob = handle.read(1024 * 1024)
                if not blob:
                    break
                yield blob
        finally:
            if handle is not None:
                handle.close()
            _LOCK.release()

    return chunks()


def snapshot_to_file(db_path: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with _LOCK:
        with _db(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target = sqlite3.connect(str(dest))
            try:
                conn.backup(target)
            finally:
                target.close()


def _validate_snapshot(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 100:
        raise ValueError("File không phải bản lưu kho.")
    with path.open("rb") as handle:
        header = handle.read(16)
    if header != b"SQLite format 3\x00":
        raise ValueError("File không phải bản lưu kho.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise ValueError("Bản lưu không nguyên vẹn.")
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='warehouse_contacts'"
        ).fetchone()
        if row is None:
            raise ValueError("File này không phải kho tổng.")
    except sqlite3.DatabaseError as exc:
        raise ValueError("Bản lưu không đọc được.") from exc
    finally:
        conn.close()


def restore_snapshot(db_path: Path, incoming: Path) -> dict[str, Any]:
    """Thay kho đang dùng bằng file đã tải. Dữ liệu trong file được giữ nguyên."""
    _validate_snapshot(incoming)
    with _LOCK:
        _validate_snapshot(incoming)
        previous = db_path.with_name(db_path.name + ".previous")
        if previous.exists():
            previous.unlink()
        if db_path.exists():
            _checkpoint(db_path)
            _drop_sidecars(db_path)
            os.replace(db_path, previous)
        try:
            os.replace(incoming, db_path)
            _drop_sidecars(db_path)
            with _db(db_path) as conn:
                _ensure_schema(conn)
                _install_count_triggers(conn)
                _seed_meta_rows(conn)
                conn.execute(
                    "INSERT INTO warehouse_meta(k, n) VALUES('counts_ready', 0) ON CONFLICT(k) DO UPDATE SET n=0"
                )
                _ensure_meta(conn)
        except Exception:
            if previous.exists():
                if db_path.exists():
                    db_path.unlink()
                os.replace(previous, db_path)
                _drop_sidecars(db_path)
            raise
        if previous.exists():
            previous.unlink()
        _retire_copies(db_path)
    return warehouse_stats(db_path)
