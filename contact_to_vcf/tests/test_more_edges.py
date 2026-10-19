"""More import edges: headerless CSV, old Mac lines, mixed bytes, parallel adds."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from exporters.vcf_validator import validate_vcf
from parsers.column_suggest import phone_column_for_import
from parsers.detect import inspect_source
from processors.pool import export_book, import_file, list_books, pool_path, pool_total


def _import(folder: Path, source: Path, *, file_format: str, per_file: int = 1000):
    info = inspect_source(source, file_format=file_format)
    return import_file(
        folder,
        source,
        file_format=info.file_format,
        delimiter=info.delimiter or ",",
        has_header=info.has_header,
        encoding=info.encoding or "utf-8-sig",
        phone_column=phone_column_for_import(info.file_format, info.columns, info.samples),
        contacts_per_file=per_file,
    )


def test_headerless_csv_keeps_the_first_phone(tmp_path: Path) -> None:
    path = tmp_path / "so.csv"
    path.write_text("0901111111,An\n0902222222,Binh\n", encoding="utf-8")
    info = inspect_source(path)
    assert info.has_header is False
    folder = tmp_path / "kho"
    folder.mkdir()
    stats = _import(folder, path, file_format="csv")
    assert stats.added == 2
    assert pool_total(folder) == 2


def test_csv_title_row_is_not_stored_as_a_phone(tmp_path: Path) -> None:
    path = tmp_path / "co-tieu-de.csv"
    path.write_text("ten,so\nAn,0901111111\n", encoding="utf-8")
    info = inspect_source(path)
    assert info.has_header is True
    folder = tmp_path / "kho"
    folder.mkdir()
    stats = _import(folder, path, file_format="csv")
    assert stats.added == 1
    assert stats.rejected == 0


def test_old_mac_line_endings_and_a_later_bad_byte(tmp_path: Path) -> None:
    classic = tmp_path / "mac.txt"
    classic.write_bytes(b"0901111111\r0902222222\r")
    folder = tmp_path / "kho"
    folder.mkdir()
    stats = _import(folder, classic, file_format="txt")
    assert stats.added == 2

    mixed = tmp_path / "tron.txt"
    body = b"".join(f"090{index:07d}\n".encode() for index in range(900))
    mixed.write_bytes(body + b"0912345678 \xff ten\n")
    more = _import(folder, mixed, file_format="txt")
    assert more.added == 901
    assert pool_total(folder) == 903


def test_parallel_imports_stay_inside_each_book(tmp_path: Path) -> None:
    folder = tmp_path / "kho"
    folder.mkdir()
    left = tmp_path / "a.txt"
    right = tmp_path / "b.txt"
    left.write_text("\n".join(f"090{index:07d}" for index in range(50)), encoding="utf-8")
    right.write_text("\n".join(f"091{index:07d}" for index in range(50)), encoding="utf-8")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(path: Path) -> None:
        try:
            barrier.wait(timeout=5)
            _import(folder, path, file_format="txt", per_file=40)
        except BaseException as exc:  # noqa: BLE001 - the test reports the worker error
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(path,)) for path in (left, right)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert pool_total(folder) == 100
    counts = [book.contact_count for book in list_books(folder)]
    assert counts
    assert max(counts) <= 40
    assert sum(counts) == 100
    connection = sqlite3.connect(pool_path(folder))
    repeated = connection.execute(
        """
        SELECT phone, COUNT(*) AS copies
        FROM numbers
        GROUP BY phone
        HAVING copies > 1
        """
    ).fetchall()
    drifted = connection.execute(
        """
        SELECT books.id
        FROM books
        LEFT JOIN numbers ON numbers.book_id = books.id
        GROUP BY books.id
        HAVING books.contact_count != COUNT(numbers.phone)
        """
    ).fetchall()
    connection.close()
    assert repeated == []
    assert drifted == []
    destination = tmp_path / "mot.vcf"
    exported = export_book(folder, list_books(folder)[0].id, destination)
    validate_vcf(destination, exported)
    text = destination.read_bytes()
    assert b"\r\n" in text
    assert b"VERSION:3.0\r\n" in text
