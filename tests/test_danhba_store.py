"""Chia danh bạ trên hub: 5000 số, một số một tên, danh bạ đã dùng không nhận thêm."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from control_plane.danhba_store import import_people, list_books, mark_used
from control_plane.db import init_db


class DanhBaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._dir.name) / "server.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_fills_open_book_and_leaves_used_books_alone(self) -> None:
        first = import_people(
            self.db_path,
            "An, 0901111111\nBinh, 0902222222\nChi, 0903333333\n",
            "Khach",
            "2026-09-29T00:00:00+00:00",
            page_size=2,
        )
        self.assertEqual(first["added"], 3)
        ready = first["ready"]
        self.assertEqual([book["count"] for book in ready], [2, 1])
        self.assertEqual([book["name"] for book in ready], ["Khach 1", "Khach 2"])
        marked = mark_used(self.db_path, str(ready[0]["id"]), "2026-09-29T01:00:00+00:00")
        self.assertIsNotNone(marked)
        assert marked is not None
        self.assertEqual(marked["status"], "used")
        second = import_people(
            self.db_path,
            "An, 0901111111\nDung, 0904444444\nEm, 0905555555\n",
            "Khach",
            "2026-09-29T02:00:00+00:00",
            page_size=2,
        )
        self.assertEqual(second["added"], 2)
        self.assertEqual(second["skippedExisting"], 1)
        books = list_books(self.db_path)
        self.assertEqual([book["name"] for book in books["used"]], ["Khach 1"])
        self.assertEqual([book["count"] for book in books["used"]], [2])
        self.assertEqual([book["name"] for book in books["ready"]], ["Khach 2", "Khach 3"])
        self.assertEqual([book["count"] for book in books["ready"]], [2, 1])


if __name__ == "__main__":
    unittest.main()
