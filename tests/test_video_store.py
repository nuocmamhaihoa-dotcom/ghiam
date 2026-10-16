"""Hàng đợi video, ghép kết quả và bản sao lưu tải về."""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.video_store import (
    abort_upload,
    backfill_video_counts,
    begin_upload,
    can_accept,
    claim,
    claim_pc,
    clean_device,
    commit_upload,
    confirm_open_profiles,
    connect,
    device_day,
    fail,
    finish,
    init_db,
    iter_backup,
    list_videos,
    merge_close_results,
    queued_bytes,
    disk_limit_bytes,
    drop_stale_uploads,
    prune_old_videos,
    pc_video_path,
    recover_dead,
    release_pc,
    release_stale_pc,
    requeue_running,
    result_page,
    retry,
    search_results,
    stats,
    touch_pc,
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

    def test_upload_abandoned_for_half_an_hour_is_removed(self) -> None:
        fresh = begin_upload(self.db, name="dang-tai.mp4", size_bytes=10, sha256="fresh")
        stale = begin_upload(self.db, name="dut.mp4", size_bytes=10, sha256="stale")
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (stale["id"],))
        self.assertEqual(drop_stale_uploads(self.db), 1)
        self.assertEqual(stats(self.db)["uploading"], 1)
        self.assertEqual(list_videos(self.db)[0]["id"], fresh["id"])

    def test_video_list_is_not_capped(self) -> None:
        with connect(self.db) as conn:
            conn.executemany(
                """
                INSERT INTO videos (name, size_bytes, sha256, status, created_at)
                VALUES (?, 1, ?, 'queued', '2026-10-15T00:00:00+00:00')
                """,
                [(f"{index}.mp4", f"sha-{index}") for index in range(250)],
            )
        listed = list_videos(self.db)
        self.assertEqual(len(listed), 250)
        self.assertEqual(listed[0]["name"], "249.mp4")
        self.assertEqual([item["name"] for item in list_videos(self.db, 3)], ["249.mp4", "248.mp4", "247.mp4"])

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

    def test_result_page_lists_every_row_newest_first(self) -> None:
        first = self._add("a.mp4", "a")
        finish(self.db, first, Table(rows=[Row("0900000001", "Mot", "@mot")]))
        second = self._add("b.mp4", "b")
        finish(self.db, second, Table(rows=[Row("0900000002", "Hai", "@hai")]))
        third = self._add("c.mp4", "c")
        finish(self.db, third, Table(rows=[Row("0900000003", "Ba", "@ba")]))
        page = result_page(self.db, limit=2)
        self.assertEqual([item["phone"] for item in page["items"]], ["0900000003", "0900000002"])
        self.assertEqual(page["total"], 3)
        self.assertGreater(int(page["before"]), 0)
        older = result_page(self.db, limit=2, before=int(page["before"]))
        self.assertEqual([item["phone"] for item in older["items"]], ["0900000001"])
        self.assertEqual(older["before"], 0)
        self.assertEqual(older["total"], 3)
        newest_id = int(page["items"][0]["id"])
        newer = result_page(self.db, limit=2, after=newest_id)
        self.assertEqual(newer["items"], [])
        self.assertEqual(newer["before"], 0)
        prefix = result_page(self.db, "0900", limit=2)
        self.assertEqual([item["phone"] for item in prefix["items"]], ["0900000003", "0900000002"])
        self.assertEqual(prefix["total"], 3)
        self.assertEqual([item["phone"] for item in search_results(self.db, limit=10)], ["0900000003", "0900000002", "0900000001"])

    def test_finished_video_counts_phones_and_complete_rows(self) -> None:
        video_id = self._add("a.mp4", "a")
        finish(
            self.db,
            video_id,
            Table(
                rows=[
                    Row("0911111111", "An", "@an"),
                    Row("0922222222", "Binh", ""),
                    Row("0911111111", "An", "@an"),
                ],
                unopened=[Unopened("0933333333", "Chi"), Unopened("0911111111", "An")],
                review=[Review("0944444444", "Dung", "Dung", "@dung", "tên chưa khớp")],
            ),
        )
        listed = list_videos(self.db)[0]
        self.assertEqual(listed["phone_count"], 4)
        self.assertEqual(listed["full_count"], 1)

    def test_unopened_only_video_has_phones_but_no_complete_rows(self) -> None:
        video_id = self._add("a.mp4", "a")
        finish(
            self.db,
            video_id,
            Table(unopened=[Unopened("0911111111", "An"), Unopened("0922222222", "Binh")]),
        )
        listed = list_videos(self.db)[0]
        self.assertEqual((listed["phone_count"], listed["full_count"]), (2, 0))

    def test_backfill_fills_old_done_videos_and_leaves_the_queue_alone(self) -> None:
        done = self._add("a.mp4", "a")
        finish(self.db, done, Table(rows=[Row("0911111111", "An", "@an")], unopened=[Unopened("0922222222", "Binh")]))
        waiting = self._add("b.mp4", "b")
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET phone_count = NULL, full_count = NULL")
        self.assertEqual(backfill_video_counts(self.db), 1)
        by_id = {item["id"]: item for item in list_videos(self.db)}
        self.assertEqual((by_id[done]["phone_count"], by_id[done]["full_count"]), (2, 1))
        self.assertIsNone(by_id[waiting]["phone_count"])
        self.assertEqual(backfill_video_counts(self.db), 0)

    def test_old_database_gains_count_columns(self) -> None:
        path = Path(self._tmp.name) / "old.db"
        raw = sqlite3.connect(path)
        raw.execute(
            """
            CREATE TABLE videos (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              path TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              error TEXT NOT NULL DEFAULT '',
              pid INTEGER,
              created_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT
            )
            """
        )
        raw.execute(
            """
            INSERT INTO videos (name, size_bytes, sha256, status, created_at)
            VALUES ('cu.mp4', 1, 'cu', 'done', '2020-01-01T00:00:00+00:00')
            """
        )
        raw.commit()
        raw.close()
        init_db(path)
        self.assertEqual(backfill_video_counts(path), 1)
        listed = list_videos(path)[0]
        self.assertEqual((listed["phone_count"], listed["full_count"]), (0, 0))
        self.assertEqual(listed["device"], "")

    def test_phone_name_stays_with_the_video_and_the_day_is_vietnam_time(self) -> None:
        from datetime import datetime, timezone

        self.assertEqual(clean_device("  Máy   1 \n"), "Máy 1")
        self.assertEqual(len(clean_device("x" * 80)), 40)
        now = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
        yesterday = "2026-10-14T16:30:00+00:00"
        today = "2026-10-14T17:30:00+00:00"
        arrived = begin_upload(self.db, name="hom-nay.mp4", size_bytes=10, sha256="today", device="  Máy 1 ")
        self.assertEqual(arrived["device"], "Máy 1")
        commit_upload(self.db, int(arrived["id"]), str(Path(self._tmp.name) / "hom-nay.mp4"))
        late_error = self._add("loi.mp4", "err-phone")
        other = begin_upload(self.db, name="may-2.mp4", size_bytes=10, sha256="phone-2", device="Máy 2")
        commit_upload(self.db, int(other["id"]), str(Path(self._tmp.name) / "may-2.mp4"))
        claim(self.db, 4)
        finish(self.db, int(other["id"]), Table())
        still = begin_upload(self.db, name="dang.mp4", size_bytes=10, sha256="still", device="Máy 2")
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET created_at = ? WHERE id = ?", (today, arrived["id"]))
            conn.execute(
                "UPDATE videos SET device = ?, created_at = ?, finished_at = ?, status = 'error' WHERE id = ?",
                ("Máy 1", yesterday, today, late_error),
            )
            conn.execute(
                "UPDATE videos SET created_at = ?, finished_at = ? WHERE id = ?",
                (today, today, other["id"]),
            )
            conn.execute("UPDATE videos SET created_at = ? WHERE id = ?", (today, still["id"]))
        report = device_day(self.db, now)
        self.assertEqual(report["day"], "2026-10-15")
        by_phone = {item["device"]: item for item in report["items"]}
        self.assertEqual(by_phone["Máy 1"]["uploaded"], 1)
        self.assertEqual(by_phone["Máy 1"]["errors"], 1)
        self.assertEqual(by_phone["Máy 1"]["waiting"], 1)
        self.assertEqual((by_phone["Máy 2"]["uploaded"], by_phone["Máy 2"]["done"], by_phone["Máy 2"]["errors"]), (1, 1, 0))
        self.assertEqual(by_phone["Máy 2"]["waiting"], 1)
        self.assertEqual(
            {item["name"] for item in list_videos(self.db, device="Máy 1")},
            {"hom-nay.mp4", "loi.mp4"},
        )
        self.assertEqual(
            {item["name"] for item in list_videos(self.db, device="Máy 2")},
            {"may-2.mp4", "dang.mp4"},
        )

    def test_a_queued_file_keeps_the_phone_that_uploaded_it_first(self) -> None:
        first = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same-phone", device="Máy 1")
        commit_upload(self.db, int(first["id"]), str(Path(self._tmp.name) / "a.mp4"))
        again = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same-phone", device="Máy 2")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["device"], "Máy 1")
        claim(self.db, 4)
        finish(self.db, int(first["id"]), Table())
        reread = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same-phone", device="Máy 2")
        self.assertEqual(reread["device"], "Máy 2")
        self.assertEqual(list_videos(self.db)[0]["device"], "Máy 2")
        kept = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same-phone")
        self.assertTrue(kept["duplicate"])
        abort_upload(self.db, int(reread["id"]), reopened=True)
        blank = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same-phone")
        self.assertFalse(blank["duplicate"])
        self.assertEqual(blank["device"], "Máy 2")

    def test_review_rows_with_a_matching_handle_become_saved(self) -> None:
        video_id = self._add("a.mp4", "a")
        finish(
            self.db,
            video_id,
            Table(
                unopened=[Unopened("0919188997", "Hồng Mến")],
                review=[
                    Review("0919313090", "Truc", "khác", "@truc99", "tên gần giống, cần xem"),
                    Review("0900000002", "Phương Anh", "Phương An", "@anhphonglacuatao", "tên gần giống, cần xem"),
                    Review("", "", "", "@hongmen999", "không đọc được tên trên hồ sơ"),
                ],
            ),
        )
        self.assertGreater(confirm_open_profiles(self.db), 0)
        found = {item["phone"]: item for item in search_results(self.db, limit=20)}
        self.assertEqual(found["0919313090"]["bucket"], "Đã lưu")
        self.assertEqual(found["0919313090"]["username"], "@truc99")
        self.assertEqual(found["0919188997"]["bucket"], "Đã lưu")
        self.assertEqual(found["0919188997"]["username"], "@hongmen999")
        self.assertEqual(found["0900000002"]["bucket"], "Cần xem")
        self.assertEqual(found["0900000002"]["username"], "@anhphonglacuatao")
        self.assertEqual(confirm_open_profiles(self.db), 0)

    def test_finished_video_can_be_uploaded_again_and_reread(self) -> None:
        video_id = self._add("a.mp4", "same")
        claim(self.db, 7)
        finish(self.db, video_id, Table())
        again = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same")
        self.assertFalse(again["duplicate"])
        self.assertTrue(again["reopened"])
        self.assertEqual(again["id"], video_id)
        self.assertEqual(stats(self.db)["uploading"], 1)
        reopened = list_videos(self.db)[0]
        self.assertIsNone(reopened["phone_count"])
        self.assertIsNone(reopened["full_count"])
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

    def test_finished_video_kept_on_disk_can_be_read_again(self) -> None:
        video_id = self._add("a.mp4", "a")
        claim(self.db, 4)
        finish(self.db, video_id, Table(unopened=[Unopened("0919313090", "Truc")]))
        item = list_videos(self.db)[0]
        self.assertTrue(item["has_file"])
        self.assertNotIn("path", item)
        self.assertTrue(retry(self.db, video_id))
        self.assertEqual(stats(self.db)["queued"], 1)
        self.assertIsNone(list_videos(self.db)[0]["phone_count"])
        claim(self.db, 4)
        finish(self.db, video_id, Table(rows=[Row("0919313090", "Truc", "@truc31798")]))
        self.assertEqual(search_results(self.db, "0919313090")[0]["bucket"], "Đã lưu")
        Path(self._tmp.name, f"{video_id}.mp4").unlink()
        self.assertFalse(list_videos(self.db)[0]["has_file"])
        self.assertFalse(retry(self.db, video_id))

    def test_reading_a_video_again_replaces_what_that_video_left_before(self) -> None:
        other = self._add("khac.mp4", "other")
        claim(self.db, 4)
        finish(self.db, other, Table(rows=[Row("0900000009", "Lan", "@lan_dung")]))
        video_id = self._add("a.mp4", "a")
        claim(self.db, 4)
        finish(
            self.db,
            video_id,
            Table(
                rows=[Row("0900000002", "Hoa", "@hoa_cu"), Row("0900000009", "Lan", "@lan_sai")],
                unopened=[Unopened("0900000001", "Truc")],
                review=[Review("", "", "CA", "@ff", "đã mở hồ sơ nhưng không thấy số cùng tên")],
            ),
        )
        self.assertTrue(retry(self.db, video_id))
        claim(self.db, 4)
        finish(
            self.db,
            video_id,
            Table(rows=[Row("0900000001", "Truc", "@truc31798"), Row("0900000002", "Hoa", "@hoa_moi")]),
        )
        found = {item["phone"] or item["username"]: item for item in search_results(self.db, limit=20)}
        self.assertNotIn("@ff", found)
        self.assertEqual(found["0900000001"]["username"], "@truc31798")
        self.assertEqual(found["0900000002"]["username"], "@hoa_moi")
        self.assertEqual(found["0900000009"]["username"], "@lan_dung")
        self.assertEqual(stats(self.db)["review"], 0)

    def test_orphan_profile_with_a_lookalike_handle_is_not_added(self) -> None:
        video_id = self._add("a.mp4", "a")
        claim(self.db, 4)
        finish(self.db, video_id, Table(rows=[Row("0917129188", "Phan Hưng Thanh", "@dy8b4fvimkx4")]))
        second = self._add("b.mp4", "b")
        claim(self.db, 4)
        finish(
            self.db,
            second,
            Table(review=[Review("", "", "Phan Hưng Thar", "@dy8b4fvlmkx4", "đã mở hồ sơ nhưng không thấy số cùng tên")]),
        )
        self.assertEqual(stats(self.db)["review"], 0)

    def test_disk_limit(self) -> None:
        gb = 1024 * 1024 * 1024
        self.assertTrue(can_accept(0, 100, 80 * gb, 10 * gb))
        self.assertFalse(can_accept(79 * gb, 2 * gb, 80 * gb, 10 * gb))
        self.assertFalse(can_accept(0, 100, 80 * gb, 1 * gb))
        self.assertEqual(queued_bytes(self.db), 0)
        self.assertEqual(disk_limit_bytes(1000, 80), 800)
        self.assertEqual(disk_limit_bytes(1000, 99), 900)
        self.assertEqual(disk_limit_bytes(1000, 1), 100)

    def _finish_kept(self, name: str, sha: str, created_at: str, payload: bytes, phone: str) -> tuple[int, Path]:
        video_id = self._add(name, sha, size=len(payload))
        path = Path(self._tmp.name) / f"{video_id}.mp4"
        path.write_bytes(payload)
        claim(self.db, 7)
        finish(self.db, video_id, Table(rows=[Row(phone, name, "@id")]))
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET created_at = ? WHERE id = ?", (created_at, video_id))
        return video_id, path

    def test_old_videos_are_deleted_when_the_disk_passes_the_limit(self) -> None:
        old_id, old_path = self._finish_kept("cu.mp4", "old", "2020-01-01T00:00:00+00:00", b"a" * 100, "0900000001")
        new_id, new_path = self._finish_kept("moi.mp4", "new", "2026-01-01T00:00:00+00:00", b"b" * 100, "0900000002")
        waiting = self._add("dang-doc.mp4", "run", size=100)
        claim(self.db, 9)

        def measure():
            return type("Usage", (), {"used": 900, "total": 1000, "free": 100})()

        removed = prune_old_videos(self.db, Path(self._tmp.name), percent=80, protect_newest=True, measure=measure)
        self.assertEqual(removed, 1)
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())
        self.assertTrue((Path(self._tmp.name) / f"{waiting}.mp4").exists())
        with connect(self.db) as conn:
            old = conn.execute("SELECT path, status FROM videos WHERE id = ?", (old_id,)).fetchone()
            newest = conn.execute("SELECT path FROM videos WHERE id = ?", (new_id,)).fetchone()
        self.assertEqual(old["path"], "")
        self.assertEqual(old["status"], "done")
        self.assertEqual(newest["path"], str(new_path))
        saved = {item["phone"]: item["name"] for item in search_results(self.db, limit=10)}
        self.assertEqual(saved["0900000001"], "cu.mp4")
        self.assertEqual(saved["0900000002"], "moi.mp4")

    def test_a_new_upload_may_replace_every_finished_video_when_that_makes_room(self) -> None:
        _old_id, old_path = self._finish_kept("cu.mp4", "old", "2020-01-01T00:00:00+00:00", b"a" * 100, "0900000001")
        _new_id, new_path = self._finish_kept("moi.mp4", "new", "2026-01-01T00:00:00+00:00", b"b" * 100, "0900000002")

        def measure():
            return type("Usage", (), {"used": 950, "total": 1000, "free": 50})()

        removed = prune_old_videos(self.db, Path(self._tmp.name), percent=80, protect_newest=False, measure=measure)
        self.assertEqual(removed, 2)
        self.assertFalse(old_path.exists())
        self.assertFalse(new_path.exists())

    def test_finished_videos_stay_when_deleting_them_still_leaves_the_disk_full(self) -> None:
        _old_id, old_path = self._finish_kept("cu.mp4", "old", "2020-01-01T00:00:00+00:00", b"a" * 40, "0900000001")

        def measure():
            return type("Usage", (), {"used": 950, "total": 1000, "free": 50})()

        removed = prune_old_videos(self.db, Path(self._tmp.name), percent=80, protect_newest=False, measure=measure)
        self.assertEqual(removed, 0)
        self.assertTrue(old_path.exists())

    def test_a_missing_file_is_not_counted_as_freed_disk(self) -> None:
        ghost_id, ghost_path = self._finish_kept("ma.mp4", "ghost", "2020-01-01T00:00:00+00:00", b"a" * 500, "0900000001")
        ghost_path.unlink()
        _real_id, real_path = self._finish_kept("that.mp4", "real", "2021-01-01T00:00:00+00:00", b"b" * 100, "0900000002")
        _newest_id, newest_path = self._finish_kept("moi.mp4", "new", "2026-01-01T00:00:00+00:00", b"c" * 100, "0900000003")

        def measure():
            return type("Usage", (), {"used": 900, "total": 1000, "free": 100})()

        removed = prune_old_videos(self.db, Path(self._tmp.name), percent=80, protect_newest=True, measure=measure)
        self.assertEqual(removed, 1)
        self.assertFalse(real_path.exists())
        self.assertTrue(newest_path.exists())
        with connect(self.db) as conn:
            ghost = conn.execute("SELECT path FROM videos WHERE id = ?", (ghost_id,)).fetchone()
        self.assertEqual(ghost["path"], "")
        self.assertEqual(search_results(self.db, "0900000001")[0]["name"], "ma.mp4")

    def test_done_videos_kept_on_disk_count_toward_stored_bytes(self) -> None:
        self._finish_kept("giu.mp4", "kept", "2026-01-01T00:00:00+00:00", b"a" * 80, "0900000001")
        self.assertEqual(queued_bytes(self.db), 80)


class PcClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "video.db"
        self.root = Path(self._tmp.name)
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, name: str, sha: str) -> int:
        created = begin_upload(self.db, name=name, size_bytes=5, sha256=sha)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, video_id, str(path))
        return video_id

    def test_server_restart_does_not_take_a_pc_video(self) -> None:
        self._add("a.mp4", "pc-a")
        job = claim_pc(self.db, "PC")
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(requeue_running(self.db, os.getpid()), 0)
        self.assertEqual(recover_dead(self.db), 0)
        self.assertIsNone(claim(self.db, 5))
        self.assertEqual(stats(self.db)["pc"], 1)
        self.assertEqual(stats(self.db)["queued"], 0)
        path = pc_video_path(self.db, int(job["id"]), str(job["lease"]), self.root)
        self.assertIsNotNone(path)
        self.assertIsNone(pc_video_path(self.db, int(job["id"]), "sai", self.root))

    def test_stale_pc_returns_to_the_queue(self) -> None:
        video_id = self._add("a.mp4", "pc-stale")
        job = claim_pc(self.db, "PC")
        assert job is not None
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET lease_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (video_id,),
            )
        self.assertEqual(release_stale_pc(self.db), 1)
        again = claim(self.db, 5)
        self.assertEqual(again["id"], video_id)

    def test_wrong_lease_does_not_write_results(self) -> None:
        video_id = self._add("a.mp4", "pc-lease")
        job = claim_pc(self.db, "PC")
        assert job is not None
        saved = finish(self.db, video_id, Table(rows=[Row("0900000001", "Mot", "@mot")]), lease="sai")
        self.assertFalse(saved)
        self.assertEqual(stats(self.db)["saved"], 0)
        self.assertEqual(stats(self.db)["pc"], 1)
        self.assertTrue(touch_pc(self.db, video_id, str(job["lease"])))
        self.assertFalse(touch_pc(self.db, video_id, "sai"))
        self.assertTrue(release_pc(self.db, video_id, str(job["lease"])))
        self.assertIsNotNone(claim(self.db, 8))

    def test_pc_result_is_stored_once(self) -> None:
        video_id = self._add("a.mp4", "pc-done")
        job = claim_pc(self.db, "PC")
        assert job is not None
        saved = finish(
            self.db,
            video_id,
            Table(rows=[Row("0900000001", "Mot", "@mot")], unopened=[Unopened("0900000002", "Hai")]),
            lease=str(job["lease"]),
        )
        self.assertTrue(saved)
        self.assertEqual(stats(self.db)["saved"], 1)
        self.assertEqual(stats(self.db)["unopened"], 1)
        self.assertEqual(stats(self.db)["done"], 1)
        self.assertFalse(finish(self.db, video_id, Table(rows=[Row("0900000003", "Ba", "@ba")]), lease=str(job["lease"])))


class BackupGzipTests(unittest.TestCase):
    def test_roundtrip_gzip_header(self) -> None:
        raw = gzip.compress("Số điện thoại\n".encode("utf-8-sig"))
        self.assertTrue(gzip.decompress(raw).startswith("Số điện thoại".encode("utf-8-sig")))


if __name__ == "__main__":
    unittest.main()
