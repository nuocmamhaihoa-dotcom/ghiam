"""Upload cắt khúc + resume (Gói B)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from control_plane.video_store import init_db, list_videos, stats
from control_plane.video_upload_sessions import (
    cleanup_stale_uploads,
    complete_upload,
    ensure_upload_tables,
    get_upload,
    init_upload,
    list_receiving_uploads,
    migrate_upload_sessions,
    put_chunk,
    receiving_count,
    uploads_connect,
)


def _ffmpeg_mp4(path: Path, duration: str = "0.4") -> bytes:
    # testsrc nén kém hơn color đặc → file đủ lớn cho nhiều chunk 16KiB.
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30",
            "-t",
            duration,
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


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "cần ffmpeg+ffprobe")
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
        payload = _ffmpeg_mp4(self.root / "clip.mp4", "0.5")
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

        skip = max(0, total // 2)
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
        # 640x360 × vài giây → chắc chắn >1 chunk @16KiB.
        payload = _ffmpeg_mp4(self.root / "a.mov", "3")
        session = init_upload(
            self.db,
            self.videos,
            name="a.mov",
            size_bytes=len(payload),
            client_key="k",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        self.assertGreater(int(session["chunks_total"]), 1)
        put_chunk(self.db, str(session["upload_id"]), 0, payload[: 16 * 1024])
        with self.assertRaises(ValueError):
            complete_upload(self.db, self.videos, str(session["upload_id"]), disk_limit_bytes=self.limit)
        body = get_upload(self.db, str(session["upload_id"]))
        self.assertEqual(body["status"], "receiving")

    def test_receiving_uploads_appear_in_queue_list(self) -> None:
        """Nhiều video chọn cùng lúc: mỗi file xếp phiên ngay → hiện trong hàng đợi."""
        payload_a = _ffmpeg_mp4(self.root / "a.mp4", "0.5")
        payload_b = _ffmpeg_mp4(self.root / "b.mp4", "0.5")
        first = init_upload(
            self.db,
            self.videos,
            name="one.mp4",
            size_bytes=len(payload_a),
            device="iPhone",
            client_key="iphone|one.mp4|1|1",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        second = init_upload(
            self.db,
            self.videos,
            name="two.mp4",
            size_bytes=len(payload_b),
            device="iPhone",
            client_key="iphone|two.mp4|1|1",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        put_chunk(self.db, str(first["upload_id"]), 0, payload_a[: 16 * 1024])
        listed = list_receiving_uploads(self.db)
        names = {item["name"] for item in listed}
        self.assertEqual(names, {"one.mp4", "two.mp4"})
        one = next(item for item in listed if item["name"] == "one.mp4")
        self.assertEqual(one["status"], "uploading")
        self.assertEqual(one["kind"], "upload")
        self.assertGreaterEqual(int(one["percent"]), 0)
        self.assertIn("tải", str(one["issue"]).lower())
        two = next(item for item in listed if item["name"] == "two.mp4")
        self.assertEqual(two["upload_id"], second["upload_id"])

    def test_stale_sessions_do_not_consume_slots_and_are_cleaned(self) -> None:
        payload = _ffmpeg_mp4(self.root / "stale.mp4", "0.4")
        session = init_upload(
            self.db,
            self.videos,
            name="stale.mp4",
            size_bytes=len(payload),
            client_key="stale|1",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        self.assertEqual(receiving_count(self.db), 1)
        # Giả lập phiên chết im từ lâu.
        with uploads_connect(self.db) as conn:
            conn.execute(
                "UPDATE upload_sessions SET updated_at = ? WHERE id = ?",
                ("2020-01-01T00:00:00+00:00", session["upload_id"]),
            )
        self.assertEqual(receiving_count(self.db), 0)
        self.assertEqual(receiving_count(self.db, include_stale=True), 1)
        removed = cleanup_stale_uploads(self.db, self.videos, abandon_sec=60)
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(get_upload(self.db, str(session["upload_id"])))

    def test_migrate_sessions_to_separate_uploads_db(self) -> None:
        payload = _ffmpeg_mp4(self.root / "m.mp4", "0.4")
        session = init_upload(
            self.db,
            self.videos,
            name="m.mp4",
            size_bytes=len(payload),
            client_key="mig|m",
            chunk_size=16 * 1024,
            disk_limit_bytes=self.limit,
        )
        uploads = self.root / "video_uploads.db"
        ensure_upload_tables(uploads)
        moved = migrate_upload_sessions(self.db, uploads)
        self.assertGreaterEqual(moved, 1)
        body = get_upload(uploads, str(session["upload_id"]))
        self.assertIsNotNone(body)
        self.assertEqual(body["name"], "m.mp4")


class ParallelChunkMapTests(unittest.TestCase):
    """Nhiều PUT cùng lúc không được ghi đè received_map của nhau."""

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

    def test_parallel_puts_keep_every_chunk(self) -> None:
        import threading

        chunk = 16 * 1024
        total = 8
        session = init_upload(
            self.db,
            self.videos,
            name="parallel.mp4",
            size_bytes=chunk * total,
            device="bench",
            client_key="bench|parallel",
            chunk_size=chunk,
            disk_limit_bytes=self.limit,
        )
        upload_id = str(session["upload_id"])
        payload = b"x" * chunk
        errors: list[BaseException] = []

        def one(index: int) -> None:
            try:
                body = put_chunk(self.db, upload_id, index, payload)
                if body.get("index") != index:
                    raise AssertionError(body)
            except BaseException as exc:  # noqa: BLE001 — gom lỗi từ thread
                errors.append(exc)

        threads = [threading.Thread(target=one, args=(index,)) for index in range(total)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        body = get_upload(self.db, upload_id)
        assert body is not None
        self.assertEqual(sorted(body["received"]), list(range(total)))
        self.assertEqual(body["received_bytes"], chunk * total)


if __name__ == "__main__":
    unittest.main()
