"""A number that already belongs to a book must not be assigned again."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from openpyxl import Workbook

from processors.phone_normalizer import equivalent_forms, to_vietnam_10
from processors.pool import book_of, export_book, import_file, list_books, pool_path, pool_total


def test_every_stored_spelling_is_still_the_same_number() -> None:
    samples = [
        "0901234567",
        "+84 901 234 567",
        "84 901234567",
        "0901234567.0",
        "01621234567",
        "0321234567",
        "＋８４０９０１２３４５６７",
    ]
    for sample in samples:
        ten = to_vietnam_10(sample)
        assert ten is not None
        for form in equivalent_forms(sample):
            assert to_vietnam_10(form) == ten, (sample, form)


def test_repeat_list_does_not_assign_old_numbers_to_a_new_book(tmp_path: Path) -> None:
    folder = tmp_path / "kho"
    folder.mkdir()
    first = tmp_path / "dot-truoc.txt"
    first.write_text(
        "\n".join(["0901234567", "0912345678", "0321234567", "0987654321"]),
        encoding="utf-8",
    )
    imported = import_file(
        folder,
        first,
        file_format="txt",
        delimiter=",",
        has_header=False,
        encoding="utf-8",
        phone_column=0,
        contacts_per_file=2,
    )
    assert imported.added == 4
    assert [book.contact_count for book in list_books(folder)] == [2, 2]
    homes = {
        "0901234567": 1,
        "0912345678": 1,
        "0321234567": 2,
        "0987654321": 2,
    }
    for phone, book_id in homes.items():
        assert book_of(folder, phone) == book_id
    export_book(folder, 1, tmp_path / "da-tai.vcf")

    again = tmp_path / "dot-sau.txt"
    again.write_text(
        "\n".join(
            [
                "0901234567",
                "+84901234567",
                "84 901 234 567",
                "090.123.4567",
                "0901234567.0",
                "0084901234567",
                "01621234567",
                "032 123 4567",
                "+84 162 123 4567",
                "0912345678",
                "0987654321",
                "0909999999",
                "0918888888",
            ]
        ),
        encoding="utf-8",
    )
    second = import_file(
        folder,
        again,
        file_format="txt",
        delimiter=",",
        has_header=False,
        encoding="utf-8",
        phone_column=0,
        contacts_per_file=2,
    )
    assert second.added == 2
    assert second.duplicate == 11
    for phone, book_id in homes.items():
        assert book_of(folder, phone) == book_id
    assert book_of(folder, "0909999999") == 3
    assert book_of(folder, "0918888888") == 3
    assert pool_total(folder) == 6
    _assert_one_row_per_number(folder)
    fresh = export_book(folder, 3, tmp_path / "moi.vcf")
    text = (tmp_path / "moi.vcf").read_text(encoding="utf-8")
    assert fresh == 2
    assert "0901234567" not in text
    assert "0321234567" not in text
    assert "FN:0909999999" in text
    assert "FN:0918888888" in text


def test_old_database_spellings_still_block_a_new_book(tmp_path: Path) -> None:
    folder = tmp_path / "kho"
    folder.mkdir()
    connection = sqlite3.connect(folder / "kho.sqlite")
    connection.execute(
        "CREATE TABLE books (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, contact_count INTEGER NOT NULL, downloaded_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE numbers (phone TEXT PRIMARY KEY, book_id INTEGER NOT NULL, added_at TEXT NOT NULL, source_name TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE pool_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    connection.execute("INSERT INTO pool_meta VALUES ('canonical_v1', '1')")
    connection.execute("INSERT INTO pool_meta VALUES ('canonical_v2', '1')")
    connection.execute(
        "INSERT INTO books VALUES (1, '2026-10-01 09:00', 2, '2026-10-01 09:48')"
    )
    connection.executemany(
        "INSERT INTO numbers VALUES (?, 1, '2026-10-01 09:00', 'old')",
        [("84901234567",), ("01631234567",)],
    )
    connection.commit()
    connection.close()

    source = tmp_path / "lai.txt"
    source.write_text(
        "0901234567\n0331234567\n84901234567\n0900000000\n",
        encoding="utf-8",
    )
    stats = import_file(
        folder,
        source,
        file_format="txt",
        delimiter=",",
        has_header=False,
        encoding="utf-8",
        phone_column=0,
        contacts_per_file=4000,
    )
    assert stats.added == 1
    assert stats.duplicate == 3
    assert book_of(folder, "0901234567") == 1
    assert book_of(folder, "84901234567") == 1
    assert book_of(folder, "01631234567") == 1
    assert book_of(folder, "0331234567") == 1
    assert book_of(folder, "0900000000") == 2
    assert pool_total(folder) == 3
    _assert_one_row_per_number(folder)


def test_excel_column_b_repeat_stays_in_the_first_book(tmp_path: Path) -> None:
    folder = tmp_path / "kho"
    folder.mkdir()
    first = tmp_path / "a.xlsx"
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["ma", "sdt"])
    sheet.append(["1", 84901234567])
    sheet.append(["2", "0912345678"])
    book.save(first)
    added = import_file(
        folder,
        first,
        file_format="xlsx",
        delimiter="",
        has_header=True,
        encoding="utf-8-sig",
        phone_column=1,
        contacts_per_file=4000,
    )
    assert added.added == 2
    assert book_of(folder, "0901234567") == 1

    second = tmp_path / "b.xlsx"
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["khach", "so"])
    sheet.append(["a", "0901234567"])
    sheet.append(["b", "+84912345678"])
    sheet.append(["c", "0987000000"])
    book.save(second)
    again = import_file(
        folder,
        second,
        file_format="xlsx",
        delimiter="",
        has_header=True,
        encoding="utf-8-sig",
        phone_column=1,
        contacts_per_file=4000,
    )
    assert again.added == 1
    assert again.duplicate == 2
    assert book_of(folder, "84901234567") == 1
    assert book_of(folder, "+84912345678") == 1
    assert book_of(folder, "0987000000") == 1
    assert [item.contact_count for item in list_books(folder)] == [3]
    _assert_one_row_per_number(folder)


def _assert_one_row_per_number(folder: Path) -> None:
    connection = sqlite3.connect(pool_path(folder))
    phones = [str(row[0]) for row in connection.execute("SELECT phone FROM numbers")]
    connection.close()
    keys = [to_vietnam_10(phone) or phone for phone in phones]
    assert len(keys) == len(set(keys))
    assert len({book_of(folder, phone) for phone in keys}) >= 1
