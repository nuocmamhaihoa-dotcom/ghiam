"""Kho tổng: lưu, ghép trùng, tìm, xuất — tra cứu theo chỉ mục, không tải cả bảng vào RAM."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from control_plane.db import session
from control_plane.warehouse import FIELDS, address_key, id_key, name_key, phone_key

FLUSH_EVERY = 8_000
EXPORT_PAGE = 8_000
_LOOKUP_COLS = {
    "uid": "uid",
    "phone": "phone_key",
    "id": "external_id",
    "name": "name_key",
    "address": "address_key",
}


def init_warehouse(db_path: Path) -> None:
    with session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS warehouse_contacts (
              row_id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL DEFAULT '',
              name_key TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              phone_key TEXT NOT NULL DEFAULT '',
              address TEXT NOT NULL DEFAULT '',
              address_key TEXT NOT NULL DEFAULT '',
              uid TEXT NOT NULL DEFAULT '',
              external_id TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              batch_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS warehouse_batches (
              batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              filename TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT '',
              inserted INTEGER NOT NULL DEFAULT 0,
              updated INTEGER NOT NULL DEFAULT 0,
              skipped INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS warehouse_aliases (
              row_id INTEGER NOT NULL,
              kind TEXT NOT NULL,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS warehouse_review (
              review_id INTEGER PRIMARY KEY AUTOINCREMENT,
              at TEXT NOT NULL,
              reason TEXT NOT NULL,
              batch_id INTEGER,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS warehouse_changes (
              change_id INTEGER PRIMARY KEY AUTOINCREMENT,
              op TEXT NOT NULL,
              at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(warehouse_contacts)")}
        for extra in ("name_extra", "phone_extra", "address_extra", "uid_extra", "id_extra"):
            if extra not in columns:
                conn.execute(f"ALTER TABLE warehouse_contacts ADD COLUMN {extra} TEXT NOT NULL DEFAULT ''")
        batch_columns = {row[1] for row in conn.execute("PRAGMA table_info(warehouse_batches)")}
        if "reviewed" not in batch_columns:
            conn.execute("ALTER TABLE warehouse_batches ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")
        if "name_key" not in columns:
            conn.execute("ALTER TABLE warehouse_contacts ADD COLUMN name_key TEXT NOT NULL DEFAULT ''")
        if "address_key" not in columns:
            conn.execute("ALTER TABLE warehouse_contacts ADD COLUMN address_key TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wh_phone ON warehouse_contacts(phone_key) WHERE phone_key != ''"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_uid ON warehouse_contacts(uid) WHERE uid != ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wh_ext ON warehouse_contacts(external_id) WHERE external_id != ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wh_name_key ON warehouse_contacts(name_key) WHERE name_key != ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wh_addr_key ON warehouse_contacts(address_key) WHERE address_key != ''"
        )
        _try_unique(conn, "idx_wh_phone_uniq", "phone_key")
        _try_unique(conn, "idx_wh_uid_uniq", "uid")
        _try_unique(conn, "idx_wh_ext_uniq", "external_id")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wh_alias ON warehouse_aliases(kind, value)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_alias_row ON warehouse_aliases(row_id)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_phone ON warehouse_aliases(value) WHERE kind='phone'"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_uid ON warehouse_aliases(value) WHERE kind='uid'"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_id ON warehouse_aliases(value) WHERE kind='id'"
        )
        _seed_meta_rows(conn)
        _install_count_triggers(conn)
        _backfill_keys(conn)
        _backfill_aliases(conn)
        _ensure_meta(conn)
        _persist_copies(db_path, conn)


def _try_unique(conn: sqlite3.Connection, name: str, column: str) -> None:
    if column not in {"phone_key", "uid", "external_id"}:
        return
    try:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON warehouse_contacts({column}) WHERE {column} != ''"
        )
    except sqlite3.IntegrityError:
        pass


def _backfill_keys(conn: sqlite3.Connection) -> None:
    ready = conn.execute("SELECT n FROM warehouse_meta WHERE k='keys_ready'").fetchone()
    if ready and int(ready["n"]) == 1:
        return
    while True:
        rows = conn.execute(
            """
            SELECT row_id, name, address FROM warehouse_contacts
            WHERE (name != '' AND name_key = '') OR (address != '' AND address_key = '')
            LIMIT 5000
            """
        ).fetchall()
        if not rows:
            break
        for row in rows:
            conn.execute(
                "UPDATE warehouse_contacts SET name_key=?, address_key=? WHERE row_id=?",
                (name_key(row["name"]), address_key(row["address"]), row["row_id"]),
            )
        conn.commit()
    conn.execute(
        "INSERT INTO warehouse_meta(k, n) VALUES('keys_ready', 1) ON CONFLICT(k) DO UPDATE SET n=1"
    )


def _seed_meta_rows(conn: sqlite3.Connection) -> None:
    for key in ("contacts", "phones", "uids", "batches"):
        conn.execute("INSERT OR IGNORE INTO warehouse_meta(k, n) VALUES(?, 0)", (key,))


def _install_count_triggers(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS wh_contacts_insert AFTER INSERT ON warehouse_contacts BEGIN
          UPDATE warehouse_meta SET n = n + 1 WHERE k = 'contacts';
          UPDATE warehouse_meta SET n = n + CASE WHEN NEW.phone_key != '' THEN 1 ELSE 0 END WHERE k = 'phones';
          UPDATE warehouse_meta SET n = n + CASE WHEN NEW.uid != '' THEN 1 ELSE 0 END WHERE k = 'uids';
        END;
        CREATE TRIGGER IF NOT EXISTS wh_contacts_delete AFTER DELETE ON warehouse_contacts BEGIN
          UPDATE warehouse_meta SET n = n - 1 WHERE k = 'contacts';
          UPDATE warehouse_meta SET n = n - CASE WHEN OLD.phone_key != '' THEN 1 ELSE 0 END WHERE k = 'phones';
          UPDATE warehouse_meta SET n = n - CASE WHEN OLD.uid != '' THEN 1 ELSE 0 END WHERE k = 'uids';
        END;
        CREATE TRIGGER IF NOT EXISTS wh_contacts_keys AFTER UPDATE OF phone_key, uid ON warehouse_contacts BEGIN
          UPDATE warehouse_meta SET n = n
            + CASE WHEN NEW.phone_key != '' THEN 1 ELSE 0 END
            - CASE WHEN OLD.phone_key != '' THEN 1 ELSE 0 END
            WHERE k = 'phones';
          UPDATE warehouse_meta SET n = n
            + CASE WHEN NEW.uid != '' THEN 1 ELSE 0 END
            - CASE WHEN OLD.uid != '' THEN 1 ELSE 0 END
            WHERE k = 'uids';
        END;
        CREATE TRIGGER IF NOT EXISTS wh_batches_insert AFTER INSERT ON warehouse_batches BEGIN
          UPDATE warehouse_meta SET n = n + 1 WHERE k = 'batches';
        END;
        CREATE TRIGGER IF NOT EXISTS wh_batches_delete AFTER DELETE ON warehouse_batches BEGIN
          UPDATE warehouse_meta SET n = n - 1 WHERE k = 'batches';
        END;
        """
    )


