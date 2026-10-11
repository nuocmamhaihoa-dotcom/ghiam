"""Kho tổng: lưu, ghép trùng, tìm, xuất."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from control_plane.db import session
from control_plane.warehouse import FIELDS, address_key, id_key, name_key, phone_key


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
            CREATE INDEX IF NOT EXISTS idx_wh_phone ON warehouse_contacts(phone_key);
            CREATE INDEX IF NOT EXISTS idx_wh_uid ON warehouse_contacts(uid);
            CREATE INDEX IF NOT EXISTS idx_wh_ext ON warehouse_contacts(external_id);
            CREATE INDEX IF NOT EXISTS idx_wh_name ON warehouse_contacts(name);
            CREATE INDEX IF NOT EXISTS idx_wh_name_key ON warehouse_contacts(name_key);
            CREATE INDEX IF NOT EXISTS idx_wh_addr_key ON warehouse_contacts(address_key);
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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(warehouse_contacts)")}
        if "name_key" not in columns:
            conn.execute("ALTER TABLE warehouse_contacts ADD COLUMN name_key TEXT NOT NULL DEFAULT ''")
        if "address_key" not in columns:
            conn.execute("ALTER TABLE warehouse_contacts ADD COLUMN address_key TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_name_key ON warehouse_contacts(name_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_addr_key ON warehouse_contacts(address_key)")
        missing = conn.execute(
            "SELECT row_id, name, address FROM warehouse_contacts WHERE name_key='' OR address_key=''"
        ).fetchall()
        for row in missing:
            conn.execute(
                "UPDATE warehouse_contacts SET name_key=?, address_key=? WHERE row_id=?",
                (name_key(row["name"]), address_key(row["address"]), row["row_id"]),
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


class _Lookups:
    def __init__(self) -> None:
        self.by_id: dict[int, dict[str, str]] = {}
        self.uid: dict[str, int] = {}
        self.phone: dict[str, int] = {}
        self.ext: dict[str, int] = {}
        self.name: dict[str, list[int]] = {}
        self.address: dict[str, list[int]] = {}


def _row_payload(row: sqlite3.Row | dict[str, str]) -> dict[str, str]:
    return {
        "row_id": str(row["row_id"]),
        "name": row["name"] or "",
        "name_key": row["name_key"] if "name_key" in row.keys() else name_key(row["name"] or ""),
        "phone": row["phone"] or "",
        "phone_key": row["phone_key"] or "",
        "address": row["address"] or "",
        "address_key": row["address_key"] if "address_key" in row.keys() else address_key(row["address"] or ""),
        "uid": row["uid"] or "",
        "external_id": row["external_id"] or "",
        "source": row["source"] or "",
    }


def _index_row(lookups: _Lookups, payload: dict[str, str]) -> None:
    row_id = int(payload["row_id"])
    lookups.by_id[row_id] = payload
    if payload["uid"]:
        lookups.uid[payload["uid"]] = row_id
    if payload["phone_key"]:
        lookups.phone[payload["phone_key"]] = row_id
    if payload["external_id"]:
        lookups.ext[id_key(payload["external_id"])] = row_id
    if payload["name_key"]:
        lookups.name.setdefault(payload["name_key"], [])
        if row_id not in lookups.name[payload["name_key"]]:
            lookups.name[payload["name_key"]].append(row_id)
    if payload["address_key"]:
        lookups.address.setdefault(payload["address_key"], [])
        if row_id not in lookups.address[payload["address_key"]]:
            lookups.address[payload["address_key"]].append(row_id)


def load_lookups(conn: sqlite3.Connection) -> _Lookups:
    lookups = _Lookups()
    rows = conn.execute(
        "SELECT row_id, name, name_key, phone, phone_key, address, address_key, uid, external_id, source "
        "FROM warehouse_contacts"
    ).fetchall()
    for row in rows:
        _index_row(lookups, _row_payload(row))
    return lookups


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


def find_match(lookups: _Lookups, record: dict[str, str]) -> tuple[int | None, str]:
    """Trùng ô độc nhất thì lấy hàng đó. Nhiều ô trỏ hai người khác nhau thì bỏ qua."""
    uid = record.get("uid") or ""
    key = record.get("phone_key") or phone_key(record.get("phone") or "")
    external = id_key(record.get("id") or record.get("external_id") or "")
    person = name_key(record.get("name") or "")
    place = address_key(record.get("address") or "")
    hits: list[tuple[int, str]] = []
    if uid and uid in lookups.uid:
        hits.append((lookups.uid[uid], "uid"))
    if key and key in lookups.phone:
        hits.append((lookups.phone[key], "phone"))
    if external and external in lookups.ext:
        hits.append((lookups.ext[external], "id"))
    weak_ambiguous = False
    if person and len(person.split()) >= 2:
        name_hits = lookups.name.get(person) or []
        if len(name_hits) == 1:
            hits.append((name_hits[0], "name"))
        elif len(name_hits) > 1:
            weak_ambiguous = True
    if place and len(place) >= 16:
        place_hits = lookups.address.get(place) or []
        if len(place_hits) == 1:
            hits.append((place_hits[0], "address"))
        elif len(place_hits) > 1:
            weak_ambiguous = True
    row_ids = {row_id for row_id, _reason in hits}
    if len(row_ids) > 1:
        return None, "conflict"
    if len(row_ids) == 1:
        row_id, reason = hits[0]
        existing = lookups.by_id[row_id]
        if reason in {"name", "address"} and not _strong_keys_agree(existing, record):
            return None, ""
        return row_id, reason
    if weak_ambiguous:
        return None, "ambiguous"
    return None, ""


def _fill(old: str, incoming: str) -> str:
    return old if old else incoming


def preview_matches(db_path: Path, records: list[dict[str, str]]) -> list[dict[str, str]]:
    with session(db_path) as conn:
        lookups = load_lookups(conn)
    out: list[dict[str, str]] = []
    for record in records:
        row_id, reason = find_match(lookups, {**record, "phone_key": phone_key(record.get("phone") or "")})
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
        lookups = load_lookups(conn)
        seen: set[tuple[str, str, str]] = set()
        for record in records:
            name = record.get("name") or ""
            phone = record.get("phone") or ""
            key = phone_key(phone)
            address = record.get("address") or ""
            uid = record.get("uid") or ""
            external = id_key(record.get("id") or "")
            person = name_key(name)
            place = address_key(address)
            identity = (uid, key, external)
            if identity != ("", "", "") and identity in seen:
                skipped += 1
                continue
            if identity != ("", "", ""):
                seen.add(identity)
            existing_id, reason = find_match(
                lookups,
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
                continue
            if existing_id:
                existing = lookups.by_id[existing_id]
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
                    continue
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
                _index_row(
                    lookups,
                    {
                        "row_id": str(existing_id),
                        "name": next_name,
                        "name_key": next_person,
                        "phone": next_phone,
                        "phone_key": next_key,
                        "address": next_address,
                        "address_key": next_place,
                        "uid": next_uid,
                        "external_id": next_ext,
                        "source": source or existing["source"],
                    },
                )
                updated += 1
                continue
            conn.execute(
                """
                INSERT INTO warehouse_contacts(
                  name, name_key, phone, phone_key, address, address_key, uid, external_id,
                  source, batch_id, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (name, person, phone, key, address, place, uid, external, source, batch_id, at, at),
            )
            new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            _index_row(
                lookups,
                {
                    "row_id": str(new_id),
                    "name": name,
                    "name_key": person,
                    "phone": phone,
                    "phone_key": key,
                    "address": address,
                    "address_key": place,
                    "uid": uid,
                    "external_id": external,
                    "source": source,
                },
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
    writer.writerow(["Tên", "Số điện thoại", "Địa chỉ", "UID Facebook", "ID TikTok"])
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
