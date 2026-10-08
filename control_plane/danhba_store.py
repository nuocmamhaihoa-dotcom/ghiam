"""Danh bạ trên hub: mỗi người một tên và một số, mỗi cuốn 5000 số, cuốn đã dùng tách riêng."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from control_plane import db

PAGE_SIZE = 5000
MAX_LINES = 200_000
MAX_CHARS = 20_000_000
_HEADER_WORDS = {
    "name",
    "ten",
    "tên",
    "ho ten",
    "họ tên",
    "hoten",
    "họ và tên",
    "phone",
    "sdt",
    "sđt",
    "so dien thoai",
    "số điện thoại",
    "tel",
    "mobile",
    "điện thoại",
    "ten ghi nho",
    "tên ghi nhớ",
}
_BOOK_ID = re.compile(r"^book-(\d+)$")
_TICKET = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_TEL_LINE = re.compile(r"^(?:[A-Za-z0-9-]+\.)?TEL(?:;[^:]*)?:(.*)$", re.IGNORECASE)
_EXPORT_TTL = timedelta(minutes=10)


def clean_name(value: str) -> str:
    collapsed = " ".join(str(value or "").split())
    return collapsed[:80]


def clean_title(value: str) -> str:
    collapsed = " ".join(str(value or "").split())
    return collapsed[:40]


def name_key(value: str) -> str:
    return clean_name(value).casefold()


def normalize_phone(raw: str) -> str | None:
    text = str(raw or "").strip()
    if text.lower().startswith("tel:"):
        text = text[4:].strip()
    if not text:
        return None
    digits: list[str] = []
    leading_plus = False
    started = False
    for character in text:
        if not started and character == "+":
            leading_plus = True
            started = True
            continue
        started = True
        if character.isdigit():
            digits.append(character)
            continue
        if character in " -().\t":
            continue
        break
    number = "".join(digits)
    if len(number) < 8 or len(number) > 15:
        return None
    if len(number) == 11 and number.startswith("84"):
        return "0" + number[2:]
    if leading_plus:
        return "+" + number
    return number


def _split_csv(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    in_quotes = False
    index = 0
    while index < len(line):
        character = line[index]
        if in_quotes:
            if character == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                in_quotes = False
            else:
                current.append(character)
        elif character == '"' and not current:
            in_quotes = True
        elif character in ",;\t":
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    fields.append("".join(current).strip())
    return fields


def _is_header(fields: list[str]) -> bool:
    if len(fields) < 2:
        return False
    return all(name_key(field) in _HEADER_WORDS for field in fields)


def _people_from_line(line: str, fields: list[str]) -> list[dict[str, str]]:
    phones: list[str] = []
    name_parts: list[str] = []
    for field in fields:
        phone = normalize_phone(field)
        if phone:
            phones.append(phone)
            continue
        piece = clean_name(field)
        if piece:
            name_parts.append(piece)
    if not phones:
        parts = [part for part in line.split() if part]
        if len(parts) >= 2:
            tail = normalize_phone(parts[-1])
            head = clean_name(" ".join(parts[:-1]))
            if tail and head:
                return [{"name": head, "phone": tail}]
        whole = normalize_phone(line)
        if whole:
            return [{"name": whole, "phone": whole}]
        return []
    name = clean_name(" ".join(name_parts))
    return [{"name": name or phone, "phone": phone} for phone in phones]


def parse_people(text: str) -> tuple[list[dict[str, str]], int, bool]:
    """Mỗi dòng một người: tên ghi nhớ và một số. Số trùng trong danh sách chỉ giữ lần đầu."""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    lines = text.lstrip("\ufeff").splitlines()
    people: list[dict[str, str]] = []
    seen: set[str] = set()
    duplicates = 0
    truncated = len(lines) > MAX_LINES
    for line in lines[:MAX_LINES]:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        fields = _split_csv(trimmed)
        if _is_header(fields):
            continue
        for person in _people_from_line(trimmed, fields):
            phone = person["phone"]
            if phone in seen:
                duplicates += 1
                continue
            seen.add(phone)
            people.append(person)
    return people, duplicates, truncated


def _book_row(row: sqlite3.Row, count: int) -> dict[str, object]:
    item: dict[str, object] = {
        "id": row["id"],
        "name": row["name"],
        "count": count,
        "status": row["status"],
    }
    used_at = row["used_at"]
    if used_at:
        item["usedAt"] = used_at
    return item


def list_books(db_path: Path) -> dict[str, object]:
    ready: list[dict[str, object]] = []
    used: list[dict[str, object]] = []
    with db.session(db_path) as conn:
        rows = conn.execute("SELECT id, name, seq, status, used_at FROM contact_books ORDER BY seq").fetchall()
        counts = {
            row["book_id"]: int(row["n"])
            for row in conn.execute(
                "SELECT book_id, COUNT(*) AS n FROM contact_entries GROUP BY book_id"
            ).fetchall()
        }
    for row in rows:
        item = _book_row(row, counts.get(row["id"], 0))
        if row["status"] == "used":
            used.append(item)
        else:
            ready.append(item)
    return {"ready": ready, "used": used, "revision": _revision(ready + used)}


def get_book(db_path: Path, book_id: str) -> dict[str, object] | None:
    if not _BOOK_ID.match(book_id):
        return None
    with db.session(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, status, used_at FROM contact_books WHERE id=?",
            (book_id,),
        ).fetchone()
        if row is None:
            return None
        entries = conn.execute(
            "SELECT name, phone FROM contact_entries WHERE book_id=? ORDER BY rowid",
            (book_id,),
        ).fetchall()
    item = _book_row(row, len(entries))
    item["entries"] = [{"name": entry["name"], "phone": entry["phone"]} for entry in entries]
    return item


def unfold_vcard(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", normalized)


def _tel_phones(text: str) -> set[str]:
    found: set[str] = set()
    for line in unfold_vcard(text).split("\n"):
        match = _TEL_LINE.match(line.strip())
        if match is None:
            continue
        phone = normalize_phone(match.group(1))
        if phone:
            found.add(phone)
    return found


def phones_in_text(text: str) -> set[str]:
    """Số trong danh sách thường, hoặc TEL của vCard. +84 và 090 là một số."""
    if "BEGIN:VCARD" in text.upper():
        return _tel_phones(text)
    found = _tel_phones(text)
    people, _, _ = parse_people(text)
    for person in people:
        found.add(str(person["phone"]))
    return found


def reconcile(
    db_path: Path,
    text: str,
    extra_phones: list[str],
    full: bool,
    used_at: str,
) -> dict[str, object]:
    """Khớp số đang có trên iPhone với từng danh bạ. Không thêm số lạ vào hub."""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    seen = phones_in_text(text)
    for raw in extra_phones[:MAX_LINES]:
        phone = normalize_phone(str(raw))
        if phone:
            seen.add(phone)
    if not seen:
        raise ValueError("empty")
    moved_used = 0
    moved_ready = 0
    reports: list[dict[str, object]] = []
    with db.session(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, seq, status, used_at FROM contact_books ORDER BY seq"
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for entry in conn.execute("SELECT book_id, phone FROM contact_entries").fetchall():
            grouped.setdefault(str(entry["book_id"]), []).append(str(entry["phone"]))
        known: set[str] = set()
        for row in rows:
            phones = grouped.get(str(row["id"]), [])
            known.update(phones)
            matched = sum(1 for phone in phones if phone in seen)
            status = str(row["status"])
            if phones and matched == len(phones) and status != "used":
                conn.execute(
                    "UPDATE contact_books SET status='used', used_at=? WHERE id=?",
                    (used_at, row["id"]),
                )
                status = "used"
                moved_used += 1
            elif full and phones and matched == 0 and status == "used":
                conn.execute(
                    "UPDATE contact_books SET status='ready', used_at=NULL WHERE id=?",
                    (row["id"],),
                )
                status = "ready"
                moved_ready += 1
            reports.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "count": len(phones),
                    "matched": matched,
                    "status": status,
                }
            )
    listed = list_books(db_path)
    return {
        "seenPhones": len(seen),
        "unknownPhones": len(seen - known),
        "movedToUsed": moved_used,
        "movedToReady": moved_ready,
        "books": reports,
        "ready": listed["ready"],
        "used": listed["used"],
        "revision": listed["revision"],
    }


def escape_vcard(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def render_vcard(books: list[dict[str, object]]) -> str:
    """Một file .vcf. ORG và NOTE giữ tên từng danh bạ. Một số chỉ xuất hiện một lần."""
    lines: list[str] = []
    for book in books:
        group = escape_vcard(str(book["name"]))
        entries = book.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            phone = str(entry.get("phone") or "")
            name = escape_vcard(str(entry.get("name") or phone))
            lines.extend(
                [
                    "BEGIN:VCARD",
                    "VERSION:3.0",
                    f"N:{name};;;;",
                    f"FN:{name}",
                    f"ORG:{group}",
                    f"TEL;TYPE=CELL:{phone}",
                    f"NOTE:{group}. {escape_vcard('Bấm nút chia sẻ góc trên. Chọn Danh bạ. Bấm Thêm tất cả.')}",
                    "END:VCARD",
                ]
            )
    if not lines:
        return ""
    return "\r\n".join(lines) + "\r\n"


def _safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isascii() and (ch.isalnum() or ch in " -_") else " " for ch in name)
    cleaned = " ".join(cleaned.split())
    return (cleaned or "Danh ba") + ".vcf"


def _book_entries(conn: sqlite3.Connection, book_id: str) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT name, phone FROM contact_entries WHERE book_id=? ORDER BY rowid",
        (book_id,),
    ).fetchall()
    return [{"name": str(row["name"]), "phone": str(row["phone"])} for row in rows]


def issue_vcard(db_path: Path, book_id: str | None, created_at: str) -> dict[str, object] | None:
    """Vé ngắn hạn để Safari mở file .vcf. Không trả số điện thoại."""
    moment = datetime.fromisoformat(created_at)
    expires_at = (moment + _EXPORT_TTL).isoformat()
    with db.session(db_path) as conn:
        conn.execute("DELETE FROM contact_exports WHERE expires_at <= ?", (created_at,))
        if book_id:
            if not _BOOK_ID.match(book_id):
                return None
            rows = conn.execute(
                "SELECT id, name FROM contact_books WHERE id=? AND status='ready'",
                (book_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name FROM contact_books WHERE status='ready' ORDER BY seq"
            ).fetchall()
        chosen: list[dict[str, object]] = []
        for row in rows:
            entries = _book_entries(conn, str(row["id"]))
            if not entries:
                continue
            chosen.append({"id": str(row["id"]), "name": str(row["name"]), "count": len(entries)})
        if not chosen:
            return None
        ticket = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO contact_exports(ticket, book_ids, expires_at) VALUES(?,?,?)",
            (ticket, ",".join(str(item["id"]) for item in chosen), expires_at),
        )
    filename = _safe_filename(str(chosen[0]["name"])) if len(chosen) == 1 else "Danh ba.vcf"
    return {
        "url": f"/danhba/xuat/{ticket}.vcf",
        "filename": filename,
        "count": sum(int(item["count"]) for item in chosen),
        "books": chosen,
    }


def read_vcard(db_path: Path, ticket: str, now: str) -> dict[str, str] | None:
    """Đọc vé và chuyển các danh bạ đó sang đã dùng. Lần mở lại trong hạn vẫn trả cùng file."""
    if not _TICKET.match(ticket):
        return None
    moment = datetime.fromisoformat(now)
    with db.session(db_path) as conn:
        row = conn.execute(
            "SELECT book_ids, expires_at FROM contact_exports WHERE ticket=?",
            (ticket,),
        ).fetchone()
        if row is None or moment > datetime.fromisoformat(str(row["expires_at"])):
            return None
        books: list[dict[str, object]] = []
        for book_id in str(row["book_ids"]).split(","):
            if not _BOOK_ID.match(book_id):
                continue
            stored = conn.execute(
                "SELECT id, name, status FROM contact_books WHERE id=?",
                (book_id,),
            ).fetchone()
            if stored is None:
                continue
            entries = _book_entries(conn, book_id)
            if not entries:
                continue
            if stored["status"] != "used":
                conn.execute(
                    "UPDATE contact_books SET status='used', used_at=? WHERE id=?",
                    (now, book_id),
                )
            books.append({"name": str(stored["name"]), "entries": entries})
    body = render_vcard(books)
    if not body:
        return None
    filename = _safe_filename(str(books[0]["name"])) if len(books) == 1 else "Danh ba.vcf"
    return {"filename": filename, "body": body}


def mark_used(db_path: Path, book_id: str, used_at: str) -> dict[str, object] | None:
    if not _BOOK_ID.match(book_id):
        return None
    with db.session(db_path) as conn:
        row = conn.execute("SELECT id, status FROM contact_books WHERE id=?", (book_id,)).fetchone()
        if row is None:
            return None
        if row["status"] != "used":
            conn.execute(
                "UPDATE contact_books SET status='used', used_at=? WHERE id=?",
                (used_at, book_id),
            )
    book = get_book(db_path, book_id)
    if book is None:
        return None
    book.pop("entries", None)
    return book


def import_people(
    db_path: Path,
    text: str,
    title: str,
    created_at: str,
    *,
    page_size: int = PAGE_SIZE,
) -> dict[str, object]:
    """Thêm người mới vào danh bạ chưa dùng. Danh bạ đã dùng không nhận thêm số."""
    if page_size < 1:
        raise ValueError("page size")
    people, duplicates, truncated = parse_people(text)
    base = clean_title(title) or "Khach"
    added = 0
    skipped_existing = 0
    with db.session(db_path) as conn:
        existing = _existing_phones(conn, [person["phone"] for person in people])
        fresh = []
        for person in people:
            if person["phone"] in existing:
                skipped_existing += 1
                continue
            fresh.append(person)
        if fresh:
            slots = _open_slots(conn, page_size)
            seq = _next_seq(conn)
            batch: list[tuple[str, str, str]] = []
            for person in fresh:
                if not slots:
                    book_id = f"book-{seq}"
                    conn.execute(
                        """
                        INSERT INTO contact_books(id, name, seq, status, created_at, used_at)
                        VALUES(?,?,?,'ready',?,NULL)
                        """,
                        (book_id, f"{base} {seq}", seq, created_at),
                    )
                    slots.append([book_id, page_size])
                    seq += 1
                book_id = str(slots[0][0])
                slots[0][1] = int(slots[0][1]) - 1
                if int(slots[0][1]) <= 0:
                    slots.pop(0)
                batch.append((person["phone"], book_id, person["name"]))
                added += 1
            conn.executemany(
                "INSERT INTO contact_entries(phone, book_id, name) VALUES(?,?,?)",
                batch,
            )
    listed = list_books(db_path)
    return {
        "added": added,
        "duplicatePhones": duplicates,
        "skippedExisting": skipped_existing,
        "truncated": truncated,
        "ready": listed["ready"],
        "used": listed["used"],
    }


def _revision(items: list[dict[str, object]]) -> str:
    parts = [
        f"{item['id']}|{item['status']}|{item['count']}|{item.get('usedAt', '')}"
        for item in items
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _existing_phones(conn: sqlite3.Connection, phones: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(phones), 400):
        chunk = phones[start : start + 400]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT phone FROM contact_entries WHERE phone IN ({marks})",
            chunk,
        ).fetchall()
        found.update(row["phone"] for row in rows)
    return found


def _next_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS n FROM contact_books").fetchone()
    if row is None:
        return 1
    return int(row["n"]) + 1


def _open_slots(conn: sqlite3.Connection, page_size: int) -> list[list[object]]:
    rows = conn.execute(
        """
        SELECT b.id AS id,
               (SELECT COUNT(*) FROM contact_entries e WHERE e.book_id = b.id) AS n
        FROM contact_books b
        WHERE b.status = 'ready'
        ORDER BY b.seq
        """
    ).fetchall()
    slots: list[list[object]] = []
    for row in rows:
        room = page_size - int(row["n"])
        if room > 0:
            slots.append([row["id"], room])
    return slots
