"""Hàng đợi video, ghép kết quả và bản sao lưu tải về."""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from control_plane.results_out import saved_rows_after
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
    employee_machine,
    employee_name,
    employee_rows,
    fail,
    finish,
    init_db,
    iter_backup,
    coverage_counts,
    list_videos,
    missing_text,
    missing_videos,
    merge_close_results,
    queued_bytes,
    disk_limit_bytes,
    guard_disk,
    upload_room,
    drop_stale_uploads,
    prune_old_videos,
    pc_queue_ready,
    pc_video_path,
    defer_for_disk,
    queue_order,
    recover_dead,
    release_pc,
    release_stale_pc,
    report_progress,
    record_pc_result,
    requeue,
    requeue_running,
    ResultFilter,
    iter_results,
    result_page,
    result_summary,
    retry,
    search_results,
    start_full_check,
    stats,
    stop_full_check,
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

    def test_a_full_disk_sends_the_video_behind_the_others(self) -> None:
        first = self._add("a.mp4", "a")
        second = self._add("b.mp4", "b")
        job = claim(self.db, 7)
        assert job is not None
        self.assertEqual(job["id"], first)
        self.assertTrue(defer_for_disk(self.db, first, str(job["lease"])))
        self.assertFalse(defer_for_disk(self.db, first, str(job["lease"])))
        self.assertEqual(queue_order(self.db), [second, first])
        deferred = next(item for item in list_videos(self.db) if item["id"] == first)
        self.assertEqual(deferred["status"], "queued")
        self.assertEqual(deferred["priority"], -1)
        self.assertEqual(deferred["error"], "")

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
        self.assertEqual([item["phone"] for item in search_results(self.db, "84982")], ["0982117072"])
        self.assertEqual([item["phone"] for item in search_results(self.db, "0084982")], ["0982117072"])
        self.assertEqual([item["phone"] for item in search_results(self.db, "982117072")], ["0982117072"])
        self.assertEqual(search_results(self.db, "84"), [])
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

    def test_a_username_already_scanned_is_not_listed_again(self) -> None:
        first = self._add("a.mp4", "a")
        finish(
            self.db,
            first,
            Table(rows=[Row("0900000001", "Mot", "@mot"), Row("0900000002", "Hai", "@hai")]),
        )
        second = self._add("b.mp4", "b")
        finish(
            self.db,
            second,
            Table(
                rows=[Row("0900000003", "Mot lai", "@mot"), Row("0900000004", "Ba", "@ba")],
                unopened=[Unopened("0900000005", "Chua")],
            ),
        )
        with connect(self.db) as conn:
            stored = int(conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()["n"])
            conn.execute(
                """
                UPDATE results SET created_at = '2020-01-01T03:00:00+00:00'
                WHERE phone IN ('0900000001', '0900000002')
                """
            )
            conn.execute(
                """
                UPDATE results SET created_at = '2026-10-19T03:00:00+00:00'
                WHERE phone IN ('0900000003', '0900000004', '0900000005')
                """
            )
        self.assertEqual(stored, 5)
        self.assertEqual((stats(self.db)["saved"], stats(self.db)["unopened"]), (4, 1))
        page = result_page(self.db, limit=2)
        self.assertEqual([item["phone"] for item in page["items"]], ["0900000005", "0900000004"])
        self.assertEqual(page["total"], 4)
        older = result_page(self.db, limit=2, before=int(page["before"]))
        self.assertEqual([item["phone"] for item in older["items"]], ["0900000002", "0900000001"])
        self.assertEqual(older["total"], 4)
        tail = result_page(self.db, limit=2, before=int(older["before"]))
        self.assertEqual(tail["items"], [])
        self.assertEqual(tail["before"], 0)
        newest = int(page["items"][0]["id"])
        self.assertEqual(result_page(self.db, limit=10, after=newest)["items"], [])
        self.assertEqual([item["phone"] for item in search_results(self.db, "@mot")], ["0900000001"])
        self.assertEqual(search_results(self.db, "0900000003"), [])
        today = result_page(
            self.db,
            limit=10,
            filters=ResultFilter(day_from="2026-10-19", day_to="2026-10-19"),
        )
        self.assertEqual([item["phone"] for item in today["items"]], ["0900000005", "0900000004"])
        self.assertEqual(today["total"], 2)
        self.assertEqual(
            [item["phone"] for item in iter_results(self.db, ResultFilter())],
            ["0900000001", "0900000002", "0900000004", "0900000005"],
        )
        self.assertEqual(result_summary(self.db, ResultFilter())["total"], 4)
        self.assertEqual(
            [item["phone"] for item in saved_rows_after(self.db, 0, 10)],
            ["0900000001", "0900000002", "0900000004"],
        )

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

    def test_missing_list_counts_unopened_rows_and_skips_videos_still_waiting(self) -> None:
        video_id = self._add("a.mp4", "miss")
        finish(self.db, video_id, Table(unopened=[Unopened("0912345678", "An Le")]))
        waiting = self._add("b.mp4", "wait")
        items = missing_videos(self.db)
        self.assertEqual([item["video_id"] for item in items], [video_id])
        self.assertEqual(items[0]["unopened"], 1)
        self.assertNotIn("phone", items[0])
        self.assertEqual(missing_text(self.db, video_id), "An Le\t0912345678\n")
        self.assertEqual(missing_text(self.db, waiting), "")
        carriers = coverage_counts(self.db)["carriers"]
        self.assertIsInstance(carriers, list)
        self.assertEqual(carriers[0]["carrier"], "Vinaphone")
        self.assertEqual(carriers[0]["unopened"], 1)

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
        self.assertEqual(report["span"], "today")
        by_phone = {item["device"]: item for item in report["items"]}
        self.assertEqual(by_phone["Máy 1"]["uploaded"], 1)
        self.assertEqual(by_phone["Máy 1"]["errors"], 1)
        self.assertEqual(by_phone["Máy 1"]["waiting"], 0)
        self.assertEqual(by_phone["Máy 1"]["running"], 1)
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

    def test_phones_with_the_same_employee_name_are_one_row(self) -> None:
        self.assertEqual(employee_name("Ly14"), "Ly")
        self.assertEqual(employee_name("Ly 2"), "Ly")
        self.assertEqual(employee_name("nghĩa 2"), "nghĩa")
        self.assertEqual(employee_name("An"), "An")
        self.assertEqual(employee_name("2"), "2")
        self.assertEqual(employee_name(""), "")
        self.assertEqual(employee_machine("Ly14"), 14)
        self.assertIsNone(employee_machine("An"))
        rows = employee_rows(
            [
                {"device": "Ly14", "uploaded": 3, "saved": 10, "unnamed": 1, "review": 2, "unopened": 4},
                {"device": "ly2", "uploaded": 1, "saved": 5, "unnamed": 0, "review": 0, "unopened": 1},
                {"device": "nghĩa 1", "uploaded": 4, "saved": 8, "unnamed": 0, "review": 1, "unopened": 0},
                {"device": "", "uploaded": 9, "saved": 9, "unnamed": 0, "review": 0, "unopened": 0},
                {"device": "An", "uploaded": 0, "saved": 0, "unnamed": 0, "review": 0, "unopened": 0},
            ]
        )
        self.assertEqual(
            [(row["name"], row["uploaded"], row["results"], row["phones"]) for row in rows],
            [
                ("Ly", 4, 23, ["ly2", "Ly14"]),
                ("nghĩa", 4, 9, ["nghĩa 1"]),
            ],
        )
        now = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)
        today = "2026-10-19T02:00:00+00:00"
        first = self._add("ly-a.mp4", "emp-ly-a")
        second = self._add("ly-b.mp4", "emp-ly-b")
        third = self._add("nghia.mp4", "emp-nghia")
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET device = 'Ly14', status = 'done', created_at = ?, finished_at = ? WHERE id = ?",
                (today, today, first),
            )
            conn.execute(
                "UPDATE videos SET device = 'Ly2', status = 'done', created_at = ?, finished_at = ? WHERE id = ?",
                (today, today, second),
            )
            conn.execute(
                "UPDATE videos SET device = 'nghĩa 1', status = 'queued', created_at = ? WHERE id = ?",
                (today, third),
            )
            conn.execute(
                """
                INSERT INTO results (phone, name, username, bucket, video_id, created_at)
                VALUES ('0901000001', 'A', '@a', 1, ?, ?),
                       ('0901000002', 'B', '@b', 2, ?, ?)
                """,
                (first, today, second, today),
            )
        people = {row["name"]: row for row in device_day(self.db, now)["employees"]}
        self.assertEqual(people["Ly"]["uploaded"], 2)
        self.assertEqual(people["Ly"]["results"], 2)
        self.assertEqual(people["Ly"]["phones"], ["Ly2", "Ly14"])
        self.assertEqual(people["nghĩa"]["uploaded"], 1)
        self.assertEqual(people["nghĩa"]["results"], 0)

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

    def test_old_finished_phone_is_zero_today_and_counted_over_all_time(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc)
        finished = self._add("xong.mp4", "ly-old")
        waiting = self._add("cho.mp4", "ly-wait")
        broken = self._add("hong.mp4", "ly-err")
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', status = 'done',
                    created_at = '2026-10-15T12:00:00+00:00',
                    finished_at = '2026-10-16T02:00:00+00:00'
                WHERE id = ?
                """,
                (finished,),
            )
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', status = 'queued', created_at = '2026-10-15T12:10:00+00:00'
                WHERE id = ?
                """,
                (waiting,),
            )
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', status = 'error',
                    created_at = '2026-10-15T12:20:00+00:00',
                    finished_at = '2026-10-16T03:00:00+00:00'
                WHERE id = ?
                """,
                (broken,),
            )
        today = {item["device"]: item for item in device_day(self.db, now)["items"]}
        whole = device_day(self.db, now, span="all")
        self.assertEqual(whole["span"], "all")
        everything = {item["device"]: item for item in whole["items"]}
        self.assertEqual(
            (today["Ly13"]["uploaded"], today["Ly13"]["done"], today["Ly13"]["errors"], today["Ly13"]["waiting"]),
            (0, 0, 0, 0),
        )
        self.assertEqual(
            (everything["Ly13"]["uploaded"], everything["Ly13"]["done"], everything["Ly13"]["errors"], everything["Ly13"]["waiting"]),
            (3, 1, 1, 1),
        )
        self.assertEqual(device_day(self.db, now, span="khac")["span"], "today")

    def test_today_and_all_time_cover_rows_queue_and_carriers(self) -> None:
        now = datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc)
        old_done = self._add("cu.mp4", "cu")
        claim(self.db, 11)
        finish(self.db, old_done, Table(rows=[Row("0900000001", "Mot", "@mot")]))
        fresh = self._add("moi.mp4", "moi")
        claim(self.db, 11)
        finish(
            self.db,
            fresh,
            Table(
                rows=[Row("0900000002", "Hai", "@hai")],
                review=[Review("0900000003", "Ba", "Ba", "@ba", "cần xem")],
                unopened=[Unopened("0900000004", "Bon")],
            ),
        )
        waiting = self._add("cho-moi.mp4", "cho-moi")
        old_wait = self._add("cho-cu.mp4", "cho-cu")
        live = self._add("dang-doc.mp4", "dang")
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', created_at = '2026-10-15T12:00:00+00:00',
                    finished_at = '2026-10-16T02:00:00+00:00'
                WHERE id = ?
                """,
                (old_done,),
            )
            conn.execute(
                """
                UPDATE results
                SET created_at = '2026-10-16T02:00:00+00:00',
                    updated_at = '2026-10-16T02:00:00+00:00',
                    checked = 'ok'
                WHERE video_id = ?
                """,
                (old_done,),
            )
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', created_at = '2026-10-17T01:00:00+00:00',
                    finished_at = '2026-10-17T02:00:00+00:00'
                WHERE id = ?
                """,
                (fresh,),
            )
            conn.execute(
                """
                UPDATE results
                SET created_at = '2026-10-17T02:00:00+00:00',
                    updated_at = '2026-10-17T02:00:00+00:00'
                WHERE video_id = ?
                """,
                (fresh,),
            )
            conn.execute(
                "UPDATE results SET checked = 'diff' WHERE phone = '0900000002'",
            )
            conn.execute(
                "UPDATE videos SET device = 'Ly13', status = 'queued', created_at = '2026-10-17T03:00:00+00:00' WHERE id = ?",
                (waiting,),
            )
            conn.execute(
                "UPDATE videos SET device = 'Ly13', status = 'queued', created_at = '2026-10-15T12:30:00+00:00' WHERE id = ?",
                (old_wait,),
            )
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', status = 'running',
                    created_at = '2026-10-15T08:00:00+00:00',
                    started_at = '2026-10-15T09:00:00+00:00'
                WHERE id = ?
                """,
                (live,),
            )
        today = device_day(self.db, now)
        whole = device_day(self.db, now, span="all")
        phone_today = {item["device"]: item for item in today["items"]}["Ly13"]
        phone_all = {item["device"]: item for item in whole["items"]}["Ly13"]
        self.assertEqual(
            (phone_today["uploaded"], phone_today["done"], phone_today["waiting"], phone_today["running"]),
            (2, 1, 1, 0),
        )
        self.assertEqual(
            (phone_today["saved"], phone_today["review"], phone_today["unopened"], phone_today["matched"], phone_today["differed"]),
            (1, 1, 1, 0, 1),
        )
        self.assertEqual(today["totals"]["saved"], 1)
        self.assertEqual(today["totals"]["waiting"], 1)
        self.assertEqual(
            (phone_all["uploaded"], phone_all["done"], phone_all["waiting"], phone_all["running"], phone_all["saved"], phone_all["matched"]),
            (5, 2, 2, 1, 2, 1),
        )
        self.assertEqual(whole["totals"]["unopened"], 1)
        today_names = {item["name"] for item in list_videos(self.db, device="Ly13", span="today", now=now)}
        self.assertEqual(today_names, {"moi.mp4", "cho-moi.mp4", "dang-doc.mp4"})
        self.assertEqual(len(list_videos(self.db, device="Ly13", span="all", now=now)), 5)
        self.assertEqual(coverage_counts(self.db, span="today", now=now)["carriers"][0]["total"], 3)
        self.assertEqual(sum(item["total"] for item in coverage_counts(self.db, span="all", now=now)["carriers"]), 4)
        self.assertEqual(phone_today["unnamed"], 0)
        self.assertEqual(phone_all["unnamed"], 0)

    def test_rewritten_and_checked_rows_follow_their_own_days(self) -> None:
        now = datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc)
        video_id = self._add("sua.mp4", "sua")
        claim(self.db, 11)
        finish(self.db, video_id, Table(rows=[Row("0900000011", "Cu", "@cu")]))
        blank = self._add("trong.mp4", "trong")
        claim(self.db, 11)
        finish(self.db, blank, Table(rows=[Row("0900000012", "", "@trong")]))
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos
                SET device = 'Ly13', created_at = '2026-10-15T01:00:00+00:00',
                    finished_at = '2026-10-15T02:00:00+00:00'
                WHERE id IN (?, ?)
                """,
                (video_id, blank),
            )
            conn.execute(
                """
                UPDATE results
                SET created_at = '2026-10-15T02:00:00+00:00',
                    updated_at = '2026-10-15T02:00:00+00:00'
                """
            )
        yesterday = device_day(self.db, now)
        self.assertEqual(yesterday["totals"]["saved"], 0)
        self.assertEqual(yesterday["totals"]["unnamed"], 0)
        whole = device_day(self.db, now, span="all")
        self.assertEqual((whole["totals"]["saved"], whole["totals"]["unnamed"]), (1, 1))
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE results
                SET name = 'Moi', updated_at = '2026-10-17T02:00:00+00:00',
                    checked = '', checked_at = ''
                WHERE phone = '0900000011'
                """
            )
        rewritten = device_day(self.db, now)
        self.assertEqual(rewritten["totals"]["saved"], 1)
        self.assertEqual(rewritten["totals"]["unnamed"], 0)
        self.assertEqual(device_day(self.db, now, span="all")["totals"]["saved"], 1)
        before = result_page(self.db, limit=10)["revision"]
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE results
                SET checked = 'ok', checked_at = '2026-10-17T03:00:00+00:00'
                WHERE phone = '0900000011'
                """
            )
        after = result_page(self.db, limit=10)
        self.assertNotEqual(before, after["revision"])
        self.assertTrue(str(after["revision"]).startswith(f"{after['total']}|"))
        checked = device_day(self.db, now)
        self.assertEqual(checked["totals"]["saved"], 1)
        self.assertEqual(checked["totals"]["matched"], 1)
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE results
                SET updated_at = '2026-10-15T02:00:00+00:00'
                WHERE phone = '0900000011'
                """
            )
        moved = device_day(self.db, now)
        self.assertEqual(moved["totals"]["saved"], 0)
        self.assertEqual(moved["totals"]["matched"], 1)

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
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos
                SET check_pass = 1, lease = 'cu', lease_at = '2020-01-01T00:00:00+00:00',
                    helper = 'PC', progress_stage = 'read', progress_done = 3, progress_total = 9,
                    progress_at = '2020-01-01T00:00:00+00:00',
                    progress_same_at = '2020-01-01T00:00:00+00:00',
                    check_from = 'error', check_error = 'cu'
                WHERE id = ?
                """,
                (video_id,),
            )
        again = begin_upload(self.db, name="a.mp4", size_bytes=10, sha256="same")
        self.assertFalse(again["duplicate"])
        self.assertTrue(again["reopened"])
        self.assertEqual(again["id"], video_id)
        self.assertEqual(stats(self.db)["uploading"], 1)
        reopened = list_videos(self.db)[0]
        self.assertIsNone(reopened["phone_count"])
        self.assertIsNone(reopened["full_count"])
        self.assertEqual(reopened["check_pass"], 0)
        self.assertEqual(reopened["helper"], "")
        with connect(self.db) as conn:
            row = conn.execute(
                """
                SELECT lease, lease_at, progress_stage, progress_done, progress_same_at,
                       check_from, check_error
                FROM videos WHERE id = ?
                """,
                (video_id,),
            ).fetchone()
        self.assertEqual(row["lease"], "")
        self.assertIsNone(row["lease_at"])
        self.assertEqual(row["progress_stage"], "")
        self.assertEqual(row["progress_done"], 0)
        self.assertIsNone(row["progress_same_at"])
        self.assertEqual(row["check_from"], "")
        self.assertEqual(row["check_error"], "")
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
        self.assertIn("0332001753,khactam,@khactam60,Đã lưu,,clip.mp4,VPS", text)
        self.assertIn("Đọc bởi", text)

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
        total = 100 * gb
        self.assertTrue(upload_room(80 * gb, 20 * gb, total, 80))
        self.assertTrue(upload_room(90 * gb, 10 * gb, total, 80))
        self.assertFalse(upload_room(91 * gb, 9 * gb, total, 80))
        self.assertFalse(upload_room(50 * gb, 2 * gb, total, 80))
        self.assertTrue(upload_room(50 * gb, 2 * gb + 1, total, 80))

    def _finish_kept(self, name: str, sha: str, created_at: str, payload: bytes, phone: str) -> tuple[int, Path]:
        video_id = self._add(name, sha, size=len(payload))
        path = Path(self._tmp.name) / f"{video_id}.mp4"
        path.write_bytes(payload)
        claim(self.db, 7)
        finish(self.db, video_id, Table(rows=[Row(phone, name, "@" + sha)]))
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

    def test_a_tight_disk_deletes_oldest_finished_videos_down_to_the_free_floor(self) -> None:
        old_id, old_path = self._finish_kept("cu.mp4", "floor-old", "2020-01-01T00:00:00+00:00", b"a" * 40, "0900000001")
        new_id, new_path = self._finish_kept("moi.mp4", "floor-new", "2026-01-01T00:00:00+00:00", b"b" * 80, "0900000002")
        waiting = self._add("cho.mp4", "floor-wait")
        running = self._add("doc.mp4", "floor-run")
        claim(self.db, 11)

        def measure():
            return type("Usage", (), {"used": 950, "free": 10, "total": 1000})()

        # Xóa cả hai file đã xong vẫn để đĩa trên mức 80%, nên luật cũ giữ nguyên file.
        removed = prune_old_videos(self.db, Path(self._tmp.name), percent=80, protect_newest=False, measure=measure)
        self.assertEqual(removed, 0)
        freed = guard_disk(self.db, Path(self._tmp.name), percent=80, floor=40, measure=measure)
        self.assertGreaterEqual(freed, 40)
        self.assertLess(freed, 120)
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())
        self.assertTrue((Path(self._tmp.name) / f"{waiting}.mp4").exists())
        self.assertTrue((Path(self._tmp.name) / f"{running}.mp4").exists())
        with connect(self.db) as conn:
            old = conn.execute("SELECT path, status FROM videos WHERE id = ?", (old_id,)).fetchone()
            kept = conn.execute("SELECT path, status FROM videos WHERE id = ?", (new_id,)).fetchone()
            states = {int(row["id"]): row["status"] for row in conn.execute("SELECT id, status FROM videos")}
        self.assertEqual(old["path"], "")
        self.assertEqual(old["status"], "done")
        self.assertEqual(kept["path"], str(new_path))
        self.assertEqual(sorted((states[waiting], states[running])), ["queued", "running"])
        saved = {item["phone"]: item["name"] for item in search_results(self.db, limit=10)}
        self.assertEqual(saved["0900000001"], "cu.mp4")

    def test_quiet_partials_go_before_a_finished_video(self) -> None:
        _video_id, kept = self._finish_kept("giu.mp4", "floor-keep", "2020-01-01T00:00:00+00:00", b"a" * 40, "0900000001")
        folder = Path(self._tmp.name) / "partial"
        folder.mkdir()
        old = folder / ("ab" * 16 + ".part")
        old.write_bytes(b"p" * 80)
        moment = time.time() - (31 * 60)
        os.utime(old, (moment, moment))

        def measure():
            return type("Usage", (), {"used": 950, "free": 10, "total": 1000})()

        freed = guard_disk(self.db, Path(self._tmp.name), percent=80, floor=50, measure=measure)
        self.assertGreaterEqual(freed, 80)
        self.assertFalse(old.exists())
        self.assertTrue(kept.exists())

    def test_readonly_stats_read_a_checkpointed_database(self) -> None:
        self._add("a.mp4", "readonly")
        with connect(self.db) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertGreaterEqual(stats(self.db, readonly=True)["queued"], 1)

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

    def _add(self, name: str, sha: str, size: int = 5) -> int:
        created = begin_upload(self.db, name=name, size_bytes=size, sha256=sha)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, video_id, str(path))
        return video_id

    def test_fail_without_a_lease_leaves_a_pc_video_alone(self) -> None:
        video_id = self._add("a.mp4", "pc-fail")
        job = claim_pc(self.db, "PC")
        self.assertIsNotNone(job)
        self.assertFalse(fail(self.db, video_id, "nhầm"))
        self.assertEqual(stats(self.db)["pc"], 1)
        self.assertEqual(stats(self.db)["error"], 0)
        running = self._add("b.mp4", "vps-fail")
        claim(self.db, 6)
        self.assertTrue(fail(self.db, running, "hỏng"))
        states = {int(item["id"]): item["status"] for item in list_videos(self.db)}
        self.assertEqual(states[video_id], "pc")
        self.assertEqual(states[running], "error")

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

    def test_server_picks_up_a_pc_video_after_the_lease_expires(self) -> None:
        video_id = self._add("a.mp4", "pc-expire")
        job = claim_pc(self.db, "PC")
        assert job is not None
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET lease_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (video_id,),
            )
        taken = claim(self.db, 5)
        assert taken is not None
        self.assertEqual(taken["id"], video_id)
        self.assertEqual(stats(self.db)["pc"], 0)
        self.assertEqual(stats(self.db)["running"], 1)
        self.assertNotEqual(taken["lease"], job["lease"])

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
        self.assertEqual(list_videos(self.db)[0]["helper"], "PC")

    def test_two_pcs_claim_different_videos(self) -> None:
        first = self._add("a.mp4", "two-a")
        second = self._add("b.mp4", "two-b")
        one = claim_pc(self.db, "May-A")
        two = claim_pc(self.db, "May-B")
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)
        assert one is not None and two is not None
        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual({int(one["id"]), int(two["id"])}, {first, second})
        listed = {int(item["id"]): item for item in list_videos(self.db)}
        self.assertEqual(listed[int(one["id"])]["helper"], "May-A")
        self.assertEqual(listed[int(two["id"])]["helper"], "May-B")
        self.assertEqual(listed[int(one["id"])]["status"], "pc")
        self.assertEqual(stats(self.db)["pc"], 2)
        self.assertEqual(stats(self.db)["queued"], 0)

    def test_many_pcs_claim_different_videos_at_once(self) -> None:
        ids = [self._add(f"{index}.mp4", f"many-{index}") for index in range(6)]
        found: list[dict[str, object]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def take(name: str) -> None:
            try:
                job = claim_pc(self.db, name)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
                return
            if job is not None:
                with lock:
                    found.append(job)

        threads = [threading.Thread(target=take, args=(f"May-{index}",)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(found), 6)
        claimed = {int(job["id"]) for job in found}
        self.assertEqual(claimed, set(ids))
        self.assertEqual(len({str(job["lease"]) for job in found}), 6)
        self.assertEqual(stats(self.db)["pc"], 6)
        self.assertEqual(stats(self.db)["queued"], 0)
        self.assertIsNone(claim(self.db, 11))

    def test_finish_records_who_read_the_video(self) -> None:
        video_id = self._add("a.mp4", "who-pc")
        job = claim_pc(self.db, "May-A")
        assert job is not None
        saved = finish(
            self.db,
            video_id,
            Table(rows=[Row("0900000091", "Mot", "@mot91")]),
            lease=str(job["lease"]),
        )
        self.assertTrue(saved)
        item = list_videos(self.db)[0]
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["helper"], "May-A")
        page = result_page(self.db, "0900000091")
        self.assertEqual(page["items"][0]["helper"], "May-A")
        self.assertEqual(page["items"][0]["video_id"], video_id)
        self.assertTrue(retry(self.db, video_id))
        again = list_videos(self.db)[0]
        self.assertEqual(again["status"], "queued")
        self.assertEqual(again["helper"], "")

    def test_vps_finish_and_failure_are_labeled_vps(self) -> None:
        video_id = self._add("a.mp4", "who-vps")
        claim(self.db, 9)
        self.assertEqual(list_videos(self.db)[0]["helper"], "")
        self.assertTrue(finish(self.db, video_id, Table(rows=[Row("0900000092", "Hai", "@hai92")])))
        item = list_videos(self.db)[0]
        self.assertEqual(item["helper"], "VPS")
        page = result_page(self.db, "0900000092")
        self.assertEqual(page["items"][0]["helper"], "VPS")
        self.assertEqual(page["items"][0]["video_id"], video_id)
        other = self._add("b.mp4", "who-fail")
        claim(self.db, 10)
        fail(self.db, other, "hỏng")
        failed = {int(row["id"]): row for row in list_videos(self.db)}
        self.assertEqual(failed[other]["helper"], "VPS")
        self.assertEqual(failed[other]["status"], "error")

    def test_a_stale_server_read_cannot_clobber_the_next_one(self) -> None:
        video_id = self._add("a.mp4", "stale")
        first = claim(self.db, 11)
        assert first is not None
        self.assertTrue(requeue(self.db, video_id, str(first["lease"])))
        second = claim(self.db, 12)
        assert second is not None
        self.assertNotEqual(first["lease"], second["lease"])
        stale = str(first["lease"])
        self.assertFalse(finish(self.db, video_id, Table(rows=[Row("0900000001", "Cu", "@cu")]), lease=stale))
        self.assertFalse(fail(self.db, video_id, "lượt cũ", stale))
        self.assertFalse(requeue(self.db, video_id, stale))
        report_progress(self.db, video_id, "scan", 9, 9, stale)
        current = list_videos(self.db)[0]
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["progress_done"], 0)
        self.assertEqual(search_results(self.db, "0900000001"), [])
        self.assertTrue(
            finish(self.db, video_id, Table(rows=[Row("0900000002", "Moi", "@moi")]), lease=str(second["lease"]))
        )
        done = list_videos(self.db)[0]
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["helper"], "VPS")
        self.assertEqual(search_results(self.db, "0900000002")[0]["username"], "@moi")

    def _video(self, video_id: int) -> dict[str, object]:
        with connect(self.db) as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return dict(row)

    def _age_progress(self, video_id: int) -> None:
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos
                SET progress_same_at = '2020-01-01T00:00:00+00:00', lease_at = ?
                WHERE id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), video_id),
            )

    def test_a_video_waiting_on_the_pc_is_not_taken_back_for_being_downloaded(self) -> None:
        reading = self._add("doc.mp4", "pc-doc")
        waiting = self._add("cho.mp4", "pc-cho")
        current = claim_pc(self.db, "May-PC")
        nxt = claim_pc(self.db, "May-PC")
        assert current is not None and nxt is not None
        self.assertTrue(touch_pc(self.db, reading, str(current["lease"]), "read", 4, 20))
        self.assertTrue(touch_pc(self.db, waiting, str(nxt["lease"]), "hold", 1, 1))
        self._age_progress(waiting)
        self.assertEqual(release_stale_pc(self.db), 0)
        self.assertEqual(self._video(waiting)["status"], "pc")
        self.assertEqual(self._video(reading)["status"], "pc")
        self.assertTrue(release_pc(self.db, waiting, str(nxt["lease"]), "return"))
        self.assertEqual(self._video(waiting)["status"], "queued")
        self.assertEqual(self._video(reading)["status"], "pc")
        with connect(self.db) as conn:
            pc = conn.execute(
                "SELECT video_id, state, fail_streak, fail_count FROM pcs WHERE name = 'May-PC'"
            ).fetchone()
        self.assertEqual(int(pc["video_id"]), reading)
        self.assertEqual(pc["state"], "reading")
        self.assertEqual((int(pc["fail_streak"]), int(pc["fail_count"])), (0, 0))

    def test_a_pc_keeps_fifty_gigabytes_of_videos_that_are_waiting(self) -> None:
        with patch("control_plane.video_store.PC_CACHE_BYTES", 100):
            reading = self._add("doc.mp4", "cap-doc", 80)
            first = self._add("cho-a.mp4", "cap-a", 60)
            second = self._add("cho-b.mp4", "cap-b", 50)
            current = claim_pc(self.db, "May-Cap")
            assert current is not None
            self.assertEqual(int(current["id"]), reading)
            nxt = claim_pc(self.db, "May-Cap")
            assert nxt is not None
            self.assertEqual(int(nxt["id"]), first)
            self.assertIsNone(claim_pc(self.db, "May-Cap"))
            other = claim_pc(self.db, "May-Khac")
            assert other is not None
            self.assertEqual(int(other["id"]), second)
            self.assertTrue(finish(self.db, reading, Table(rows=[]), lease=str(current["lease"])))
            record_pc_result(self.db, reading)
            self.assertIsNone(claim_pc(self.db, "May-Cap"))
            release_pc(self.db, first, str(nxt["lease"]), "return")
            freed = claim_pc(self.db, "May-Cap")
            assert freed is not None
            self.assertEqual(int(freed["id"]), first)

    def test_queue_is_ready_only_when_this_pc_can_take_the_next_video(self) -> None:
        self.assertFalse(pc_queue_ready(self.db, "May-Nghi"))
        video_id = self._add("cho.mp4", "ready-one", 20)
        self.assertTrue(pc_queue_ready(self.db, "May-Nghi"))
        self.assertEqual(self._video(video_id)["status"], "queued")
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET pc_last_fail = ? WHERE id = ?", ("May-Nghi", video_id))
        self.assertFalse(pc_queue_ready(self.db, "May-Nghi"))
        self.assertTrue(pc_queue_ready(self.db, "May-Khac"))
        missing = self._add("mat.mp4", "ready-missing", 8)
        Path(str(self._video(missing)["path"])).unlink()
        self.assertTrue(pc_queue_ready(self.db, "May-Nghi"))

    def test_a_full_cache_is_not_ready_for_another_video(self) -> None:
        with patch("control_plane.video_store.PC_CACHE_BYTES", 100):
            reading = self._add("doc.mp4", "full-doc", 40)
            waiting = self._add("day.mp4", "full-wait", 80)
            current = claim_pc(self.db, "May-Day")
            assert current is not None
            self.assertEqual(int(current["id"]), reading)
            extra = claim_pc(self.db, "May-Day")
            assert extra is not None
            self.assertEqual(int(extra["id"]), waiting)
            blocked = self._add("nua.mp4", "full-block", 30)
            self.assertFalse(pc_queue_ready(self.db, "May-Day"))
            self.assertEqual(self._video(blocked)["status"], "queued")
            self.assertTrue(pc_queue_ready(self.db, "May-Rong"))

    def test_finished_pc_read_returns_even_while_heartbeats_continue(self) -> None:
        stuck = self._add("stuck.mp4", "pc-stuck")
        moving = self._add("moving.mp4", "pc-moving")
        held = claim_pc(self.db, "May-Stuck")
        live = claim_pc(self.db, "May-Live")
        assert held is not None and live is not None
        self.assertTrue(touch_pc(self.db, stuck, str(held["lease"]), "read", 1272, 1272))
        self.assertTrue(touch_pc(self.db, moving, str(live["lease"]), "download", 1, 5))
        frozen = self._video(stuck)["progress_same_at"]
        self.assertTrue(touch_pc(self.db, stuck, str(held["lease"]), "read", 1272, 1272))
        self.assertEqual(self._video(stuck)["progress_same_at"], frozen)
        self._age_progress(stuck)
        self._age_progress(moving)
        self.assertEqual(release_stale_pc(self.db), 1)
        self.assertEqual(self._video(stuck)["status"], "queued")
        self.assertEqual(self._video(moving)["status"], "pc")
        with connect(self.db) as conn:
            streak = conn.execute("SELECT fail_streak FROM pcs WHERE name = 'May-Stuck'").fetchone()
        self.assertEqual(int(streak["fail_streak"]), 0)
        taken = claim(self.db, 21)
        assert taken is not None
        self.assertEqual(taken["id"], stuck)
        self.assertEqual(self._video(moving)["status"], "pc")

    def test_finished_progress_is_armed_before_it_is_reclaimed(self) -> None:
        video_id = self._add("arm.mp4", "pc-arm")
        job = claim_pc(self.db, "May-Arm")
        assert job is not None
        self.assertTrue(touch_pc(self.db, video_id, str(job["lease"]), "read", 4, 4))
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET progress_same_at = NULL WHERE id = ?", (video_id,))
        self.assertEqual(release_stale_pc(self.db), 0)
        self.assertEqual(self._video(video_id)["status"], "pc")
        self.assertTrue(self._video(video_id)["progress_same_at"])
        self.assertTrue(touch_pc(self.db, video_id, str(job["lease"]), "read", 4, 4))
        self._age_progress(video_id)
        self.assertEqual(release_stale_pc(self.db), 1)
        self.assertEqual(self._video(video_id)["status"], "queued")

    def test_live_read_and_server_read_stay_put(self) -> None:
        reading = self._add("mid.mp4", "pc-mid")
        sending = self._add("send.mp4", "pc-send")
        server = self._add("vps.mp4", "vps-full")
        mid = claim_pc(self.db, "May-Mid")
        late = claim_pc(self.db, "May-Send")
        assert mid is not None and late is not None
        self.assertTrue(touch_pc(self.db, reading, str(mid["lease"]), "read", 10, 100))
        self.assertTrue(touch_pc(self.db, sending, str(late["lease"]), "send", 0, 0))
        earlier = self._video(reading)["progress_same_at"]
        self.assertTrue(touch_pc(self.db, reading, str(mid["lease"]), "read", 11, 100))
        self.assertNotEqual(self._video(reading)["progress_same_at"], earlier)
        taken = claim(self.db, 22)
        assert taken is not None
        self.assertEqual(taken["id"], server)
        report_progress(self.db, server, "read", 10, 10)
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET progress_same_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (server,),
            )
        self._age_progress(reading)
        self._age_progress(sending)
        self.assertEqual(release_stale_pc(self.db), 1)
        self.assertEqual(self._video(reading)["status"], "pc")
        self.assertEqual(self._video(reading)["progress_done"], 11)
        self.assertEqual(self._video(sending)["status"], "queued")
        self.assertEqual(self._video(server)["status"], "running")


class FullCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "video.db"
        self.root = Path(self._tmp.name)
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _done(self, name: str, sha: str, phone: str, person: str, handle: str) -> int:
        created = begin_upload(self.db, name=name, size_bytes=5, sha256=sha)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, video_id, str(path))
        claim(self.db, 4)
        self.assertTrue(finish(self.db, video_id, Table(rows=[Row(phone, person, handle)])))
        return video_id

    def test_start_queues_only_finished_videos_that_still_have_a_file(self) -> None:
        kept = self._done("a.mp4", "check-a", "0900000093", "Mot", "@mot93")
        missing = self._done("b.mp4", "check-b", "0900000094", "Hai", "@hai94")
        Path(self.root / f"{missing}.mp4").unlink()
        busy = self._done("c.mp4", "check-c", "0900000095", "Ba", "@ba95")
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET status = 'running' WHERE id = ?", (busy,))
        outcome = start_full_check(self.db)
        self.assertEqual(outcome["videos"], 1)
        self.assertEqual(outcome["rows"], 1)
        self.assertEqual(outcome["missing"], 1)
        self.assertEqual(outcome["busy"], 1)
        listed = {int(item["id"]): item for item in list_videos(self.db)}
        self.assertEqual(listed[kept]["status"], "queued")
        self.assertEqual(listed[kept]["check_pass"], 1)
        self.assertEqual(listed[busy]["status"], "running")
        self.assertEqual(stats(self.db)["checking"], 1)

    def test_a_new_video_is_read_before_the_check_queue(self) -> None:
        self._done("a.mp4", "check-order", "0900000093", "Mot", "@mot93")
        start_full_check(self.db)
        fresh = begin_upload(self.db, name="moi.mp4", size_bytes=5, sha256="check-new")
        path = self.root / f"{fresh['id']}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, int(fresh["id"]), str(path))
        job = claim(self.db, 8)
        assert job is not None
        self.assertEqual(job["id"], int(fresh["id"]))
        self.assertEqual(job["check"], 0)

    def test_second_read_marks_a_match_and_keeps_a_difference(self) -> None:
        same = self._done("a.mp4", "check-same", "0900000093", "Mot", "@mot93")
        other = self._done("b.mp4", "check-diff", "0900000094", "Hai", "@hai94")
        review_id = self._done("c.mp4", "check-review", "0900000095", "Ba", "@ba95")
        finish(
            self.db,
            review_id,
            Table(
                rows=[Row("0900000095", "Ba", "@ba95")],
                review=[Review("0900000096", "Bon", "", "@bon96", "cần xem")],
            ),
        )
        start_full_check(self.db)
        claimed = claim(self.db, 9)
        assert claimed is not None
        self.assertEqual(claimed["check"], 1)
        self.assertTrue(
            finish(
                self.db,
                int(claimed["id"]),
                Table(rows=[Row("0900000093", "Mot", "@mot93"), Row("0900000099", "Moi", "@moi99")]),
            )
        )
        # Hai video còn lại vẫn chờ kiểm. Đọc đúng video lệch và video có dòng cần xem.
        while True:
            job = claim(self.db, 9)
            if job is None:
                break
            video_id = int(job["id"])
            if video_id == other:
                table = Table(rows=[Row("0900000094", "Hai khac", "@hai94")])
            else:
                table = Table(rows=[Row("0900000098", "Khong thay", "@khong")])
            self.assertTrue(finish(self.db, video_id, table))
        page = {item["phone"]: item for item in result_page(self.db, limit=20)["items"]}
        self.assertEqual(page["0900000093"]["checked"], "ok")
        self.assertEqual(page["0900000093"]["name"], "Mot")
        self.assertNotIn("0900000099", page)
        self.assertEqual(page["0900000094"]["checked"], "diff")
        self.assertEqual(page["0900000094"]["name"], "Hai")
        self.assertEqual(page["0900000094"]["username"], "@hai94")
        self.assertIn("Lần đọc sau:", page["0900000094"]["reason"])
        self.assertEqual(page["0900000095"]["checked"], "")
        self.assertEqual(page["0900000095"]["username"], "@ba95")
        self.assertEqual(page["0900000096"]["bucket"], "Cần xem")
        self.assertEqual(stats(self.db)["matched"], 1)
        self.assertEqual(stats(self.db)["differed"], 1)
        self.assertEqual(list_videos(self.db)[0]["check_pass"], 0)
        stopped = stop_full_check(self.db)
        self.assertEqual(stopped["stopped"], 0)

    def test_stop_returns_unread_checks_and_leaves_the_one_being_read(self) -> None:
        first = self._done("a.mp4", "stop-a", "0900000093", "Mot", "@mot93")
        second = self._done("b.mp4", "stop-b", "0900000094", "Hai", "@hai94")
        start_full_check(self.db)
        job = claim(self.db, 4)
        assert job is not None
        outcome = stop_full_check(self.db)
        self.assertEqual(outcome["stopped"], 1)
        self.assertEqual(outcome["reading"], 1)
        listed = {int(item["id"]): item for item in list_videos(self.db)}
        running_id = int(job["id"])
        waiting_id = second if running_id == first else first
        self.assertEqual(listed[running_id]["status"], "running")
        self.assertEqual(listed[running_id]["check_pass"], 1)
        self.assertEqual(listed[waiting_id]["status"], "done")
        self.assertEqual(listed[waiting_id]["check_pass"], 0)
        self.assertEqual(listed[waiting_id]["helper"], "VPS")

    def test_stop_restores_an_error_video_that_was_queued_for_a_check(self) -> None:
        video_id = self._done("loi.mp4", "stop-err", "0900000097", "Bay", "@bay97")
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET status = 'error', error = 'đọc hỏng' WHERE id = ?",
                (video_id,),
            )
        self.assertEqual(start_full_check(self.db)["videos"], 1)
        stopped = stop_full_check(self.db)
        self.assertEqual(stopped["stopped"], 1)
        listed = {int(item["id"]): item for item in list_videos(self.db)}
        self.assertEqual(listed[video_id]["status"], "error")
        self.assertEqual(listed[video_id]["error"], "đọc hỏng")
        self.assertEqual(listed[video_id]["check_pass"], 0)
        with connect(self.db) as conn:
            row = conn.execute(
                "SELECT check_from, check_error FROM videos WHERE id = ?",
                (video_id,),
            ).fetchone()
        self.assertEqual(row["check_from"], "")
        self.assertEqual(row["check_error"], "")


class BackupGzipTests(unittest.TestCase):
    def test_roundtrip_gzip_header(self) -> None:
        raw = gzip.compress("Số điện thoại\n".encode("utf-8-sig"))
        self.assertTrue(gzip.decompress(raw).startswith("Số điện thoại".encode("utf-8-sig")))


if __name__ == "__main__":
    unittest.main()
