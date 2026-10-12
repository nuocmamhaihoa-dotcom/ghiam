"""Kho tổng: lưu, ghép trùng, tìm, xuất — tra cứu theo chỉ mục, không tải cả bảng vào RAM."""

from __future__ import annotations

import csv
import io
import json
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
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(warehouse_contacts)")}
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
        _seed_meta_rows(conn)
        _install_count_triggers(conn)
        _backfill_keys(conn)
        _ensure_meta(conn)


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


def _row_payload(row: sqlite3.Row | dict[str, str]) -> dict[str, str]:
    keys = row.keys() if hasattr(row, "keys") else row
    return {
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


def _id_hits(conn: sqlite3.Connection, kind: str, value: str) -> list[int]:
    column = _LOOKUP_COLS.get(kind)
    if not column or not value:
        return []
    rows = conn.execute(
        f"SELECT row_id FROM warehouse_contacts WHERE {column} = ? LIMIT 2",
        (value,),
    ).fetchall()
    return [int(row["row_id"]) for row in rows]


def _get_row(conn: sqlite3.Connection, row_id: int) -> dict[str, str] | None:
    row = conn.execute(
        "SELECT row_id, name, name_key, phone, phone_key, address, address_key, uid, external_id, source "
        "FROM warehouse_contacts WHERE row_id=?",
        (row_id,),
    ).fetchone()
    return _row_payload(row) if row else None


def _strong_keys_agree(existing: dict[str, str], record: dict[str, str]) -> bool:
    incoming_uid = record.get("uid") or ""
    incoming_phone = record.get("phone_key") or phone_key(record.get("phone") or "")
    incoming_id = id_key(record.get("id") or record.get("external_id") or "")
    if incoming_uid and existing["uid"] and incoming_uid != existing["uid"]:
        return False
    if incoming_phone and existing["phone_key"] and incoming_phone != existing["phone_key"]:
        return False
    if incoming_id and id_key(existing["external_id"]) and incoming_id != id_key(existing["external_id"]):
        return False
    return True


def find_match(conn: sqlite3.Connection, record: dict[str, str]) -> tuple[int | None, str]:
    """Trùng ô độc nhất thì lấy hàng đó. Tra cứu chỉ mục, không quét cả kho."""
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
    weak_ambiguous = False
    if person and len(person.split()) >= 2:
        name_hits = _id_hits(conn, "name", person)
        if len(name_hits) == 1:
            hits.append((name_hits[0], "name"))
        elif len(name_hits) > 1:
            weak_ambiguous = True
    if place and len(place) >= 16:
        place_hits = _id_hits(conn, "address", place)
        if len(place_hits) == 1:
            hits.append((place_hits[0], "address"))
        elif len(place_hits) > 1:
            weak_ambiguous = True
    row_ids = {row_id for row_id, _reason in hits}
    if len(row_ids) > 1:
        return None, "conflict"
    if len(row_ids) == 1:
        row_id, reason = hits[0]
        existing = _get_row(conn, row_id)
        if existing is None:
            return None, ""
        if reason in {"name", "address"} and not _strong_keys_agree(existing, record):
            return None, ""
        return row_id, reason
    if weak_ambiguous:
        return None, "ambiguous"
    return None, ""


def _fill(old: str, incoming: str) -> str:
    return old if old else incoming


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
    with session(db_path) as conn:
        conn.execute(
            """
            INSERT INTO warehouse_batches(at, filename, source, inserted, updated, skipped, mapping_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (at, filename, source, 0, 0, 0, json.dumps(mapping, ensure_ascii=False)),
        )
        batch_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        done = 0
        for record in records:
            name = record.get("name") or ""
            phone = record.get("phone") or ""
            key = phone_key(phone)
            address = record.get("address") or ""
            uid = record.get("uid") or ""
            external = id_key(record.get("id") or "")
            person = name_key(name)
            place = address_key(address)
            existing_id, reason = find_match(
                conn,
                {
                    "uid": uid,
                    "phone": phone,
                    "phone_key": key,
                    "id": external,
                    "name": name,
                    "address": address,
                },
            )
            if reason in {"ambiguous", "conflict"}:
                skipped += 1
            elif existing_id:
                existing = _get_row(conn, existing_id)
                if existing is None:
                    skipped += 1
                else:
                    next_name = _fill(existing["name"], name)
                    next_phone = _fill(existing["phone"], phone)
                    next_key = _fill(existing["phone_key"], key)
                    next_address = _fill(existing["address"], address)
                    next_uid = _fill(existing["uid"], uid)
                    next_ext = _fill(existing["external_id"], external)
                    next_person = name_key(next_name)
                    next_place = address_key(next_address)
                    changed = (
                        next_name != existing["name"]
                        or next_phone != existing["phone"]
                        or next_address != existing["address"]
                        or next_uid != existing["uid"]
                        or next_ext != existing["external_id"]
                    )
                    if not changed:
                        skipped += 1
                    else:
                        conn.execute(
                            """
                            UPDATE warehouse_contacts
                            SET name=?, name_key=?, phone=?, phone_key=?, address=?, address_key=?,
                                uid=?, external_id=?, source=?, batch_id=?, updated_at=?
                            WHERE row_id=?
                            """,
                            (
                                next_name,
                                next_person,
                                next_phone,
                                next_key,
                                next_address,
                                next_place,
                                next_uid,
                                next_ext,
                                source or existing["source"],
                                batch_id,
                                at,
                                existing_id,
                            ),
                        )
                        updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO warehouse_contacts(
                      name, name_key, phone, phone_key, address, address_key, uid, external_id,
                      source, batch_id, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (name, person, phone, key, address, place, uid, external, source, batch_id, at, at),
                )
                inserted += 1
            done += 1
            if done % FLUSH_EVERY == 0:
                conn.commit()
        conn.execute(
            """
            UPDATE warehouse_batches
            SET inserted=?, updated=?, skipped=?
            WHERE batch_id=?
            """,
            (inserted, updated, skipped, batch_id),
        )
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "batchId": batch_id}


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
        clauses.append("phone_key = ?")
        params.append(key)
    digits = "".join(ch for ch in q if ch.isdigit())
    if 5 <= len(digits) <= 20:
        clauses.append("uid = ?")
        params.append(digits)
    if ext.startswith("@") or (ext and ext != q and ext != key):
        clauses.append("external_id = ?")
        params.append(ext)
    elif q.startswith("@"):
        clauses.append("external_id = ?")
        params.append(q.casefold())
    if person:
        clauses.append("name_key = ?")
        params.append(person)
        clauses.append("name_key LIKE ?")
        params.append(person + "%")
    if place:
        clauses.append("address_key = ?")
        params.append(place)
        if len(place) >= 4:
            clauses.append("address_key LIKE ?")
            params.append(place + "%")
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
        "SELECT row_id, name, phone, address, uid, external_id, source, updated_at "
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
            "phone": row["phone"],
            "address": row["address"],
            "uid": row["uid"],
            "externalId": row["external_id"],
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
    writer.writerow(["Tên", "Số điện thoại", "Địa chỉ", "UID Facebook", "ID TikTok"])
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
                    [row["name"], row["phone"], row["address"], row["uid"], row["external_id"]]
                )
            yield buf.getvalue().encode("utf-8")
            after = int(rows[-1]["row_id"])


def export_csv(db_path: Path, query: str) -> bytes:
    return b"".join(export_csv_rows(db_path, query))


def warehouse_stats(db_path: Path) -> dict[str, int]:
    with session(db_path) as conn:
        _ensure_meta(conn)
        return {
            "contacts": _meta_get(conn, "contacts"),
            "phones": _meta_get(conn, "phones"),
            "uids": _meta_get(conn, "uids"),
            "batches": _meta_get(conn, "batches"),
        }
