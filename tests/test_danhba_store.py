"""Chia danh bạ trên hub: 5000 số, một số một tên, danh bạ đã dùng không nhận thêm."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from control_plane.danhba_store import import_people, list_books, mark_used, reconcile
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

    def test_reconcile_promotes_full_books_and_can_return_missing_ones(self) -> None:
        imported = import_people(
            self.db_path,
            "Lan, +84 901-234-567\nMinh, 0902222222\n",
            "Khach",
            "2026-09-29T00:00:00+00:00",
            page_size=5000,
        )
        self.assertEqual(imported["added"], 2)
        partial = reconcile(
            self.db_path,
            "BEGIN:VCARD\r\nTEL;TYPE=CELL:+84 901-234-567\r\nEND:VCARD\r\n",
            [],
            False,
            "2026-09-29T01:00:00+00:00",
        )
        self.assertEqual(partial["movedToUsed"], 0)
        self.assertEqual(partial["seenPhones"], 1)
        self.assertEqual(partial["unknownPhones"], 0)
        self.assertEqual(partial["books"][0]["matched"], 1)
        self.assertEqual(list_books(self.db_path)["ready"][0]["name"], "Khach 1")
        folded = "BEGIN:VCARD\nTEL;TYPE=CELL:0901\n 234567\nEND:VCARD\nBEGIN:VCARD\nTEL;TYPE=CELL:0902222222\nEND:VCARD\n"
        matched = reconcile(self.db_path, folded, [], False, "2026-09-29T02:00:00+00:00")
        self.assertEqual(matched["movedToUsed"], 1)
        self.assertEqual(matched["unknownPhones"], 0)
        self.assertEqual([book["name"] for book in list_books(self.db_path)["used"]], ["Khach 1"])
        returned = reconcile(
            self.db_path,
            "",
            ["0910000000"],
            True,
            "2026-09-29T03:00:00+00:00",
        )
        self.assertEqual(returned["movedToReady"], 1)
        self.assertEqual(returned["unknownPhones"], 1)
        self.assertEqual(list_books(self.db_path)["used"], [])
        again = import_people(
            self.db_path,
            "Lan, 0901234567\n",
            "Khach",
            "2026-09-29T04:00:00+00:00",
        )
        self.assertEqual(again["skippedExisting"], 1)
        self.assertEqual(again["added"], 0)


if __name__ == "__main__":
    unittest.main()
