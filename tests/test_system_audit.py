"""Kiểm tra biên API video, hàng đợi, sao lưu và tính nhất quán thống kê."""

from __future__ import annotations

import gzip
import io
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.app import app
from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.settings import settings
from control_plane.video_store import (
    begin_upload,
    claim,
    commit_upload,
    count_results,
    fail,
    finish,
    init_db,
    iter_backup,
    list_videos,
    queued_bytes,
    rematch_results,
    retry,
    search_results,
    stats,
)


class SystemAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "video.db"
        self.video_dir = Path(self._tmp.name) / "videos"
        self.video_dir.mkdir()
        init_db(self.db)
        self._old_db = settings.video_db_path
        self._old_dir = settings.video_dir
        self._old_token = settings.token
        self._old_page = settings.page_token
        settings.video_db_path = self.db
        settings.video_dir = self.video_dir
        settings.token = "audit-token"
        settings.page_token = "audit-page"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        settings.video_db_path = self._old_db
        settings.video_dir = self._old_dir
        settings.token = self._old_token
        settings.page_token = self._old_page
        self._tmp.cleanup()

    def _auth(self) -> dict[str, str]:
        return {"Authorization": "Bearer audit-page"}

    def _seed(self) -> int:
        created = begin_upload(self.db, name="a.mp4", size_bytes=12, sha256="seed-a", device="iPhone")
        video_id = int(created["id"])
        path = self.video_dir / f"{video_id}.mp4"
        path.write_bytes(b"123456789012")
        commit_upload(self.db, video_id, str(path))
        finish(
            self.db,
            video_id,
            Table(
                rows=[Row("0982117072", "Đặng Thị Tâm", "@dangtam.3")],
                unopened=[Unopened("0332001753", "khactam")],
                review=[Review("", "", "alone", "@alone.1", "đã mở hồ sơ nhưng chưa thấy số")],
            ),
        )
        return video_id

    def test_results_views_and_bad_view(self) -> None:
        self._seed()
        ok = self.client.get("/v1/results?view=complete", headers=self._auth())
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertEqual(body["view"], "complete")
        self.assertTrue(all(item["phone"] and item["username"] for item in body["items"]))
        bad = self.client.get("/v1/results?view=weird", headers=self._auth())
        self.assertEqual(bad.status_code, 422)
        incomplete = self.client.get("/v1/results?view=incomplete", headers=self._auth()).json()
        self.assertTrue(incomplete["total"] >= 1)
        self.assertTrue(all((not i["phone"]) or (not i["username"]) for i in incomplete["items"]))

    def test_stats_queue_and_backup_roundtrip(self) -> None:
        self._seed()
        stats_body = self.client.get("/v1/videos/stats", headers=self._auth()).json()
        self.assertEqual(stats_body["saved"], 1)
        self.assertEqual(stats_body["unopened"], 1)
        self.assertEqual(stats_body["review"], 1)
        videos = self.client.get("/v1/videos", headers=self._auth()).json()
        self.assertEqual(videos["items"][0]["saved_count"], 1)
        ticket = self.client.post("/v1/backup/ticket", headers=self._auth()).json()["url"]
        raw = self.client.get(ticket)
        self.assertEqual(raw.status_code, 200)
        text = gzip.decompress(raw.content).decode("utf-8-sig")
        self.assertIn("Số điện thoại,Tên,Username,Time quét,Tên máy,Video", text)
        self.assertIn("0982117072", text)
        self.assertNotIn(",Loại,", text)

    def test_claim_fail_retry_and_duplicate_upload(self) -> None:
        created = begin_upload(self.db, name="b.mp4", size_bytes=8, sha256="seed-b")
        video_id = int(created["id"])
        path = self.video_dir / f"{video_id}.mp4"
        path.write_bytes(b"abcdefgh")
        commit_upload(self.db, video_id, str(path))
        job = claim(self.db, 4242)
        self.assertEqual(int(job["id"]), video_id)
        fail(self.db, video_id, "hỏng thử")
        self.assertTrue(retry(self.db, video_id))
        again = begin_upload(self.db, name="b-copy.mp4", size_bytes=8, sha256="seed-b")
        self.assertTrue(again["duplicate"])
        self.assertEqual(queued_bytes(self.db), 8)

    def test_homepage_has_new_controls(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        text = page.text
        self.assertIn("viewComplete", text)
        self.assertIn("mở hồ sơ", text)
        self.assertIn("Đủ 3 cột", text)
        self.assertNotIn("100 dòng mới nhất", text)
        self.assertIn("Time quét", text)

    def test_rematch_is_idempotent_and_counts_match(self) -> None:
        self._seed()
        self.assertEqual(rematch_results(self.db), 0)
        self.assertEqual(rematch_results(self.db), 0)
        complete = count_results(self.db, view="complete")
        incomplete = count_results(self.db, view="incomplete")
        all_n = count_results(self.db, view="all")
        self.assertEqual(complete + incomplete, all_n)
        self.assertEqual(stats(self.db)["results"], all_n)
        listed = list_videos(self.db)
        self.assertEqual(listed[0]["device"], "iPhone")

    def test_unauthorized_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/v1/results").status_code, 401)
        self.assertEqual(self.client.get("/v1/videos/stats").status_code, 401)

    def test_init_db_migration_is_reentrant(self) -> None:
        init_db(self.db)
        init_db(self.db)
        # Giả lập tiến trình thứ hai gặp cột device đã có.
        from control_plane.video_store import connect, _ensure_video_columns, _reconcile_result_counts

        with connect(self.db) as conn:
            _ensure_video_columns(conn)
            _ensure_video_columns(conn)
            conn.execute("DELETE FROM result_counts")
            conn.execute("INSERT INTO result_counts (bucket, n) VALUES (1, 999)")
            _reconcile_result_counts(conn)
            real = {int(r[0]): int(r[1]) for r in conn.execute("SELECT bucket, COUNT(*) FROM results GROUP BY bucket")}
            cached = {int(r[0]): int(r[1]) for r in conn.execute("SELECT bucket, n FROM result_counts")}
            self.assertEqual(cached, real)


if __name__ == "__main__":
    unittest.main()
