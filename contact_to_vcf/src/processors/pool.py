"""Persistent phone pool. A number stays in the book it was first assigned to."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from exporters.vcf_validator import contact_label, format_card
from models.records import JobConfig
from parsers.detect import detect_encoding, open_reader, prepare_source
from processors.phone_normalizer import canonical_phones, storage_forms, to_vietnam_10

CONTACTS_PER_FILE = 4000


@dataclass(frozen=True)
class ImportStats:
    seen: int
    added: int
    duplicate: int
    rejected: int


@dataclass(frozen=True)
class BookInfo:
    id: int
    contact_count: int
    created_at: str
    downloaded_at: str | None

    @property
    def file_name(self) -> str:
        return f"danhba_{self.id:05d}.vcf"


def pool_path(folder: Path) -> Path:
    return folder / "kho.sqlite"


def _phones_on_record(columns: tuple[str, ...], file_format: str, phone_column: int) -> list[str]:
    """TXT keeps every number on the line. Other files use the chosen column."""
    cells = list(columns) if file_format == "txt" else []
    if file_format != "txt" and 0 <= phone_column < len(columns):
        cells = [columns[phone_column]]
    found: list[str] = []
    seen: set[str] = set()
    for cell in cells:
        for phone in canonical_phones(cell):
            if phone not in seen:
                seen.add(phone)
                found.append(phone)
    return found


def import_file(
    folder: Path,
    source: Path,
    *,
    file_format: str,
    delimiter: str,
    has_header: bool,
    encoding: str,
    phone_column: int,
    contacts_per_file: int = CONTACTS_PER_FILE,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> ImportStats:
    """Add phones from one source. Existing phones stay in their original book."""
    if file_format == "txt":
        source = prepare_source(source)
        encoding = detect_encoding(source)
    config = JobConfig(
        input_path=source,
        output_dir=folder,
        file_format=file_format,
        delimiter=delimiter,
        has_header=has_header,
        name_column=0,
        phone_column=phone_column,
        contacts_per_file=contacts_per_file,
        encoding=encoding,
    )
    reader = open_reader(config)
    stream = reader.iter_stream(0, 1)
    seen = 0
    added = 0
    duplicate = 0
    rejected = 0
    stats = ImportStats(0, 0, 0, 0)
    batch: list[str] = []
    pool = _Pool(pool_path(folder), contacts_per_file)

    def flush_batch() -> None:
        nonlocal added, duplicate
        if not batch:
            return
        new_count, duplicate_count = pool.add_many(batch, source.name)
        added += new_count
        duplicate += duplicate_count
        batch.clear()

    try:
        for record in stream:
            if should_stop is not None and should_stop():
                break
            phones = _phones_on_record(record.columns, file_format, phone_column)
            seen += 1
            if not phones:
                rejected += 1
            else:
                batch.extend(phones)
                if len(batch) >= 4000:
                    flush_batch()
            if on_progress is not None and seen % 4000 == 0:
                on_progress(seen)
        flush_batch()
        pool.finish()
        stats = ImportStats(seen, added, duplicate, rejected)
    finally:
        pool.close()
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if on_progress is not None:
        on_progress(stats.seen)
    return stats


def list_books(folder: Path, status: str = "all") -> list[BookInfo]:
    if not pool_path(folder).is_file():
        return []
    connection = _connect(pool_path(folder))
    try:
        _ensure_schema(connection)
        query = "SELECT id, contact_count, created_at, downloaded_at FROM books"
        if status == "pending":
            query += " WHERE downloaded_at IS NULL"
        elif status == "downloaded":
            query += " WHERE downloaded_at IS NOT NULL"
        query += " ORDER BY id"
        rows = connection.execute(query).fetchall()
    finally:
        connection.close()
    return [
        BookInfo(
            id=int(row["id"]),
            contact_count=int(row["contact_count"]),
            created_at=str(row["created_at"]),
            downloaded_at=None if row["downloaded_at"] is None else str(row["downloaded_at"]),
        )
        for row in rows
    ]


def pool_total(folder: Path) -> int:
    if not pool_path(folder).is_file():
        return 0
    connection = _connect(pool_path(folder))
    try:
        _ensure_schema(connection)
        row = connection.execute("SELECT COUNT(*) AS total FROM numbers").fetchone()
    finally:
        connection.close()
    if row is None:
        return 0
    return int(row["total"])


def export_book(folder: Path, book_id: int, destination: Path) -> int:
    """Write one book and record the download time. Membership does not change."""
    connection = _connect(pool_path(folder))
    try:
        _ensure_schema(connection)
        rows = connection.execute(
            "SELECT phone FROM numbers WHERE book_id = ? ORDER BY rowid",
            (book_id,),
        ).fetchall()
        if not rows:
            raise ValueError("Danh bạ này không còn số để tải")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as handle:
            for row in rows:
                phone = contact_label(str(row["phone"]))
                handle.write(format_card(phone, phone))
        downloaded_at = _now()
        connection.execute(
            "UPDATE books SET downloaded_at = ? WHERE id = ?",
            (downloaded_at, book_id),
        )
        connection.commit()
    finally:
        connection.close()
    return len(rows)


def book_of(folder: Path, phone: str) -> int | None:
    if not pool_path(folder).is_file():
        return None
    connection = _connect(pool_path(folder))
    try:
        _ensure_schema(connection)
        stored, alt = storage_forms(phone)
        row = connection.execute(
            "SELECT book_id FROM numbers WHERE phone = ? OR phone = ?",
            (stored, alt or stored),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return int(row["book_id"])


class _Pool:
    def __init__(self, path: Path, contacts_per_file: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.contacts_per_file = contacts_per_file
        self.connection = _connect(path)
        _ensure_schema(self.connection)
        self._pending = 0
        self._book_id: int | None = None
        self._room = 0
        self._unflushed = 0
        self.connection.execute("BEGIN")

    def add_many(self, phones: list[str], source_name: str) -> tuple[int, int]:
        """Insert many phones. Returns ``(added, duplicate)``."""
        ordered: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        duplicate = 0
        for phone in phones:
            stored, alt = storage_forms(phone)
            if stored in seen:
                duplicate += 1
                continue
            seen.add(stored)
            ordered.append((stored, alt))
        if not ordered:
            return 0, duplicate
        self.connection.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS staging (
                phone TEXT PRIMARY KEY,
                alt TEXT
            )
            """
        )
        self.connection.execute("DELETE FROM staging")
        self.connection.executemany(
            "INSERT OR IGNORE INTO staging (phone, alt) VALUES (?, ?)",
            ordered,
        )
        already = {
            str(row["phone"])
            for row in self.connection.execute(
                """
                SELECT staging.phone AS phone
                FROM staging
                JOIN numbers
                  ON numbers.phone = staging.phone
                  OR numbers.phone = staging.alt
                """
            )
        }
        stamp = _now()
        added = 0
        for phone, _alt in ordered:
            if phone in already:
                duplicate += 1
                continue
            book_id = self._book_for_new()
            try:
                self.connection.execute(
                    """
                    INSERT INTO numbers (phone, book_id, added_at, source_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (phone, book_id, stamp, source_name),
                )
            except sqlite3.IntegrityError:
                duplicate += 1
                continue
            self._room -= 1
            self._unflushed += 1
            added += 1
            if self._room <= 0:
                self._flush_book()
                self._book_id = None
        self._pending += added
        if self._pending >= 8000:
            self._flush_book()
            self._commit_open()
        return added, duplicate

    def add(self, phone: str, source_name: str) -> bool:
        stored, alt = storage_forms(phone)
        existing = self.connection.execute(
            "SELECT book_id FROM numbers WHERE phone = ? OR phone = ?",
            (stored, alt or stored),
        ).fetchone()
        if existing is not None:
            return False
        book_id = self._open_book()
        try:
            self.connection.execute(
                """
                INSERT INTO numbers (phone, book_id, added_at, source_name)
                VALUES (?, ?, ?, ?)
                """,
                (stored, book_id, _now(), source_name),
            )
        except sqlite3.IntegrityError:
            return False
        self.connection.execute(
            "UPDATE books SET contact_count = contact_count + 1 WHERE id = ?",
            (book_id,),
        )
        self._pending += 1
        if self._pending >= 2000:
            self._commit_open()
        return True

    def finish(self) -> None:
        self._flush_book()
        self._commit_open()

    def close(self) -> None:
        self.connection.close()

    def _book_for_new(self) -> int:
        if self._book_id is not None and self._room > 0:
            return self._book_id
        self._flush_book()
        row = self.connection.execute(
            """
            SELECT id, contact_count FROM books
            WHERE downloaded_at IS NULL AND contact_count < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (self.contacts_per_file,),
        ).fetchone()
        if row is not None:
            self._book_id = int(row["id"])
            self._room = self.contacts_per_file - int(row["contact_count"])
            self._unflushed = 0
            return self._book_id
        cursor = self.connection.execute(
            "INSERT INTO books (created_at, contact_count, downloaded_at) VALUES (?, 0, NULL)",
            (_now(),),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Không tạo được danh bạ mới")
        self._book_id = int(cursor.lastrowid)
        self._room = self.contacts_per_file
        self._unflushed = 0
        return self._book_id

    def _flush_book(self) -> None:
        if self._book_id is None or self._unflushed == 0:
            return
        self.connection.execute(
            "UPDATE books SET contact_count = contact_count + ? WHERE id = ?",
            (self._unflushed, self._book_id),
        )
        self._unflushed = 0

    def _open_book(self) -> int:
        row = self.connection.execute(
            """
            SELECT id FROM books
            WHERE downloaded_at IS NULL AND contact_count < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (self.contacts_per_file,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = self.connection.execute(
            "INSERT INTO books (created_at, contact_count, downloaded_at) VALUES (?, 0, NULL)",
            (_now(),),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Không tạo được danh bạ mới")
        return int(cursor.lastrowid)

    def _commit_open(self) -> None:
        self.connection.commit()
        self.connection.execute("BEGIN")
        self._pending = 0


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-65536")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            contact_count INTEGER NOT NULL,
            downloaded_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS numbers (
            phone TEXT PRIMARY KEY,
            book_id INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            source_name TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_numbers_book ON numbers (book_id)"
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS numbers_book_locked
        BEFORE UPDATE OF book_id ON numbers
        BEGIN
            SELECT RAISE(ABORT, 'Số đã chia danh bạ, không được chuyển danh bạ khác');
        END
        """
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS pool_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    _collapse_duplicate_numbers(connection)
    connection.commit()


def _collapse_duplicate_numbers(connection: sqlite3.Connection) -> None:
    """Keep the first book for each number and store one 10-digit key."""
    done = connection.execute(
        "SELECT value FROM pool_meta WHERE key = 'canonical_v1'"
    ).fetchone()
    if done is not None:
        return
    rows = connection.execute(
        """
        SELECT rowid, phone, added_at
        FROM numbers
        WHERE phone NOT LIKE '0%' OR length(phone) != 10
        """
    ).fetchall()
    removed = False
    for row in rows:
        key = to_vietnam_10(str(row["phone"])) or str(row["phone"])
        if key == row["phone"]:
            continue
        other = connection.execute(
            "SELECT rowid, added_at FROM numbers WHERE phone = ?",
            (key,),
        ).fetchone()
        if other is None:
            connection.execute(
                "UPDATE numbers SET phone = ? WHERE rowid = ?",
                (key, row["rowid"]),
            )
            continue
        current_first = (str(row["added_at"]), int(row["rowid"])) < (
            str(other["added_at"]),
            int(other["rowid"]),
        )
        if current_first:
            connection.execute("DELETE FROM numbers WHERE rowid = ?", (other["rowid"],))
            connection.execute(
                "UPDATE numbers SET phone = ? WHERE rowid = ?",
                (key, row["rowid"]),
            )
        else:
            connection.execute("DELETE FROM numbers WHERE rowid = ?", (row["rowid"],))
        removed = True
    if removed:
        connection.execute(
            """
            UPDATE books
            SET contact_count = (
                SELECT COUNT(*) FROM numbers WHERE numbers.book_id = books.id
            )
            """
        )
    connection.execute(
        "INSERT INTO pool_meta (key, value) VALUES ('canonical_v1', '1')"
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def iter_book_phones(folder: Path, book_id: int) -> Iterator[str]:
    connection = _connect(pool_path(folder))
    try:
        _ensure_schema(connection)
        rows = connection.execute(
            "SELECT phone FROM numbers WHERE book_id = ? ORDER BY rowid",
            (book_id,),
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        yield str(row["phone"])
