"""Sổ video: hàng chờ, vấn đề, và đọc tiếp sau khi hub khởi động lại."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_plane import db, video_ledger
from control_plane.settings import settings
from control_plane.video_jobs import jobs, restore_open
from control_plane.video_ledger import board, open_upload, save_job, touch_upload


class VideoLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = settings.db_path
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._dir.name) / "ledger.db"
        settings.db_path = self.db_path
        db.init_db(self.db_path)
        self._ids: list[str] = []

    def tearDown(self) -> None:
        for job_id in self._ids:
            jobs._jobs.pop(job_id, None)
        settings.db_path = self._previous
        video_ledger._SEEN.clear()
        self._dir.cleanup()

    def _track(self, job_id: str) -> None:
        self._ids.append(job_id)

    def test_queue_keeps_order_and_problems(self) -> None:
        first = jobs.create(name="mot.mp4", source="iPhone aaaa", size=3)
        second = jobs.create(name="hai.mp4", source="iPhone bbbb", size=4)
        self._track(first.id)
        self._track(second.id)
        first.add_problem("Khung mờ")
        listed = board()
        names = [item["name"] for item in listed["queued"]]
        self.assertEqual(names[:2], ["mot.mp4", "hai.mp4"])
        self.assertEqual(listed["queued"][0]["problems"], ["Khung mờ"])
        self.assertEqual(listed["queued"][0]["source"], "iPhone aaaa")
        self.assertEqual(listed["queued"][0]["state"], "queued")

    def test_upload_progress_does_not_rewind_a_queued_job(self) -> None:
        open_upload("clip1", "clip.mp4", "iPhone cccc", 9)
        with db.connect(self.db_path) as conn:
            original = conn.execute("SELECT created_at FROM video_jobs WHERE id=?", ("clip1",)).fetchone()
            self.assertIsNotNone(original)
            created = float(original["created_at"])
        job = jobs.create("clip1", name="clip.mp4", source="iPhone cccc", size=9)
        self._track(job.id)
        path = Path(self._dir.name) / "clip.mp4"
        path.write_bytes(b"123456789")
        job.bind(path)
        video_ledger._SEEN.clear()
        touch_upload("clip1", 80, "Đang gửi 1/2 MB")
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT state, percent, created_at FROM video_jobs WHERE id=?", ("clip1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "queued")
        self.assertNotEqual(int(row["percent"]), 80)
        self.assertEqual(float(row["created_at"]), created)

    def test_restore_keeps_frames_and_skips_a_missing_file(self) -> None:
        path = Path(self._dir.name) / "doc.mp4"
        path.write_bytes(b"video")
        job = jobs.create(name="doc.mp4", source="Trang chủ", size=5)
        self._track(job.id)
        job.bind(path)
        job.remember_frame(1.5, ["Xin chào"], [{"kind": "row", "name": "An", "contactName": "", "username": "an"}])
        save_job(job.snapshot(), force=True)
        jobs._jobs.pop(job.id, None)
        ready = restore_open()
        self.assertEqual(ready, [(job.id, path)])
        restored = jobs.get(job.id)
        self.assertIsNotNone(restored)
        assert restored is not None
        remembered = restored.remembered()
        self.assertIn("1.500", remembered)
        self.assertEqual(remembered["1.500"][0], ["Xin chào"])
        self.assertEqual(restore_open(), [])
        jobs._jobs.pop(job.id, None)
        path.unlink()
        self.assertEqual(restore_open(), [])
        listed = board()
        failed = [item for item in listed["history"] if item["jobId"] == job.id]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["state"], "failed")
        self.assertEqual(failed[0]["error"], "Mất file video.")

    def test_frame_notes_are_not_written_on_every_frame(self) -> None:
        path = Path(self._dir.name) / "nhieu.mp4"
        path.write_bytes(b"video")
        job = jobs.create(name="nhieu.mp4", source="Trang chủ", size=5)
        self._track(job.id)
        job.bind(path)
        video_ledger._SEEN.clear()
        # Đồng hồ đứng yên để bài thử không phụ thuộc máy chậm: ghi khung cách 2 giây.
        with patch.object(video_ledger.time, "monotonic", return_value=1000.0):
            for index in range(1000):
                job.remember_frame(index / 1000, ["a"], [])
        with db.connect(self.db_path) as conn:
            raw = conn.execute("SELECT frames_json FROM video_jobs WHERE id=?", (job.id,)).fetchone()
        self.assertIsNotNone(raw)
        self.assertLess(len(json.loads(raw["frames_json"])), 20)
        save_job(job.snapshot(), force=True)
        with db.connect(self.db_path) as conn:
            raw = conn.execute("SELECT frames_json FROM video_jobs WHERE id=?", (job.id,)).fetchone()
        self.assertEqual(len(json.loads(raw["frames_json"])), 1000)


if __name__ == "__main__":
    unittest.main()
