"""Bảng đọc mới nhận lại file cũ và không đụng kết quả đang chạy."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from control_plane.fresh_lane import fresh_report, import_kept_videos, is_test_upload
from control_plane.screen_table import Row, Table
from control_plane.video_store import begin_upload, commit_upload, finish, init_db, search_results, stats


class FreshLaneTests(unittest.TestCase):
    def test_test_marker_is_its_own_segment(self) -> None:
        self.assertTrue(is_test_upload("iphone|clip.mp4|10|0|test"))
        self.assertTrue(is_test_upload("iphone|clip.mp4|10|0|test|lane2"))
        self.assertFalse(is_test_upload("iphone|latest.mp4|10|0"))
    def test_repeat_upload_is_a_new_video_and_the_live_table_stays(self) -> None:
        with TemporaryDirectory() as tmp:
            live = Path(tmp) / "video.db"
            init_db(live)
            first = begin_upload(live, name="a.mp4", size_bytes=4, sha256="abc")
            self.assertFalse(first["duplicate"])
            again = begin_upload(live, name="a.mp4", size_bytes=4, sha256="abc")
            self.assertTrue(again["duplicate"])
            fresh = begin_upload(live, name="a.mp4", size_bytes=4, sha256="abc", allow_repeat=True)
            self.assertFalse(fresh["duplicate"])
            self.assertNotEqual(fresh["id"], first["id"])

    def test_import_reads_a_kept_file_as_a_new_job(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "video.db"
            init_db(live)
            created = begin_upload(live, name="RPReplay.mp4", size_bytes=4, sha256="dead", device="iPhone")
            video_id = int(created["id"])
            src = root / "old.mp4"
            src.write_bytes(b"videobytes")
            commit_upload(live, video_id, str(src))
            finish(live, video_id, Table(rows=[Row("0982117072", "An", "@an.1")]))
            moved = import_kept_videos(live, root)
            self.assertEqual(moved["imported"], 1)
            again = import_kept_videos(live, root)
            self.assertEqual(again["imported"], 0)
            self.assertEqual(again["skipped"], 1)
            self.assertEqual(stats(live)["saved"], 1)
            fresh_rows = search_results(root / "video-fresh.db", view="all", limit=10)
            self.assertEqual(fresh_rows, [])
            report = fresh_report(root / "video-fresh.db")
            self.assertEqual(report["queued"], 1)
            self.assertTrue(any("Đang đọc từ đầu" in line for line in report["notes"]))


if __name__ == "__main__":
    unittest.main()
