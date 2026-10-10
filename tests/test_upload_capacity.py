"""Năng lực upload nhiều máy / video lớn: slot, pause OCR, đếm incoming."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from control_plane.video_store import init_db, should_pause_ocr
from control_plane.video_upload_sessions import init_upload, receiving_count, upload_slots


class UploadCapacityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        self.videos = self.root / "videos"
        self.videos.mkdir()
        init_db(self.db)
        self.limit = 120 * 1024 * 1024 * 1024
        self._env = {
            "CONTROL_VIDEO_UPLOAD_SLOTS": os.environ.get("CONTROL_VIDEO_UPLOAD_SLOTS"),
            "CONTROL_VIDEO_OCR_PAUSE_UPLOADS": os.environ.get("CONTROL_VIDEO_OCR_PAUSE_UPLOADS"),
            "CONTROL_VIDEO_FREE_RESERVE_GB": os.environ.get("CONTROL_VIDEO_FREE_RESERVE_GB"),
            "CONTROL_VIDEO_OCR_PAUSE_FREE_GB": os.environ.get("CONTROL_VIDEO_OCR_PAUSE_FREE_GB"),
        }
        os.environ["CONTROL_VIDEO_UPLOAD_SLOTS"] = "2"
        os.environ["CONTROL_VIDEO_OCR_PAUSE_UPLOADS"] = "2"
        os.environ["CONTROL_VIDEO_FREE_RESERVE_GB"] = "4"
        os.environ["CONTROL_VIDEO_OCR_PAUSE_FREE_GB"] = "8"  # free giả lập 200GB → không pause vì free

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_upload_slots_block_extra_sessions(self) -> None:
        self.assertEqual(upload_slots(), 2)
        init_upload(
            self.db,
            self.videos,
            name="a.mp4",
            size_bytes=1024 * 1024,
            client_key="a",
            chunk_size=64 * 1024,
            disk_limit_bytes=self.limit,
        )
        init_upload(
            self.db,
            self.videos,
            name="b.mp4",
            size_bytes=1024 * 1024,
            client_key="b",
            chunk_size=64 * 1024,
            disk_limit_bytes=self.limit,
        )
        self.assertEqual(receiving_count(self.db), 2)
        with self.assertRaises(MemoryError):
            init_upload(
                self.db,
                self.videos,
                name="c.mp4",
                size_bytes=1024 * 1024,
                client_key="c",
                chunk_size=64 * 1024,
                disk_limit_bytes=self.limit,
            )
        # Resume phiên cũ vẫn được.
        again = init_upload(
            self.db,
            self.videos,
            name="a.mp4",
            size_bytes=1024 * 1024,
            client_key="a",
            chunk_size=64 * 1024,
            disk_limit_bytes=self.limit,
        )
        self.assertTrue(again["resumed"])

    def test_ocr_pauses_when_many_uploads(self) -> None:
        init_upload(
            self.db,
            self.videos,
            name="a.mp4",
            size_bytes=1024 * 1024,
            client_key="a",
            chunk_size=64 * 1024,
            disk_limit_bytes=self.limit,
        )
        init_upload(
            self.db,
            self.videos,
            name="b.mp4",
            size_bytes=1024 * 1024,
            client_key="b",
            chunk_size=64 * 1024,
            disk_limit_bytes=self.limit,
        )
        with mock.patch("control_plane.video_store.shutil.disk_usage") as usage:
            usage.return_value = mock.Mock(free=200 * 1024**3)
            self.assertTrue(should_pause_ocr(self.db, self.videos))

    def test_one_upload_pauses_ocr(self) -> None:
        os.environ["CONTROL_VIDEO_OCR_PAUSE_UPLOADS"] = "1"
        init_upload(
            self.db,
            self.videos,
            name="only.mp4",
            size_bytes=1024 * 1024,
            client_key="only",
            chunk_size=64 * 1024,
            disk_limit_bytes=self.limit,
        )
        with mock.patch("control_plane.video_store.shutil.disk_usage") as usage:
            usage.return_value = mock.Mock(free=200 * 1024**3)
            self.assertTrue(should_pause_ocr(self.db, self.videos))


if __name__ == "__main__":
    unittest.main()
