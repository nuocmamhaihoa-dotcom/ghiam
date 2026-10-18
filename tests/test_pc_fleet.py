"""Danh sách PC cùng đọc: tiến độ, thời gian, lỗi liền nhau, tạm dừng, giờ đọc, mã mời."""

from __future__ import annotations

import functools
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from control_plane.pc_fleet import (
    clean_schedule,
    create_invite,
    forget_pc,
    invite_codes,
    invite_open,
    list_invites,
    list_pcs,
    pc_checkin,
    revoke_invite,
    schedule_open,
    update_pc,
)
from control_plane.screen_table import Row, Table
from control_plane.video_store import (
    PC_FAIL_LIMIT,
    begin_upload,
    claim,
    claim_pc,
    commit_upload,
    connect,
    finish,
    init_db,
    list_videos,
    queue_order,
    record_pc_result,
    release_pc,
    release_stale_pc,
    report_progress,
    set_priority,
    touch_pc,
)
from control_plane.video_worker import run_job, scan_paths


def vn(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 16, hour, minute, tzinfo=timezone(timedelta(hours=7)))


class FleetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, sha: str) -> int:
        created = begin_upload(self.db, name=f"{sha}.mp4", size_bytes=5, sha256=sha)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, video_id, str(path))
        return video_id

    def _pc(self, name: str) -> dict:
        return next(item for item in list_pcs(self.db) if item["name"] == name)

    def test_progress_and_timings_reach_the_pc_list(self) -> None:
        video_id = self._add("progress")
        job = claim_pc(self.db, "May-A")
        assert job is not None
        lease = str(job["lease"])
        self.assertEqual(self._pc("May-A")["video"]["stage"], "download")
        self.assertTrue(touch_pc(self.db, video_id, lease, "scan", 40, 120))
        listed = {int(item["id"]): item for item in list_videos(self.db)}[video_id]
        self.assertEqual((listed["progress_stage"], listed["progress_done"], listed["progress_total"]), ("scan", 40, 120))
        self.assertTrue(touch_pc(self.db, video_id, lease, "bogus", 1, 2))
        self.assertEqual(self._pc("May-A")["video"]["stage"], "scan")
        self.assertTrue(finish(self.db, video_id, Table(rows=[Row("0900000001", "Mot", "@mot")]), lease=lease))
        record_pc_result(self.db, video_id, 61.04, 1200.0)
        pc = self._pc("May-A")
        self.assertIsNone(pc["video"])
        self.assertEqual(pc["ok_count"], 1)
        self.assertEqual(pc["done_today"], 1)
        self.assertEqual(pc["avg_download_s"], 61.0)
        self.assertEqual(pc["avg_read_s"], 1200.0)
        self.assertTrue(pc["online"])
        done = {int(item["id"]): item for item in list_videos(self.db)}[video_id]
        self.assertEqual(done["progress_stage"], "")

    def test_pc_failing_several_videos_in_a_row_is_excluded_until_enabled(self) -> None:
        for index in range(PC_FAIL_LIMIT):
            video_id = self._add(f"bad-{index}")
            job = claim_pc(self.db, "May-B")
            assert job is not None
            self.assertEqual(int(job["id"]), video_id)
            self.assertTrue(release_pc(self.db, video_id, str(job["lease"]), "fail", "RuntimeError: hỏng"))
        pc = self._pc("May-B")
        self.assertTrue(pc["excluded"])
        self.assertIn("hỏng", pc["last_error"])
        control = pc_checkin(self.db, "May-B")
        self.assertEqual(control["wait"], "excluded")
        self.assertTrue(update_pc(self.db, "May-B", enable=True))
        control = pc_checkin(self.db, "May-B")
        self.assertEqual(control["wait"], "")
        self.assertEqual(self._pc("May-B")["fail_streak"], 0)

    def test_one_pc_keeps_the_next_video_ready_and_shows_the_one_being_read(self) -> None:
        reading = self._add("reading")
        waiting = self._add("waiting")
        extra = self._add("extra")
        current = claim_pc(self.db, "May-Read")
        nxt = claim_pc(self.db, "May-Read")
        assert current is not None and nxt is not None
        self.assertEqual(int(current["id"]), reading)
        self.assertEqual(int(nxt["id"]), waiting)
        later = claim_pc(self.db, "May-Read")
        assert later is not None
        self.assertEqual(int(later["id"]), extra)
        self.assertTrue(touch_pc(self.db, reading, str(current["lease"]), "read", 3, 10))
        self.assertTrue(touch_pc(self.db, waiting, str(nxt["lease"]), "hold", 1, 1))
        pc = self._pc("May-Read")
        self.assertEqual(pc["video"]["id"], reading)
        self.assertEqual(pc["video"]["stage"], "read")
        self.assertEqual(pc["ahead"], 2)
        self.assertEqual(pc["ahead_bytes"], 10)
        with connect(self.db) as conn:
            shown = conn.execute("SELECT video_id FROM pcs WHERE name = 'May-Read'").fetchone()
        self.assertEqual(int(shown["video_id"]), reading)
        self.assertTrue(finish(self.db, reading, Table(rows=[]), lease=str(current["lease"])))
        record_pc_result(self.db, reading, 3.0, 8.0)
        pc = self._pc("May-Read")
        self.assertEqual(pc["video"]["id"], waiting)
        self.assertEqual(pc["video"]["stage"], "hold")
        self.assertEqual(pc["ahead"], 1)
        self.assertEqual(pc["ahead_bytes"], 5)

    def test_success_resets_the_failure_streak(self) -> None:
        first = self._add("streak-a")
        job = claim_pc(self.db, "May-C")
        assert job is not None
        release_pc(self.db, first, str(job["lease"]), "fail", "lỗi")
        self.assertEqual(self._pc("May-C")["fail_streak"], 1)
        self.assertIsNone(claim_pc(self.db, "May-C"))
        second = self._add("streak-b")
        job = claim_pc(self.db, "May-C")
        assert job is not None
        self.assertEqual(int(job["id"]), second)
        finish(self.db, int(job["id"]), Table(rows=[]), lease=str(job["lease"]))
        record_pc_result(self.db, int(job["id"]))
        self.assertEqual(self._pc("May-C")["fail_streak"], 0)

    def test_disk_release_is_not_a_failure(self) -> None:
        video_id = self._add("disk")
        job = claim_pc(self.db, "May-D")
        assert job is not None
        self.assertTrue(release_pc(self.db, video_id, str(job["lease"]), "disk"))
        pc = self._pc("May-D")
        self.assertEqual((pc["fail_streak"], pc["fail_count"], pc["state"]), (0, 0, "wait-disk"))
        again = claim_pc(self.db, "May-E")
        self.assertIsNotNone(again)

    def test_video_failing_on_two_pcs_is_left_for_the_server(self) -> None:
        video_id = self._add("poison")
        first = claim_pc(self.db, "May-F")
        assert first is not None
        release_pc(self.db, video_id, str(first["lease"]), "fail", "lỗi video")
        second = claim_pc(self.db, "May-G")
        assert second is not None
        release_pc(self.db, video_id, str(second["lease"]), "fail", "lỗi video")
        self.assertEqual(self._pc("May-F")["fail_streak"], 1)
        self.assertEqual(self._pc("May-G")["fail_streak"], 0)
        self.assertIsNone(claim_pc(self.db, "May-H"))
        self.assertEqual(claim(self.db, 7)["id"], video_id)

    def test_lost_heartbeat_is_noted_but_not_counted(self) -> None:
        video_id = self._add("stale")
        job = claim_pc(self.db, "May-I")
        assert job is not None
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET lease_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (video_id,))
        self.assertEqual(release_stale_pc(self.db), 1)
        pc = self._pc("May-I")
        self.assertIn("Mất nhịp", pc["last_error"])
        self.assertEqual(pc["fail_streak"], 0)
        self.assertIsNone(pc["video"])

    def test_paused_or_out_of_hours_pc_waits(self) -> None:
        pc_checkin(self.db, "May-J", state="idle")
        self.assertTrue(update_pc(self.db, "May-J", paused=True))
        self.assertEqual(pc_checkin(self.db, "May-J")["wait"], "paused")
        self.assertTrue(update_pc(self.db, "May-J", paused=False, schedule="22:00-06:00"))
        self.assertEqual(pc_checkin(self.db, "May-J", now=vn(12))["wait"], "schedule")
        self.assertEqual(pc_checkin(self.db, "May-J", now=vn(23, 30))["wait"], "")
        self.assertEqual(pc_checkin(self.db, "May-J", now=vn(5, 59))["wait"], "")
        self.assertTrue(update_pc(self.db, "May-J", autostart=True))
        self.assertTrue(pc_checkin(self.db, "May-J")["autostart"])
        self.assertFalse(update_pc(self.db, "Khong-co"))
        with self.assertRaises(ValueError):
            update_pc(self.db, "May-J", schedule="25:00-26:00")

    def test_schedule_text(self) -> None:
        self.assertEqual(clean_schedule(" 8:00 - 12:30 , 22:00-6:00 "), "08:00-12:30,22:00-06:00")
        self.assertEqual(clean_schedule(""), "")
        for bad in ("8-12", "12:00-12:00", "24:00-01:00", "abc"):
            with self.assertRaises(ValueError):
                clean_schedule(bad)
        self.assertTrue(schedule_open("", vn(3)))
        self.assertTrue(schedule_open("08:00-12:00", vn(8)))
        self.assertFalse(schedule_open("08:00-12:00", vn(12)))
        self.assertTrue(schedule_open("08:00-12:00,13:00-17:00", vn(14)))

    def test_invites_can_be_revoked(self) -> None:
        made = create_invite(self.db, "Văn phòng\n tầng 2")
        self.assertEqual(made["label"], "Văn phòng tầng 2")
        self.assertTrue(invite_open(self.db, str(made["code"])))
        pc_checkin(self.db, "May-K", invite=str(made["code"]))
        listed = list_invites(self.db)
        self.assertEqual(listed[0]["pcs"], 1)
        self.assertEqual(self._pc("May-K")["invite"], "Văn phòng tầng 2")
        self.assertTrue(revoke_invite(self.db, int(made["id"])))
        self.assertFalse(revoke_invite(self.db, int(made["id"])))
        self.assertFalse(invite_open(self.db, str(made["code"])))
        self.assertIn((str(made["code"]), True), invite_codes(self.db))

    def test_forget_only_offline_idle_pcs(self) -> None:
        video_id = self._add("forget")
        job = claim_pc(self.db, "May-L")
        assert job is not None
        self.assertFalse(forget_pc(self.db, "May-L"))
        finish(self.db, video_id, Table(rows=[]), lease=str(job["lease"]))
        self.assertFalse(forget_pc(self.db, "May-L"))
        later = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.assertTrue(forget_pc(self.db, "May-L", now=later))
        self.assertFalse(any(item["name"] == "May-L" for item in list_pcs(self.db)))


class QueueOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, sha: str, device: str) -> int:
        created = begin_upload(self.db, name=f"{sha}.mp4", size_bytes=5, sha256=sha, device=device)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(self.db, video_id, str(path))
        return video_id

    def test_phones_take_turns_and_urgent_goes_first(self) -> None:
        a1, a2, a3 = (self._add(f"a{index}", "May A") for index in range(3))
        b1 = self._add("b1", "May B")
        self.assertEqual(queue_order(self.db), [a1, b1, a2, a3])
        positions = {int(item["id"]): item["position"] for item in list_videos(self.db)}
        self.assertEqual(positions[b1], 2)
        self.assertTrue(set_priority(self.db, a3, True))
        self.assertEqual(queue_order(self.db), [a3, a1, b1, a2])
        job = claim(self.db, 1)
        self.assertEqual(job["id"], a3)
        self.assertFalse(set_priority(self.db, a3, False))
        self.assertTrue(finish(self.db, a3, Table(rows=[])))
        done = {int(item["id"]): item for item in list_videos(self.db)}[a3]
        self.assertEqual((done["priority"], done["position"]), (0, None))
        pc_job = claim_pc(self.db, "May-PC")
        assert pc_job is not None
        self.assertEqual(int(pc_job["id"]), a1)

    def test_new_videos_go_before_the_check_pass(self) -> None:
        old = self._add("old", "May A")
        self.assertTrue(finish(self.db, old, Table(rows=[Row("0900000001", "Mot", "@mot")])))
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET status = 'queued', check_pass = 1 WHERE id = ?", (old,))
        fresh = self._add("fresh", "May A")
        self.assertEqual(queue_order(self.db), [fresh, old])

    def test_server_progress_is_written_for_running_videos_only(self) -> None:
        video_id = self._add("progress-vps", "May A")
        report_progress(self.db, video_id, "scan", 4, 9)
        self.assertEqual(list_videos(self.db)[0]["progress_done"], 0)
        claim(self.db, 3)
        report_progress(self.db, video_id, "scan", 4, 9)
        report_progress(self.db, video_id, "nonsense", 8, 9)
        item = list_videos(self.db)[0]
        self.assertEqual((item["progress_stage"], item["progress_done"], item["progress_total"]), ("scan", 4, 9))

    def test_worker_writes_progress_while_reading(self) -> None:
        self._add("worker-progress", "May A")
        job = claim(self.db, 4)
        seen: list[tuple] = []

        def snapshot() -> None:
            item = list_videos(self.db)[0]
            seen.append((item["progress_stage"], item["progress_done"]))

        def fake_read(paths, submit=None, work_dir=None, progress=None):
            progress("scan", 1, 3)
            progress("scan", 2, 3)
            snapshot()
            progress("read", 3, 3)
            snapshot()
            return SimpleNamespace(rows=[], review=[], unopened=[]), []

        with patch("control_plane.video_worker.read_video", side_effect=fake_read):
            run_job(self.db, job, functools.partial(scan_paths, work_dir=self.root))
        self.assertEqual(seen, [("scan", 1), ("read", 3)])
        self.assertEqual(list_videos(self.db)[0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