def _ensure_meta(conn: sqlite3.Connection) -> None:
    _seed_meta_rows(conn)
    ready = conn.execute("SELECT n FROM warehouse_meta WHERE k='counts_ready'").fetchone()
    if ready and int(ready["n"]) == 1:
        return
    total = int(conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts").fetchone()["n"])
    phones = int(
        conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts WHERE phone_key != ''").fetchone()["n"]
    )
    uids = int(conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts WHERE uid != ''").fetchone()["n"])
    batches = int(conn.execute("SELECT COUNT(*) AS n FROM warehouse_batches").fetchone()["n"])
    for key, value in (("contacts", total), ("phones", phones), ("uids", uids), ("batches", batches)):
        conn.execute("UPDATE warehouse_meta SET n=? WHERE k=?", (value, key))
    conn.execute(
        "INSERT INTO warehouse_meta(k, n) VALUES('counts_ready', 1) ON CONFLICT(k) DO UPDATE SET n=1"
    )


def _meta_get(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT n FROM warehouse_meta WHERE k=?", (key,)).fetchone()
    return int(row["n"]) if row else 0


def list_templates(db_path: Path) -> dict[str, dict[str, int]]:
    with session(db_path) as conn:
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
    with session(db_path) as conn:
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


def _row_payload(row: sqlite3.Row | dict[str, str]) -> dict[str, str]:
    keys = row.keys() if hasattr(row, "keys") else row
    payload = _blank_row()
    payload.update(
        {
            "row_id": str(row["row_id"]),
            "name": row["name"] or "",
            "name_key": row["name_key"] if "name_key" in keys else name_key(row["name"] or ""),
            "phone": row["phone"] or "",
            "phone_key": row["phone_key"] or "",
            "address": row["address"] or "",
            "address_key": row["address_key"] if "address_key" in keys else address_key(row["address"] or ""),
            "uid": row["uid"] or "",
            "external_id": row["external_id"] or "",
            "source": row["source"] or "",
        }
    )
    for extra in ("name_extra", "phone_extra", "address_extra", "uid_extra", "id_extra"):
        if extra in keys:
            payload[extra] = row[extra] or ""
    return payload


def _alias_values(row: dict[str, str]) -> list[tuple[str, str]]:
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
    add("id", id_key(row.get("external_id") or ""))
    for part in _parts(row.get("id_extra") or ""):
        add("id", id_key(part))
    return pairs


def _replace_aliases(conn: sqlite3.Connection, row_id: int, row: dict[str, str]) -> None:
    conn.execute("DELETE FROM warehouse_aliases WHERE row_id=?", (row_id,))
    conn.executemany(
        "INSERT INTO warehouse_aliases(row_id, kind, value) VALUES(?,?,?)",
        [(row_id, kind, value) for kind, value in _alias_values(row)],
    )


def _backfill_aliases(conn: sqlite3.Connection) -> None:
    ready = conn.execute("SELECT n FROM warehouse_meta WHERE k='aliases_ready'").fetchone()
    if ready and int(ready["n"]) == 1:
        return
    last = 0
    while True:
        rows = conn.execute(
            "SELECT row_id FROM warehouse_contacts WHERE row_id>? ORDER BY row_id LIMIT 2000",
            (last,),
        ).fetchall()
        if not rows:
            break
        for item in rows:
            last = int(item["row_id"])
            stored = _get_row(conn, last)
            if stored and _alias_values(stored):
                _replace_aliases(conn, last, stored)
        conn.commit()
    conn.execute(
        "INSERT INTO warehouse_meta(k, n) VALUES('aliases_ready', 1) ON CONFLICT(k) DO UPDATE SET n=1"
    )


def _id_hits(conn: sqlite3.Connection, kind: str, value: str) -> list[int]:
    if kind not in _LOOKUP_COLS or not value:
        return []
    rows = conn.execute(
        "SELECT row_id FROM warehouse_aliases WHERE kind=? AND value=? LIMIT 3",
        (kind, value),
    ).fetchall()
    if rows:
        return [int(row["row_id"]) for row in rows]
    column = _LOOKUP_COLS[kind]
    legacy = conn.execute(
        f"SELECT row_id FROM warehouse_contacts WHERE {column} = ? LIMIT 3",
        (value,),
    ).fetchall()
    return [int(row["row_id"]) for row in legacy]


def _get_row(conn: sqlite3.Connection, row_id: int) -> dict[str, str] | None:
    row = conn.execute(
        "SELECT row_id, name, name_extra, name_key, phone, phone_extra, phone_key, "
        "address, address_extra, address_key, uid, uid_extra, external_id, id_extra, source "
        "FROM warehouse_contacts WHERE row_id=?",
        (row_id,),
    ).fetchone()
    return _row_payload(row) if row else None


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
    with session(db_path) as conn:
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


def _write_contact(conn: sqlite3.Connection, row_id: int, row: dict[str, str], at: str, batch_id: int, source: str) -> None:
    conn.execute(
        """
        UPDATE warehouse_contacts
        SET name=?, name_extra=?, name_key=?, phone=?, phone_extra=?, phone_key=?,
            address=?, address_extra=?, address_key=?, uid=?, uid_extra=?,
            external_id=?, id_extra=?, source=?, batch_id=?, updated_at=?
        WHERE row_id=?
        """,
        (
            row["name"],
            row["name_extra"],
            row["name_key"],
            row["phone"],
            row["phone_extra"],
            row["phone_key"],
            row["address"],
            row["address_extra"],
            row["address_key"],
            row["uid"],
            row["uid_extra"],
            row["external_id"],
            row["id_extra"],
            source or row.get("source") or "",
            batch_id,
            at,
            row_id,
        ),
    )
    _replace_aliases(conn, row_id, row)


def _log_change(conn: sqlite3.Connection, op: str, at: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO warehouse_changes(op, at, payload_json) VALUES(?,?,?)",
        (op, at, json.dumps(payload, ensure_ascii=False)),
    )


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
    with session(db_path) as conn:
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
                _log_change(conn, "review", at, payload)
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
                        _write_contact(conn, existing_id, merged, at, batch_id, source or existing["source"])
                        stored = _get_row(conn, existing_id)
                        _log_change(conn, "update", at, stored or merged)
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
                          name, name_extra, name_key, phone, phone_extra, phone_key,
                          address, address_extra, address_key, uid, uid_extra, external_id, id_extra,
                          source, batch_id, created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            merged["name"],
                            merged["name_extra"],
                            merged["name_key"],
                            merged["phone"],
                            merged["phone_extra"],
                            merged["phone_key"],
                            merged["address"],
                            merged["address_extra"],
                            merged["address_key"],
                            merged["uid"],
                            merged["uid_extra"],
                            merged["external_id"],
                            merged["id_extra"],
                            source,
                            batch_id,
                            at,
                            at,
                        ),
                    )
                    row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                    _replace_aliases(conn, row_id, merged)
                    stored = _get_row(conn, row_id)
                    _log_change(conn, "insert", at, stored or {**merged, "row_id": str(row_id)})
                    inserted += 1
            done += 1
            if done % FLUSH_EVERY == 0:
                _persist_copies(db_path, conn)
        conn.execute(
            """
            UPDATE warehouse_batches
            SET inserted=?, updated=?, skipped=?, reviewed=?
            WHERE batch_id=?
            """,
            (inserted, updated, skipped, reviewed, batch_id),
        )
    _persist_copies(db_path, None)
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
        clauses.append("(phone_key = ? OR phone_extra LIKE ? OR row_id IN (SELECT row_id FROM warehouse_aliases WHERE kind='phone' AND value=?))")
        params.extend([key, f"%{key}%", key])
    digits = "".join(ch for ch in q if ch.isdigit())
    if 5 <= len(digits) <= 20:
        clauses.append("(uid = ? OR uid_extra LIKE ?)")
        params.extend([digits, f"%{digits}%"])
    if ext.startswith("@") or (ext and ext != q and ext != key):
        clauses.append("(external_id = ? OR id_extra LIKE ?)")
        params.extend([ext, f"%{ext}%"])
    elif q.startswith("@"):
        clauses.append("(external_id = ? OR id_extra LIKE ?)")
        params.extend([q.casefold(), f"%{q.casefold()}%"])
    if person:
        clauses.append("(name_key = ? OR name_key LIKE ? OR name_extra LIKE ? OR row_id IN (SELECT row_id FROM warehouse_aliases WHERE kind='name' AND (value=? OR value LIKE ?)))")
        params.extend([person, person + "%", f"%{q}%", person, person + "%"])
    if place:
        clauses.append("(address_key = ? OR address_extra LIKE ? OR row_id IN (SELECT row_id FROM warehouse_aliases WHERE kind='address' AND value=?))")
        params.extend([place, f"%{q}%", place])
        if len(place) >= 4:
            clauses.append("address_key LIKE ?")
            params.append(place + "%")
    if not clauses:
        return " AND 0", []
    return " AND (" + " OR ".join(clauses) + ")", params


_CONTACT_COLUMNS = (
    "row_id, name, name_extra, phone, phone_extra, address, address_extra, "
    "uid, uid_extra, external_id, id_extra, source, updated_at"
)


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


def search_contacts(
    db_path: Path,
    query: str,
    *,
    limit: int,
    after: int,
) -> dict[str, Any]:
    with session(db_path) as conn:
        rows = _fetch_page(conn, query, limit=limit + 1, after=after)
        total = _meta_get(conn, "contacts")
    items = [
        {
            "id": row["row_id"],
            "name": row["name"],
            "nameExtra": row["name_extra"],
            "phone": row["phone"],
            "phoneExtra": row["phone_extra"],
            "address": row["address"],
            "addressExtra": row["address_extra"],
            "uid": row["uid"],
            "uidExtra": row["uid_extra"],
            "externalId": row["external_id"],
            "idExtra": row["id_extra"],
            "source": row["source"],
            "updatedAt": row["updated_at"],
        }
        for row in rows[:limit]
    ]
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
    with session(db_path) as conn:
        while True:
            rows = _fetch_page(conn, query, limit=EXPORT_PAGE, after=after)
            if not rows:
                break
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(
                    [
                        row["name"],
                        row["name_extra"],
                        row["phone"],
                        row["phone_extra"],
                        row["address"],
                        row["address_extra"],
                        row["uid"],
                        row["uid_extra"],
                        row["external_id"],
                        row["id_extra"],
                    ]
                )
            yield buf.getvalue().encode("utf-8")
            after = int(rows[-1]["row_id"])


def export_csv(db_path: Path, query: str) -> bytes:
    return b"".join(export_csv_rows(db_path, query))


def _copy_paths(db_path: Path) -> tuple[Path, Path, Path]:
    folder = db_path.parent / f"{db_path.name}.copies"
    return folder / "mirror.db", folder / "journal.jsonl", folder / "journal.pos"


def _journal_watermark(journal: Path) -> int:
    if not journal.is_file():
        return 0
    last = 0
    with journal.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 65536))
        tail = handle.read().decode("utf-8", errors="ignore")
    for line in tail.splitlines():
        try:
            last = int(json.loads(line).get("changeId") or last)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return last


