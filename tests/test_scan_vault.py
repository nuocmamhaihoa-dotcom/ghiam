"""Sổ dữ liệu đã quét: giữ mọi lần quét và dựng lại từ từng bản dự phòng."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from control_plane import db, scan_vault


def _erase(path: Path) -> None:
    for extra in ("", "-wal", "-shm"):
        item = Path(str(path) + extra)
        if item.is_file():
            item.unlink()


class ScanVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._dir.name) / "server.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _save(self, username: str, at: str) -> None:
        db.save_people(
            self.db_path,
            [
                {
                    "nameKey": "an",
                    "name": "An",
                    "contactName": "A Ban",
                    "username": username,
                }
            ],
            at,
        )

    def test_a_later_scan_keeps_the_earlier_one(self) -> None:
        self._save("@an", "2026-10-04T01:00:00Z")
        self._save("@an.moi", "2026-10-04T02:00:00Z")
        facts = scan_vault.list_facts(self.db_path)
        self.assertEqual([item["username"] for item in facts], ["@an", "@an.moi"])
        self.assertEqual(db.people_by_keys(self.db_path, ["an"])["an"]["username"], "@an.moi")
        _mirror, log, seal = scan_vault.locations(self.db_path)
        lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual([item["username"] for item in lines], ["@an", "@an.moi"])
        self.assertTrue(seal.is_file())
        self._save("@an.moi", "2026-10-04T03:00:00Z")
        self.assertEqual(len(scan_vault.list_facts(self.db_path)), 2)

    def test_sql_cannot_delete_or_edit_a_scan(self) -> None:
        self._save("@an", "2026-10-04T01:00:00Z")
        conn = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM scan_facts")
            conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE scan_facts SET name = 'Khac'")
            conn.commit()
        conn.close()
        self.assertEqual(len(scan_vault.list_facts(self.db_path)), 1)

    def test_each_copy_rebuilds_the_other_two(self) -> None:
        self._save("@an", "2026-10-04T01:00:00Z")
        mirror, log, seal = scan_vault.locations(self.db_path)

        _erase(mirror)
        log.unlink()
        seal.unlink()
        scan_vault.startup(self.db_path)
        self.assertIn("@an", log.read_text(encoding="utf-8"))
        self.assertTrue(mirror.is_file())

        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TRIGGER IF EXISTS scan_facts_no_delete")
        conn.execute("DELETE FROM scan_facts")
        conn.commit()
        conn.close()
        log.unlink()
        seal.unlink()
        scan_vault.startup(self.db_path)
        self.assertEqual(scan_vault.list_facts(self.db_path)[0]["username"], "@an")
        self.assertIn("@an", log.read_text(encoding="utf-8"))

        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TRIGGER IF EXISTS scan_facts_no_delete")
        conn.execute("DELETE FROM scan_facts")
        conn.commit()
        conn.close()
        _erase(mirror)
        scan_vault.startup(self.db_path)
        self.assertEqual(scan_vault.list_facts(self.db_path)[0]["username"], "@an")
        self.assertTrue(mirror.is_file())

    def test_a_deleted_database_comes_back_from_the_backups(self) -> None:
        self._save("@an", "2026-10-04T01:00:00Z")
        _erase(self.db_path)
        db.init_db(self.db_path)
        stored = db.people_by_keys(self.db_path, ["an"])
        self.assertEqual(stored["an"]["username"], "@an")
        self.assertEqual(stored["an"]["contactName"], "A Ban")
        self.assertEqual(len(scan_vault.list_facts(self.db_path)), 1)

    def test_a_missing_person_row_is_restored(self) -> None:
        self._save("@an", "2026-10-04T01:00:00Z")
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM saved_people")
        conn.commit()
        conn.close()
        scan_vault.startup(self.db_path)
        self.assertEqual(db.people_by_keys(self.db_path, ["an"])["an"]["username"], "@an")

    def test_a_full_working_store_still_keeps_the_scan(self) -> None:
        previous = db.PEOPLE_CAPACITY
        db.PEOPLE_CAPACITY = 0
        try:
            skipped = db.save_people(
                self.db_path,
                [
                    {
                        "nameKey": "an",
                        "name": "An",
                        "contactName": "A Ban",
                        "username": "@an",
                    }
                ],
                "2026-10-04T01:00:00Z",
            )
        finally:
            db.PEOPLE_CAPACITY = previous
        self.assertEqual(skipped, ["an"])
        self.assertEqual(db.people_by_keys(self.db_path, ["an"]), {})
        facts = scan_vault.list_facts(self.db_path)
        self.assertEqual(facts[0]["kept"], 0)
        self.assertEqual(facts[0]["username"], "@an")
        _mirror, log, _seal = scan_vault.locations(self.db_path)
        self.assertIn("@an", log.read_text(encoding="utf-8"))

    def test_a_duplicate_scan_is_kept_too(self) -> None:
        self._save("@an", "2026-10-04T01:00:00Z")
        db.save_duplicates(
            self.db_path,
            [
                {
                    "nameKey": "an",
                    "name": "An",
                    "contactName": "A Ban Khac",
                    "username": "@an",
                }
            ],
            "2026-10-04T04:00:00Z",
        )
        kinds = [item["kind"] for item in scan_vault.list_facts(self.db_path)]
        self.assertEqual(kinds, ["person", "duplicate"])

    def test_pages_say_scans_stay_forever(self) -> None:
        root = Path(__file__).resolve().parents[1]
        iphone = (root / "control_plane" / "static" / "iphone.html").read_text(encoding="utf-8")
        dashboard = (root / "control_plane" / "static" / "dashboard.html").read_text(encoding="utf-8")
        for page in (iphone, dashboard):
            self.assertIn("50 triệu", page)
            self.assertIn("giữ vĩnh viễn", page)
            self.assertIn("hai bản dự phòng", page)
