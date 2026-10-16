"""Danh sách PC cùng đọc: tiến độ, thời gian, lỗi liền nhau, tạm dừng, giờ đọc, mã mời."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    record_pc_result,
    release_pc,
    release_stale_pc,
    touch_pc,
)


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


if __name__ == "__main__":
    unittest.main()
