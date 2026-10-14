"""Giám sát bộ đọc: chỉ restart khi còn việc mà heartbeat đã chết."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from control_plane.settings import settings
from control_plane.video_store import begin_upload, claim, commit_upload, init_db
from control_plane.video_watchdog import needs_restart


class VideoWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "video.db"
        self.hb = Path(self._tmp.name) / "video-worker.heartbeat"
        init_db(self.db)
        self._old_db = settings.video_db_path
        settings.video_db_path = self.db

    def tearDown(self) -> None:
        settings.video_db_path = self._old_db
        self._tmp.cleanup()

    def _queue_one(self) -> None:
        created = begin_upload(self.db, name="a.mp4", size_bytes=8, sha256="w1")
        path = Path(self._tmp.name) / "a.mp4"
        path.write_bytes(b"12345678")
        commit_upload(self.db, int(created["id"]), str(path))

    def test_no_restart_when_queue_empty(self) -> None:
        # Heartbeat già nhưng không còn việc → không restart.
        self.hb.write_text(str(time.time() - 1000), encoding="utf-8")
        self.assertFalse(needs_restart(self.hb, max_age=90.0))

    def test_restart_when_pending_and_heartbeat_dead(self) -> None:
        self._queue_one()
        claim(self.db, 1)
        self.hb.write_text(str(time.time() - 1000), encoding="utf-8")
        self.assertTrue(needs_restart(self.hb, max_age=90.0))

    def test_no_restart_when_reader_alive(self) -> None:
        self._queue_one()
        self.hb.write_text(str(time.time()), encoding="utf-8")
        self.assertFalse(needs_restart(self.hb, max_age=90.0))


if __name__ == "__main__":
    unittest.main()
