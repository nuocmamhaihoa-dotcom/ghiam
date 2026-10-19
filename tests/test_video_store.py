"""Hàng đợi video, ghép kết quả và bản sao lưu tải về."""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.settings import env_float, env_int
from control_plane.video_store import (
    abort_upload,
    begin_upload,
    can_accept,
    claim,
    commit_upload,
    fail,
    feeder_count,
    finish,
    free_before_this_file,
    init_db,
    iter_backup,
    list_videos,
    merge_close_results,
    queued_bytes,
    recover_dead,
    requeue,
    requeue_running,
    retry,
    search_results,
    stats,
    worker_count,
)


class VideoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "video.db"
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, name: str, sha: str, size: int = 10) -> int:
        created = begin_upload(self.db, name=name, size_bytes=size, sha256=sha)
        self.assertFalse(created["duplicate"])
        video_id = int(created["id"])
        path = Path(self._tmp.name) / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, video_id, str(path))
        return video_id

    def test_same_file_is_not_queued_twice(self) -> None:
        first = self._add("a.mp4", "abc")
        again = begin_upload(self.db, name="a-copy.mp4", size_bytes=10, sha256="abc")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], first)
        self.assertEqual(stats(self.db)["queued"], 1)

    def test_two_workers_do_not_take_the_same_video(self) -> None:
        self._add("a.mp4", "a")
        self._add("b.mp4", "b")
        first = claim(self.db, 11)
        second = claim(self.db, 12)
        third = claim(self.db, 13)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(third)
        self.assertNotEqual(first["id"], second["id"])

    def test_dead_worker_returns_the_video_to_the_queue(self) -> None:
        self._add("a.mp4", "a")
        claim(self.db, 999999)
        self.assertEqual(recover_dead(self.db), 1)
        again = claim(self.db, 5)
        self.assertEqual(again["name"], "a.mp4")

    def test_saved_row_is_not_overwritten_and_unopened_can_be_completed(self) -> None:
        video_id = self._add("a.mp4", "a")
        finish(
            self.db,
            video_id,
            Table(
                rows=[Row("0982117072", "Đặng Thị Tâm", "@dangtam.3")],
                unopened=[Unopened("0332001753", "khactam")],
                review=[Review("0900000001", "Một", "Khác", "@khac", "tên danh bạ và tên hồ sơ khác nhau")],
            ),
        )
        self.assertEqual(stats(self.db)["saved"], 1)
        self.assertEqual(stats(self.db)["unopened"], 1)
        self.assertEqual(stats(self.db)["review"], 1)
        second = self._add("b.mp4", "b")
        finish(
            self.db,
            second,
            Table(
                rows=[
                    Row("0982117072", "Tên khác", "@khac"),
                    Row("0332001753", "khactam", "@khactam60"),
                ]
            ),
        )
        found = {item["phone"]: item for item in search_results(self.db, limit=20)}
        self.assertEqual(found["0982117072"]["username"], "@dangtam.3")
        self.assertEqual(found["0332001753"]["username"], "@khactam60")
        self.assertEqual(found["0332001753"]["bucket"], "Đã lưu")
        self.assertEqual(stats(self.db)["results"], 3)

    def test_close_names_in_one_video_merge_and_other_videos_stay_apart(self) -> None:
        first = self._add("a.mp4", "a")
        finish(
            self.db,
            first,
            Table(
                unopened=[
                    Unopened("0982117072", "ÿHanhnguyen"),
                    Unopened("0900000001", "Lý Mai Trang"),
                    Unopened("0900000002", "Lý Mai Trang"),
                    Unopened("0900000003", "Quang Le"),
                ],
                review=[
                    Review("", "", "Hanhnguyen", "@hanhnguyenn375", "đã mở hồ sơ nhưng chưa thấy số"),
                    Review("", "", "Ly Mai Trang", "@user1", "đã mở hồ sơ nhưng chưa thấy số"),
                    Review("", "", "tân", "@tn1", "đã mở hồ sơ nhưng chưa thấy số"),
                ],
            ),
        )
        second = self._add("b.mp4", "b")
        finish(
            self.db,
            second,
            Table(
                unopened=[Unopened("0911111111", "Dang Thi Tam")],
                review=[
                    Review("", "", "Đặng Thị Tâm", "@dangtam.3", "đã mở hồ sơ nhưng chưa thấy số"),
                    Review("", "", "Quang Le", "@quang.le354", "đã mở hồ sơ nhưng chưa thấy số"),
                ],
            ),
        )
        self.assertEqual(merge_close_results(self.db), 2)
        found = {item["phone"]: item for item in search_results(self.db, limit=20)}
        self.assertEqual(found["0982117072"]["username"], "@hanhnguyenn375")
        self.assertEqual(found["0982117072"]["bucket"], "Đã lưu")
        self.assertEqual(found["0911111111"]["username"], "@dangtam.3")
        self.assertEqual(found["0900000001"]["username"], "")
        self.assertEqual(found["0900000002"]["username"], "")
        self.assertEqual(found["0900000003"]["username"], "")
        listed = search_results(self.db, limit=20)
        self.assertTrue(any(item["username"] == "@user1" and item["phone"] == "" for item in listed))
        self.assertTrue(any(item["username"] == "@quang.le354" and item["phone"] == "" for item in listed))
        self.assertEqual(merge_close_results(self.db), 0)

    def test_counts_follow_every_insert_upgrade_and_merge(self) -> None:
        first = self._add("a.mp4", "a")
        finish(
            self.db,
            first,
            Table(
                unopened=[Unopened("0332001753", "khactam")],
                review=[Review("", "", "khactam", "@khactam60", "đã mở hồ sơ nhưng chưa thấy số")],
            ),
        )
        self.assertEqual((stats(self.db)["saved"], stats(self.db)["review"], stats(self.db)["unopened"]), (0, 1, 1))
        second = self._add("b.mp4", "b")
        finish(self.db, second, Table(rows=[Row("0332001753", "khactam", "@khactam60")]))
        counted = stats(self.db)
        self.assertEqual((counted["saved"], counted["review"], counted["unopened"]), (1, 0, 0))
        listed = search_results(self.db, limit=20)
        self.assertEqual([(item["phone"], item["username"]) for item in listed], [("0332001753", "@khactam60")])

    def test_lone_username_is_not_added_when_it_already_has_a_phone(self) -> None:
        first = self._add("a.mp4", "a")
        finish(self.db, first, Table(rows=[Row("0332001753", "khactam", "@khactam60")]))
        second = self._add("b.mp4", "b")
        finish(
            self.db,
            second,
            Table(review=[Review("", "", "khactam", "@khactam60", "đã mở hồ sơ nhưng chưa thấy số")]),
        )
        self.assertEqual(stats(self.db)["results"], 1)

    def test_search_by_phone_prefix_and_username(self) -> None:
        video_id = self._add("a.mp4", "a")
        finish(
            self.db,
            video_id,
            Table(rows=[Row("0332001753", "khactam", "@khactam60"), Row("0982117072", "Đặng Thị Tâm", "@dangtam.3")]),
        )
        self.assertEqual([item["phone"] for item in search_results(self.db, "0332")], ["0332001753"])
        self.assertEqual([item["phone"] for item in search_results(self.db, "+84982")], ["0982117072"])
        self.assertEqual([item["phone"] for item in search_results(self.db, "dangtam")], ["0982117072"])
        self.assertEqual([item["phone"] for item in search_results(self.db, "@khac")], ["0332001753"])
        self.assertEqual(search_results(self.db, "0999"), [])

    def test_finished_video_can_be_uploaded_again_and_reread(self) -> None:
        video_id = self._add("a.mp4", "same")
        claim(self.db, 7)
        finish(self.db, video_id, Table())
        again = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same")
        self.assertFalse(again["duplicate"])
        self.assertTrue(again["reopened"])
        self.assertEqual(again["id"], video_id)
        self.assertEqual(stats(self.db)["uploading"], 1)
        abort_upload(self.db, video_id, reopened=True)
        self.assertEqual(stats(self.db)["error"], 1)
        self.assertEqual(len(list_videos(self.db)), 1)

    def test_video_waiting_in_the_queue_is_still_a_duplicate(self) -> None:
        self._add("a.mp4", "same")
        again = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same")
        self.assertTrue(again["duplicate"])

    def test_error_videos_kept_on_disk_count_toward_the_limit(self) -> None:
        video_id = self._add("a.mp4", "err", size=500)
        claim(self.db, 7)
        fail(self.db, video_id, "hỏng")
        self.assertEqual(queued_bytes(self.db), 500)

    def test_unopened_phone_keeps_the_username_seen_later(self) -> None:
        first = self._add("a.mp4", "a")
        finish(self.db, first, Table(unopened=[Unopened("0332001753", "khactam")]))
        second = self._add("b.mp4", "b")
        finish(
            self.db,
            second,
            Table(review=[Review("0332001753", "khactam", "", "@khactam60", "không đọc được tên hồ sơ")]),
        )
        found = search_results(self.db, "0332001753")[0]
        self.assertEqual(found["username"], "@khactam60")
        self.assertEqual(found["bucket"], "Cần xem")
        self.assertEqual((stats(self.db)["review"], stats(self.db)["unopened"]), (1, 0))

    def test_startup_returns_half_read_videos_to_the_queue(self) -> None:
        self._add("a.mp4", "a")
        claim(self.db, 4242)
        self.assertEqual(stats(self.db)["running"], 1)
        self.assertEqual(requeue_running(self.db, os.getpid()), 1)
        self.assertEqual(stats(self.db)["queued"], 1)

    def test_backup_contains_the_saved_row(self) -> None:
        video_id = self._add("clip.mp4", "a")
        finish(self.db, video_id, Table(rows=[Row("0332001753", "khactam", "@khactam60")]))
        text = "".join(iter_backup(self.db))
        self.assertIn("Số điện thoại,Tên,Username", text)
        self.assertIn("0332001753,khactam,@khactam60,Đã lưu,,clip.mp4", text)

    def test_error_can_be_retried_only_while_the_file_remains(self) -> None:
        video_id = self._add("a.mp4", "a")
        job = claim(self.db, 4)
        fail(self.db, int(job["id"]), "hỏng")
        self.assertTrue(retry(self.db, video_id))
        self.assertEqual(stats(self.db)["queued"], 1)
        Path(str(claim(self.db, 4)["path"])).unlink()
        fail(self.db, video_id, "hỏng lần nữa")
        self.assertFalse(retry(self.db, video_id))

    def test_disk_limit(self) -> None:
        gb = 1024 * 1024 * 1024
        self.assertTrue(can_accept(0, 100, 80 * gb, 10 * gb))
        self.assertFalse(can_accept(79 * gb, 2 * gb, 80 * gb, 10 * gb))
        self.assertFalse(can_accept(0, 100, 80 * gb, 1 * gb))
        self.assertEqual(queued_bytes(self.db), 0)

    def test_free_space_is_measured_before_the_temp_file(self) -> None:
        gb = 1024 * 1024 * 1024
        free_after = 2 * gb + 50
        written = 100
        # Sau khi ghi, chỗ trống chỉ hơn 2 GB một chút. Cộng lại file vừa ghi thì vẫn đủ chỗ.
        self.assertTrue(can_accept(0, written, 80 * gb, free_before_this_file(free_after, written)))
        self.assertFalse(can_accept(0, written, 80 * gb, free_after))
        self.assertFalse(can_accept(0, written, 80 * gb, free_before_this_file(gb, written)))

    def test_stale_upload_frees_quota_and_accepts_the_same_file_again(self) -> None:
        created = begin_upload(self.db, name="a.mp4", size_bytes=500, sha256="stuck")
        video_id = int(created["id"])
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET created_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", video_id),
            )
        self.assertEqual(queued_bytes(self.db), 0)
        self.assertEqual(stats(self.db)["error"], 1)
        self.assertEqual(stats(self.db)["uploading"], 0)
        again = begin_upload(self.db, name="a.mp4", size_bytes=500, sha256="stuck")
        self.assertFalse(again["duplicate"])
        self.assertTrue(again["reopened"])
        self.assertEqual(again["id"], video_id)

    def test_fresh_upload_is_still_a_duplicate(self) -> None:
        begin_upload(self.db, name="a.mp4", size_bytes=500, sha256="live")
        again = begin_upload(self.db, name="a.mp4", size_bytes=500, sha256="live")
        self.assertTrue(again["duplicate"])
        self.assertEqual(queued_bytes(self.db), 500)

    def test_late_worker_does_not_overwrite_a_requeued_video(self) -> None:
        video_id = self._add("a.mp4", "late")
        claim(self.db, 42)
        requeue(self.db, video_id)
        saved = finish(
            self.db,
            video_id,
            Table(rows=[Row("0332001753", "khactam", "@khactam60")]),
            owner_pid=42,
        )
        self.assertFalse(saved)
        self.assertFalse(fail(self.db, video_id, "đến muộn", owner_pid=42))
        self.assertEqual(stats(self.db)["queued"], 1)
        self.assertEqual(stats(self.db)["saved"], 0)
        self.assertEqual(search_results(self.db, "0332"), [])
        claim(self.db, 42)
        self.assertTrue(
            finish(self.db, video_id, Table(rows=[Row("0332001753", "khactam", "@khactam60")]), owner_pid=42)
        )
        self.assertEqual(stats(self.db)["done"], 1)

    def test_bad_worker_env_falls_back(self) -> None:
        previous_workers = os.environ.get("CONTROL_VIDEO_WORKERS")
        previous_feeders = os.environ.get("CONTROL_VIDEO_FEEDERS")
        os.environ["CONTROL_VIDEO_WORKERS"] = "không-phải-số"
        os.environ["CONTROL_VIDEO_FEEDERS"] = "không"
        try:
            self.assertGreaterEqual(worker_count(), 1)
            self.assertEqual(feeder_count(), 3)
            os.environ["CONTROL_VIDEO_FEEDERS"] = "4"
            self.assertEqual(feeder_count(), 4)
        finally:
            if previous_workers is None:
                os.environ.pop("CONTROL_VIDEO_WORKERS", None)
            else:
                os.environ["CONTROL_VIDEO_WORKERS"] = previous_workers
            if previous_feeders is None:
                os.environ.pop("CONTROL_VIDEO_FEEDERS", None)
            else:
                os.environ["CONTROL_VIDEO_FEEDERS"] = previous_feeders

    def test_bad_settings_env_falls_back(self) -> None:
        previous_port = os.environ.get("CONTROL_PORT")
        previous_timeout = os.environ.get("CONTROL_PROXY_CHECK_TIMEOUT")
        os.environ["CONTROL_PORT"] = "abc"
        os.environ["CONTROL_PROXY_CHECK_TIMEOUT"] = "chậm"
        try:
            self.assertEqual(env_int("CONTROL_PORT", 8088), 8088)
            self.assertEqual(env_float("CONTROL_PROXY_CHECK_TIMEOUT", 8.0), 8.0)
        finally:
            if previous_port is None:
                os.environ.pop("CONTROL_PORT", None)
            else:
                os.environ["CONTROL_PORT"] = previous_port
            if previous_timeout is None:
                os.environ.pop("CONTROL_PROXY_CHECK_TIMEOUT", None)
            else:
                os.environ["CONTROL_PROXY_CHECK_TIMEOUT"] = previous_timeout


class BackupGzipTests(unittest.TestCase):
    def test_roundtrip_gzip_header(self) -> None:
        raw = gzip.compress("Số điện thoại\n".encode("utf-8-sig"))
        self.assertTrue(gzip.decompress(raw).startswith("Số điện thoại".encode("utf-8-sig")))


if __name__ == "__main__":
    unittest.main()
