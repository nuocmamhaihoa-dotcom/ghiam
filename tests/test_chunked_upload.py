"""Upload cắt khúc + resume (Gói B)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from control_plane.video_store import init_db, list_videos, stats
from control_plane.video_upload_sessions import (
    complete_upload,
    get_upload,
    init_upload,
    put_chunk,
)


class ChunkedUploadTests(unittest.TestCase):
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

    def test_resume_skips_already_received_chunks(self) -> None:
        payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * 2000  # ~72KB
        chunk = 16 * 1024
        session = init_upload(
            self.db,
            self.videos,
            name="clip.mp4",
            size_bytes=len(payload),
            device="iPhone A",
            client_key="A|clip.mp4|size|1",
            chunk_size=chunk,
            disk_limit_bytes=self.limit,
        )
        self.assertFalse(session["resumed"])
        total = int(session["chunks_total"])
        upload_id = str(session["upload_id"])

        # Gửi hết trừ chunk giữa.
        skip = total // 2
        for index in range(total):
            if index == skip:
                continue
            start = index * chunk
            end = min(len(payload), start + chunk)
            put_chunk(self.db, upload_id, index, payload[start:end])

        again = init_upload(
            self.db,
            self.videos,
            name="clip.mp4",
            size_bytes=len(payload),
            device="iPhone A",
            client_key="A|clip.mp4|size|1",
            chunk_size=chunk,
            disk_limit_bytes=self.limit,
        )
        self.assertTrue(again["resumed"])
        self.assertEqual(again["upload_id"], upload_id)
        self.assertNotIn(skip, again["received"])

        start = skip * chunk
        end = min(len(payload), start + chunk)
        put_chunk(self.db, upload_id, skip, payload[start:end])
        done = complete_upload(self.db, self.videos, upload_id, disk_limit_bytes=self.limit)
        self.assertTrue(done["ok"])
        self.assertEqual(done["status"], "queued")
        self.assertEqual(stats(self.db)["queued"], 1)
        items = list_videos(self.db)
        self.assertEqual(items[0]["name"], "clip.mp4")
        self.assertEqual(items[0]["device"], "iPhone A")
        path = self.videos / f"{done['id']}.mp4"
        self.assertEqual(path.read_bytes(), payload)

    def test_complete_rejects_missing_chunk(self) -> None:
        data = b"0123456789abcdef" * 2048  # 32KiB
        session = init_upload(
            self.db,
            self.videos,
            name="a.mov",
            size_bytes=len(data),
            client_key="k",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        put_chunk(self.db, str(session["upload_id"]), 0, data[: 16 * 1024])
        with self.assertRaises(ValueError):
            complete_upload(self.db, self.videos, str(session["upload_id"]), disk_limit_bytes=self.limit)
        body = get_upload(self.db, str(session["upload_id"]))
        self.assertEqual(body["status"], "receiving")


if __name__ == "__main__":
    unittest.main()
