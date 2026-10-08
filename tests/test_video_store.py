"""Hàng đợi video, ghép kết quả và bản sao lưu tải về."""

from __future__ import annotations

import gzip
import os
import tempfile
import unittest
from pathlib import Path

from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.video_store import (
    abort_upload,
    begin_upload,
    can_accept,
    claim,
    commit_upload,
    count_results,
    fail,
    finish,
    init_db,
    iter_backup,
    list_videos,
    rematch_results,
    queued_bytes,
    recover_dead,
    requeue_running,
    retry,
    search_results,
    stats,
)


class VideoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "video.db"
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, name: str, sha: str, size: int = 10, device: str = "") -> int:
        created = begin_upload(self.db, name=name, size_bytes=size, sha256=sha, device=device)
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

    def test_close_names_merge_inside_finish_and_global_rematch(self) -> None:
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
        found = {item["phone"]: item for item in search_results(self.db, limit=20)}
        self.assertEqual(found["0982117072"]["username"], "@hanhnguyenn375")
        self.assertEqual(found["0982117072"]["bucket"], "Đã lưu")
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
        self.assertEqual(rematch_results(self.db), 1)
        found = {item["phone"]: item for item in search_results(self.db, limit=20)}
        self.assertEqual(found["0911111111"]["username"], "@dangtam.3")
        self.assertEqual(found["0900000003"]["username"], "@quang.le354")
        self.assertEqual(found["0900000001"]["username"], "")
        self.assertEqual(found["0900000002"]["username"], "")
        listed = search_results(self.db, limit=20)
        self.assertTrue(any(item["username"] == "@user1" and item["phone"] == "" for item in listed))
        self.assertFalse(any(item["username"] == "@quang.le354" and item["phone"] == "" for item in listed))
        self.assertEqual(rematch_results(self.db), 0)

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
        # finish đã ghép ngay thành một hàng ngang đủ số + username
        self.assertEqual((stats(self.db)["saved"], stats(self.db)["review"], stats(self.db)["unopened"]), (1, 0, 0))
        listed = search_results(self.db, limit=20)
        self.assertEqual([(item["phone"], item["username"]) for item in listed], [("0332001753", "@khactam60")])
        second = self._add("b.mp4", "b")
        finish(self.db, second, Table(rows=[Row("0332001753", "khactam", "@khactam60")]))
        counted = stats(self.db)
        self.assertEqual((counted["saved"], counted["review"], counted["unopened"]), (1, 0, 0))

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
        video_id = self._add("IMG_0018.MOV", "a", device="iPhone An")
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
        by_video = search_results(self.db, "IMG_0018")
        self.assertEqual(len(by_video), 2)
        self.assertEqual(by_video[0]["video"], "IMG_0018.MOV")
        self.assertEqual(by_video[0]["device"], "iPhone An")
        self.assertTrue(by_video[0]["scanned_at"])
        self.assertEqual(count_results(self.db, "IMG_0018"), 2)
        self.assertEqual(len(search_results(self.db, "IMG_0018", limit=1, offset=1)), 1)
        listed = list_videos(self.db)
        self.assertEqual(listed[0]["device"], "iPhone An")
        self.assertEqual(listed[0]["saved_count"], 2)
        self.assertEqual(listed[0]["result_count"], 2)

    def test_same_name_merge_joins_phone_and_username_rows(self) -> None:
        video_id = self._add("clip.mp4", "merge-handle")
        finish(
            self.db,
            video_id,
            Table(
                unopened=[Unopened("0985721500", "uniquehandle")],
                review=[Review("", "", "OCR lech", "@uniquehandle99", "đã mở hồ sơ nhưng chưa thấy số")],
            ),
        )
        found = search_results(self.db, "0985721500")[0]
        self.assertEqual(found["username"], "@uniquehandle99")
        self.assertEqual(found["bucket"], "Đã lưu")
        self.assertEqual(stats(self.db)["results"], 1)
        self.assertEqual(rematch_results(self.db), 0)

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
        video_id = self._add("clip.mp4", "a", device="iPhone 13")
        finish(self.db, video_id, Table(rows=[Row("0332001753", "khactam", "@khactam60")]))
        text = "".join(iter_backup(self.db))
        self.assertIn("Số điện thoại,Tên,Username,Time quét,Tên máy,Video", text)
        self.assertNotIn(",Loại,", text)
        self.assertIn("0332001753,khactam,@khactam60,", text)
        self.assertIn(",iPhone 13,clip.mp4", text)

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


class BackupGzipTests(unittest.TestCase):
    def test_roundtrip_gzip_header(self) -> None:
        raw = gzip.compress("Số điện thoại\n".encode("utf-8-sig"))
        self.assertTrue(gzip.decompress(raw).startswith("Số điện thoại".encode("utf-8-sig")))


if __name__ == "__main__":
    unittest.main()
