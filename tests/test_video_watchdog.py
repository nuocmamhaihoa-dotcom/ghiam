"""Quyết định cập nhật mã đọc video khi PC rảnh."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from pc_agent.video_watchdog import (
    process_alive,
    read_local_version,
    read_reading_state,
    replace_tree,
    upgrade_allowed,
    verify_sha256,
)
from pc_agent.video_worker import write_worker_state


class VideoWatchdogTests(unittest.TestCase):
    def test_one_reading_blocks_an_update(self) -> None:
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=True, age_sec=5, pid_alive=True),
            "busy",
        )
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=False, age_sec=1, pid_alive=True),
            "apply",
        )
        self.assertEqual(
            upgrade_allowed(local=2, remote=2, reading=False, age_sec=0, pid_alive=False),
            "current",
        )
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=True, age_sec=181, pid_alive=True),
            "apply",
        )
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=True, age_sec=5, pid_alive=False),
            "apply",
        )

    def test_replace_keeps_config_outside_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "config.json").write_text('{"hub":"http://hub","token":"secret"}', encoding="utf-8")
            current = base / "current"
            current.mkdir()
            (current / "VERSION").write_text("1", encoding="utf-8")
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, "w") as archive:
                archive.writestr("VERSION", "2")
                archive.writestr("pc_agent/video_worker.py", "print('new')\n")
            payload = blob.getvalue()
            replace_tree(current, payload)
            self.assertEqual(read_local_version(base / "current"), 2)
            self.assertEqual((base / "current" / "pc_agent" / "video_worker.py").read_text(encoding="utf-8"), "print('new')\n")
            self.assertIn("secret", (base / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(verify_sha256(payload, hashlib.sha256(payload).hexdigest()))

    def test_bad_checksum_and_zip_slip_are_rejected(self) -> None:
        self.assertFalse(verify_sha256(b"abc", "0" * 64))
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("../config.json", "stolen")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                replace_tree(Path(folder) / "current", blob.getvalue())
            self.assertFalse((Path(folder) / "config.json").exists())

    def test_worker_state_marks_a_live_reading(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            write_worker_state(True, path)
            reading, age, pid = read_reading_state(path)
            self.assertTrue(reading)
            self.assertLess(age, 5)
            self.assertEqual(pid, os.getpid())
            self.assertTrue(process_alive(pid))
            write_worker_state(False, path)
            reading, _age, pid = read_reading_state(path)
            self.assertFalse(reading)
            self.assertEqual(pid, os.getpid())


if __name__ == "__main__":
    unittest.main()