def _export_journal(db_path: Path, conn: sqlite3.Connection) -> None:
    _mirror, journal, pos = _copy_paths(db_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    watermark = _journal_watermark(journal)
    rows = conn.execute(
        "SELECT change_id, op, at, payload_json FROM warehouse_changes WHERE change_id>? ORDER BY change_id",
        (watermark,),
    ).fetchall()
    if not rows and journal.is_file():
        return
    with journal.open("a", encoding="utf-8") as handle:
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            line = {
                "changeId": int(row["change_id"]),
                "op": row["op"],
                "at": row["at"],
                "payload": payload,
            }
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if rows:
        pos.write_text(str(int(rows[-1]["change_id"])), encoding="utf-8")
    elif not pos.is_file():
        pos.write_text("0", encoding="utf-8")


def _backup_mirror(db_path: Path, conn: sqlite3.Connection) -> None:
    mirror, _journal, _pos = _copy_paths(db_path)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(str(mirror))
    try:
        conn.backup(dest)
    finally:
        dest.close()


def _persist_copies(db_path: Path, conn: sqlite3.Connection | None = None) -> None:
    if conn is not None:
        conn.commit()
        _export_journal(db_path, conn)
        _backup_mirror(db_path, conn)
        return
    with session(db_path) as owned:
        _export_journal(db_path, owned)
        _backup_mirror(db_path, owned)


def copy_status(db_path: Path) -> dict[str, Any]:
    mirror, journal, _pos = _copy_paths(db_path)
    with session(db_path) as conn:
        last = conn.execute("SELECT COALESCE(MAX(change_id), 0) AS n FROM warehouse_changes").fetchone()
        reviewed = conn.execute("SELECT COUNT(*) AS n FROM warehouse_review").fetchone()
    last_change = int(last["n"]) if last else 0
    exported = _journal_watermark(journal)
    return {
        "live": db_path.is_file() and db_path.stat().st_size > 0,
        "mirror": mirror.is_file() and mirror.stat().st_size > 0,
        "journal": journal.is_file() and exported >= last_change,
        "journalLag": max(0, last_change - exported),
        "reviewed": int(reviewed["n"]) if reviewed else 0,
    }


def rebuild_from_journal(target: Path, journal: Path) -> int:
    """Dựng lại kho từ sổ ghi. Ghi vào file target, không đụng sổ nguồn."""
    applied = 0
    if target.exists():
        target.unlink()
    init_warehouse(target)
    with session(target) as conn:
        with journal.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = item.get("op")
                payload = item.get("payload") or {}
                at = str(item.get("at") or "")
                if op == "insert":
                    row_id = int(payload.get("row_id") or 0)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO warehouse_contacts(
                          row_id, name, name_extra, name_key, phone, phone_extra, phone_key,
                          address, address_extra, address_key, uid, uid_extra, external_id, id_extra,
                          source, batch_id, created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            row_id,
                            payload.get("name") or "",
                            payload.get("name_extra") or "",
                            payload.get("name_key") or name_key(payload.get("name") or ""),
                            payload.get("phone") or "",
                            payload.get("phone_extra") or "",
                            payload.get("phone_key") or phone_key(payload.get("phone") or ""),
                            payload.get("address") or "",
                            payload.get("address_extra") or "",
                            payload.get("address_key") or address_key(payload.get("address") or ""),
                            payload.get("uid") or "",
                            payload.get("uid_extra") or "",
                            payload.get("external_id") or "",
                            payload.get("id_extra") or "",
                            payload.get("source") or "",
                            None,
                            at,
                            at,
                        ),
                    )
                    _replace_aliases(conn, row_id, payload)
                    applied += 1
                elif op == "update":
                    row_id = int(payload.get("row_id") or 0)
                    _write_contact(conn, row_id, {**_blank_row(), **payload}, at, 0, payload.get("source") or "")
                    applied += 1
                elif op == "review":
                    conn.execute(
                        "INSERT INTO warehouse_review(at, reason, batch_id, payload_json) VALUES(?,?,?,?)",
                        (at, payload.get("reason") or "conflict", payload.get("batchId"), json.dumps(payload, ensure_ascii=False)),
                    )
                    applied += 1
    return applied


def warehouse_stats(db_path: Path) -> dict[str, Any]:
    with session(db_path) as conn:
        _ensure_meta(conn)
        counts = {
            "contacts": _meta_get(conn, "contacts"),
            "phones": _meta_get(conn, "phones"),
            "uids": _meta_get(conn, "uids"),
            "batches": _meta_get(conn, "batches"),
        }
    counts["copies"] = copy_status(db_path)
    counts["reviewed"] = int(counts["copies"]["reviewed"])
    return counts
