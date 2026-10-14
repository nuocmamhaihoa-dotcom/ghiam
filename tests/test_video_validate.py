"""Chặn file giả / thiếu moov trước khi vào hàng đợi OCR."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from control_plane.video_store import init_db, list_videos, stats
from control_plane.video_upload_sessions import complete_upload, init_upload, put_chunk
from control_plane.video_validate import probe_duration_sec, validate_media_file


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "cần ffmpeg+ffprobe")
class VideoValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        self.videos = self.root / "videos"
        self.videos.mkdir()
        init_db(self.db)
        self.limit = 80 * 1024 * 1024 * 1024

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_real_mp4(self, path: Path) -> bytes:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x120:rate=30",
                "-t",
                "0.4",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(path),
            ],
            check=True,
            timeout=30,
        )
        return path.read_bytes()

    def test_junk_bytes_rejected_before_queue(self) -> None:
        junk = b"BplusD-smoke-" * 2000
        path = self.root / "smoke.mp4"
        path.write_bytes(junk)
        with self.assertRaises(ValueError) as ctx:
            validate_media_file(path)
        text = str(ctx.exception).lower()
        self.assertTrue("moov" in text or "không phải video" in text or "hợp lệ" in text)

        session = init_upload(
            self.db,
            self.videos,
            name="smoke.mp4",
            size_bytes=len(junk),
            client_key="smoke",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        upload_id = str(session["upload_id"])
        chunk = int(session["chunk_size"])
        for index in range(int(session["chunks_total"])):
            start = index * chunk
            put_chunk(self.db, upload_id, index, junk[start : start + chunk])
        with self.assertRaises(ValueError):
            complete_upload(self.db, self.videos, upload_id, disk_limit_bytes=self.limit)
        self.assertEqual(stats(self.db)["queued"], 0)
        self.assertEqual(stats(self.db).get("error", 0), 0)

    def test_real_mp4_accepted(self) -> None:
        path = self.root / "ok.mp4"
        payload = self._make_real_mp4(path)
        validate_media_file(path)
        session = init_upload(
            self.db,
            self.videos,
            name="ok.mp4",
            size_bytes=len(payload),
            client_key="ok",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        upload_id = str(session["upload_id"])
        chunk = int(session["chunk_size"])
        for index in range(int(session["chunks_total"])):
            start = index * chunk
            end = min(len(payload), start + chunk)
            put_chunk(self.db, upload_id, index, payload[start:end])
        done = complete_upload(self.db, self.videos, upload_id, disk_limit_bytes=self.limit)
        self.assertEqual(done["status"], "queued")
        self.assertEqual(stats(self.db)["queued"], 1)
        duration = probe_duration_sec(path)
        self.assertGreater(duration, 0.2)
        self.assertGreater(float(done.get("duration_sec") or 0), 0.2)
        listed = list_videos(self.db)
        self.assertAlmostEqual(float(listed[0]["duration_sec"]), duration, places=1)
        self.assertEqual(listed[0]["issue"], "Chờ đọc")


if __name__ == "__main__":
    unittest.main()
