"""Kho tổng: lưu, ghép trùng, tìm, xuất."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from control_plane.db import session
from control_plane.warehouse import FIELDS, phone_key


def init_warehouse(db_path: Path) -> None:
    with session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS warehouse_contacts (
              row_id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              phone_key TEXT NOT NULL DEFAULT '',
              address TEXT NOT NULL DEFAULT '',
              uid TEXT NOT NULL DEFAULT '',
              external_id TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              batch_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wh_phone ON warehouse_contacts(phone_key);
            CREATE INDEX IF NOT EXISTS idx_wh_uid ON warehouse_contacts(uid);
            CREATE INDEX IF NOT EXISTS idx_wh_ext ON warehouse_contacts(external_id);
            CREATE INDEX IF NOT EXISTS idx_wh_name ON warehouse_contacts(name);
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
            """
        )


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


def _find_existing(conn: sqlite3.Connection, record: dict[str, str]) -> sqlite3.Row | None:
    uid = record.get("uid") or ""
    key = record.get("phone_key") or phone_key(record.get("phone") or "")
    external = record.get("id") or record.get("external_id") or ""
    if uid:
        row = conn.execute("SELECT * FROM warehouse_contacts WHERE uid=?", (uid,)).fetchone()
        if row:
            return row
    if key:
        row = conn.execute("SELECT * FROM warehouse_contacts WHERE phone_key=?", (key,)).fetchone()
        if row:
            return row
    if external:
        row = conn.execute("SELECT * FROM warehouse_contacts WHERE external_id=?", (external,)).fetchone()
        if row:
            return row
    return None


def _fill(old: str, incoming: str) -> str:
    return old if old else incoming


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
        seen: set[tuple[str, str, str]] = set()
        for record in records:
            name = record.get("name") or ""
            phone = record.get("phone") or ""
            key = phone_key(phone)
            address = record.get("address") or ""
            uid = record.get("uid") or ""
            external = record.get("id") or ""
            identity = (uid, key, external)
            if identity != ("", "", "") and identity in seen:
                skipped += 1
                continue
            if identity != ("", "", ""):
                seen.add(identity)
            existing = _find_existing(
                conn, {"uid": uid, "phone": phone, "phone_key": key, "id": external}
            )
            if existing:
                next_name = _fill(existing["name"], name)
                next_phone = _fill(existing["phone"], phone)
                next_key = _fill(existing["phone_key"], key)
                next_address = _fill(existing["address"], address)
                next_uid = _fill(existing["uid"], uid)
                next_ext = _fill(existing["external_id"], external)
                changed = (
                    next_name != existing["name"]
                    or next_phone != existing["phone"]
                    or next_address != existing["address"]
                    or next_uid != existing["uid"]
                    or next_ext != existing["external_id"]
                )
                if not changed:
                    skipped += 1
                    continue
                conn.execute(
                    """
                    UPDATE warehouse_contacts
                    SET name=?, phone=?, phone_key=?, address=?, uid=?, external_id=?,
                        source=?, batch_id=?, updated_at=?
                    WHERE row_id=?
                    """,
                    (
                        next_name,
                        next_phone,
                        next_key,
                        next_address,
                        next_uid,
                        next_ext,
                        source or existing["source"],
                        batch_id,
                        at,
                        existing["row_id"],
                    ),
                )
                updated += 1
                continue
            conn.execute(
                """
                INSERT INTO warehouse_contacts(
                  name, phone, phone_key, address, uid, external_id, source, batch_id, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (name, phone, key, address, uid, external, source, batch_id, at, at),
            )
            inserted += 1
        conn.execute(
            """
            UPDATE warehouse_batches
            SET inserted=?, updated=?, skipped=?
            WHERE batch_id=?
            """,
            (inserted, updated, skipped, batch_id),
        )
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "batchId": batch_id}


def search_contacts(
    db_path: Path,
    query: str,
    *,
    limit: int,
    after: int,
) -> dict[str, Any]:
    q = (query or "").strip()
    params: list[object] = []
    where = "row_id > ?"
    params.append(after)
    if q:
        key = phone_key(q)
        where += (
            " AND (name LIKE ? OR phone LIKE ? OR phone_key = ? OR address LIKE ?"
            " OR uid = ? OR external_id = ? OR uid LIKE ? OR external_id LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, key, like, q, q, f"{q}%", f"{q}%"])
    sql = (
        "SELECT row_id, name, phone, address, uid, external_id, source, updated_at "
        f"FROM warehouse_contacts WHERE {where} ORDER BY row_id ASC LIMIT ?"
    )
    params.append(limit + 1)
    with session(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts").fetchone()["n"]
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


def export_csv(db_path: Path, query: str) -> bytes:
    result = search_contacts(db_path, query, limit=200_000, after=0)
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["Tên", "Số điện thoại", "Địa chỉ", "UID", "ID"])
    for item in result["items"]:
        writer.writerow([item["name"], item["phone"], item["address"], item["uid"], item["externalId"]])
    return buf.getvalue().encode("utf-8")


def warehouse_stats(db_path: Path) -> dict[str, int]:
    with session(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts").fetchone()["n"]
        phones = conn.execute(
            "SELECT COUNT(*) AS n FROM warehouse_contacts WHERE phone_key != ''"
        ).fetchone()["n"]
        uids = conn.execute("SELECT COUNT(*) AS n FROM warehouse_contacts WHERE uid != ''").fetchone()["n"]
        batches = conn.execute("SELECT COUNT(*) AS n FROM warehouse_batches").fetchone()["n"]
    return {"contacts": int(total), "phones": int(phones), "uids": int(uids), "batches": int(batches)}
