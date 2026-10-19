"""Tự sửa: từ trường hợp rỗng tới một lượt lẫn nhiều lỗi, không đụng việc đang chạy."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from control_plane.self_heal import heal_once
from control_plane.video_store import connect, init_db, queue_order, stats, utcnow


class SelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        self.videos = self.root / "videos"
        self.videos.mkdir()
        init_db(self.db)
        self._seq = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(
        self,
        status: str,
        *,
        error: str = "",
        pid: int | None = None,
        priority: int = 0,
        created: str | None = None,
        file: bool = True,
        device: str = "Ly1",
    ) -> tuple[int, Path]:
        self._seq += 1
        path = self.videos / f"{self._seq}.mp4"
        if file:
            path.write_bytes(b"video")
        with connect(self.db) as conn:
            cur = conn.execute(
                """
                INSERT INTO videos (
                    name, size_bytes, sha256, path, status, error, pid, created_at, device, priority
                ) VALUES (?, 10, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{self._seq}.mp4",
                    f"sha-{self._seq}",
                    str(path) if file or status == "queued" else "",
                    status,
                    error,
                    pid,
                    created or utcnow(),
                    device,
                    priority,
                ),
            )
            video_id = int(cur.lastrowid or 0)
        if status == "queued" and not file:
            # Đường dẫn trỏ tới file không có.
            missing = self.videos / f"missing-{self._seq}.mp4"
            with connect(self.db) as conn:
                conn.execute("UPDATE videos SET path = ? WHERE id = ?", (str(missing), video_id))
            return video_id, missing
        return video_id, path

    def _heal(self, now: datetime | None = None) -> list[str]:
        return heal_once(self.db, self.videos, self.root / "heartbeat", now=now)

    def _status(self, video_id: int) -> str:
        with connect(self.db) as conn:
            row = conn.execute("SELECT status FROM videos WHERE id = ?", (video_id,)).fetchone()
        self.assertIsNotNone(row)
        return str(row["status"])

    def test_an_empty_library_needs_nothing(self) -> None:
        self.assertEqual(self._heal(), [])
        self.assertEqual(self._heal(), [])

    def test_an_abandoned_partial_is_removed_and_a_live_piece_stays(self) -> None:
        partial = self.videos / "partial"
        partial.mkdir()
        old = partial / "aabbccddeeff0011.part"
        fresh = partial / "aabbccddeeff0022.part"
        old_done = partial / "aabbccddeeff0033.done"
        fresh_done = partial / "aabbccddeeff0044.done"
        old_lock = partial / "aabbccddeeff0055.lock"
        live_lock = partial / "aabbccddeeff0066.lock"
        old.write_bytes(b"stale-video")
        fresh.write_bytes(b"live")
        old_done.write_text("{}", encoding="utf-8")
        fresh_done.write_text("{}", encoding="utf-8")
        old_lock.mkdir()
        live_lock.mkdir()
        now = time.time()
        os.utime(old, (now - 7 * 3600, now - 7 * 3600))
        os.utime(old_done, (now - 3 * 3600, now - 3 * 3600))
        os.utime(old_lock, (now - 600, now - 600))
        video_id, path = self._add("running", pid=os.getpid())
        with connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO results (phone, name, username, bucket, video_id, created_at)
                VALUES ('0901000099', 'Giữ', '@giu', 1, ?, ?)
                """,
                (video_id, utcnow()),
            )
        notes = self._heal()
        self.assertFalse(old.exists())
        self.assertFalse(old_done.exists())
        self.assertTrue(fresh.is_file())
        self.assertEqual(fresh.read_bytes(), b"live")
        self.assertTrue(fresh_done.is_file())
        self.assertTrue(old_lock.is_dir())
        self.assertTrue(live_lock.is_dir())
        self.assertTrue(path.is_file())
        self.assertEqual(self._status(video_id), "running")
        with connect(self.db) as conn:
            kept = conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()
        self.assertEqual(int(kept["n"]), 1)
        self.assertTrue(any(note.startswith("Xóa 2 mảnh tải bỏ dở quá hạn") for note in notes))
        self.assertEqual(self._heal(), [])

    def test_a_fresh_upload_stays_and_an_abandoned_one_is_dropped(self) -> None:
        fresh, _path = self._add("uploading", file=False)
        stale, _path = self._add("uploading", file=False, created="2020-01-01T00:00:00+00:00")
        notes = self._heal()
        self.assertEqual(self._status(fresh), "uploading")
        self.assertNotIn(stale, self._ids())
        self.assertIn("Bỏ 1 lần tải dở quá hạn", notes[0])

    def test_a_live_read_is_not_touched_even_if_it_started_long_ago(self) -> None:
        video_id, _path = self._add("running", pid=os.getpid(), created="2020-01-01T00:00:00+00:00")
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos
                SET progress_stage = 'read', progress_done = 10, progress_total = 40, lease = 'live'
                WHERE id = ?
                """,
                (video_id,),
            )
        self.assertEqual(self._heal(), [])
        with connect(self.db) as conn:
            row = conn.execute(
                "SELECT status, progress_done, lease FROM videos WHERE id = ?",
                (video_id,),
            ).fetchone()
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["progress_done"], 10)
        self.assertEqual(row["lease"], "live")

    def test_a_dead_reader_returns_only_its_video(self) -> None:
        live, _path = self._add("running", pid=os.getpid())
        dead, _path = self._add("running", pid=999999)
        notes = self._heal()
        self.assertEqual(self._status(live), "running")
        self.assertEqual(self._status(dead), "queued")
        self.assertIn(f"Video {dead} đang đọc nhưng tiến trình đã tắt", notes[0])

    def test_a_bad_frame_waits_behind_the_queue_and_a_real_error_stays(self) -> None:
        # Video lỗi có id nhỏ hơn video đang chờ. Không có cờ xếp sau thì nó chen lên đầu.
        broken, _path = self._add("error", error="image file is truncated (43 bytes not processed)", device="Ly2")
        unknown, _path = self._add("error", error="cannot identify image file '/tmp/f_000001.jpg'", device="Ly2")
        waiting, _path = self._add("queued", device="Ly2")
        real, _path = self._add("error", error="Không mở được video")
        gone, _path = self._add("error", error="image file is truncated (1 bytes not processed)", file=False)
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"video")
        escaped, _path = self._add("error", error="image file is truncated (2 bytes not processed)", file=False)
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET path = ? WHERE id = ?", (str(outside), escaped))
            conn.execute(
                """
                UPDATE videos
                SET started_at = '2020-01-01T00:00:00+00:00', progress_same_at = '2020-01-01T00:00:00+00:00'
                WHERE id = ?
                """,
                (broken,),
            )
        notes = self._heal()
        order = queue_order(self.db)
        self.assertEqual(order[0], waiting)
        self.assertEqual(self._status(broken), "queued")
        self.assertEqual(self._status(unknown), "queued")
        self.assertEqual(self._status(real), "queued")
        self.assertEqual(self._status(gone), "error")
        self.assertEqual(self._status(escaped), "error")
        with connect(self.db) as conn:
            gone_error = conn.execute("SELECT error FROM videos WHERE id = ?", (gone,)).fetchone()
            escaped_error = conn.execute("SELECT error FROM videos WHERE id = ?", (escaped,)).fetchone()
        self.assertEqual(gone_error["error"], "File video không còn trên máy chủ.")
        self.assertIn("truncated", escaped_error["error"])
        self.assertLess(order.index(waiting), order.index(broken))
        self.assertLess(order.index(waiting), order.index(unknown))
        self.assertLess(order.index(waiting), order.index(real))
        with connect(self.db) as conn:
            priority = conn.execute(
                "SELECT priority, started_at, progress_same_at FROM videos WHERE id = ?",
                (broken,),
            ).fetchone()
        self.assertEqual(int(priority["priority"]), -1)
        self.assertIsNone(priority["started_at"])
        self.assertIsNone(priority["progress_same_at"])
        self.assertTrue(any(f"Video {broken} lỗi vì một khung ảnh hỏng" in line for line in notes))
        self.assertTrue(any(f"Video {real} không mở được lần trước" in line for line in notes))
        with connect(self.db) as conn:
            tried = conn.execute("SELECT heal_tries, priority FROM videos WHERE id = ?", (real,)).fetchone()
        self.assertEqual(int(tried["heal_tries"]), 1)
        self.assertEqual(int(tried["priority"]), -1)
        self.assertTrue(any(f"Video {gone} lỗi đã biết nhưng file không còn" in line for line in notes))
        self.assertFalse(any(str(escaped) in line for line in notes))
        self.assertEqual(self._heal(), [])
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET status = 'error', error = 'Không mở được video' WHERE id = ?",
                (real,),
            )
        again = self._heal()
        self.assertEqual(self._status(real), "error")
        self.assertFalse(any(str(real) in line for line in again))

    def test_a_disk_full_error_waits_behind_and_saved_rows_are_kept(self) -> None:
        waiting, _path = self._add("queued")
        full, _path = self._add("error", error="[Errno 28] No space left on device")
        kept, path = self._add("error", error="image file is truncated (9 bytes not processed)")
        with connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO results (phone, name, username, bucket, video_id, created_at)
                VALUES ('0900000001', 'An', '@an', 1, ?, ?)
                """,
                (kept, utcnow()),
            )
        notes = self._heal()
        self.assertEqual(self._status(full), "queued")
        self.assertLess(queue_order(self.db).index(waiting), queue_order(self.db).index(full))
        self.assertEqual(self._status(kept), "error")
        with connect(self.db) as conn:
            saved = conn.execute("SELECT username FROM results WHERE video_id = ?", (kept,)).fetchone()
        self.assertEqual(saved["username"], "@an")
        self.assertTrue(path.is_file())
        self.assertFalse(any(str(kept) in line for line in notes))
        self.assertEqual(self._heal(), [])
        self.assertEqual(stats(self.db)["saved"], 1)

    def test_a_queued_video_without_its_file_is_marked_and_a_real_file_stays(self) -> None:
        present, path = self._add("queued")
        missing, _path = self._add("queued", file=False)
        notes = self._heal()
        self.assertEqual(self._status(present), "queued")
        self.assertTrue(path.is_file())
        self.assertEqual(self._status(missing), "error")
        with connect(self.db) as conn:
            error = conn.execute("SELECT error FROM videos WHERE id = ?", (missing,)).fetchone()
        self.assertEqual(error["error"], "File video không còn trên máy chủ.")
        self.assertIn(f"Video {missing} đang chờ nhưng file không còn", notes[0])

    def test_an_offline_pc_marked_reading_rests_and_a_live_one_does_not(self) -> None:
        old = "2020-01-01T00:00:00+00:00"
        fresh = utcnow()
        with connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO pcs (name, first_seen, last_seen, state, last_error)
                VALUES ('MAY-CU', ?, ?, 'reading', 'Mất nhịp khi đọc video 1.')
                """,
                (old, old),
            )
            conn.execute(
                "INSERT INTO pcs (name, first_seen, last_seen, state) VALUES ('MAY-MOI', ?, ?, 'reading')",
                (fresh, fresh),
            )
            conn.execute(
                "INSERT INTO pcs (name, first_seen, last_seen, state) VALUES ('MAY-DIA', ?, ?, 'wait-disk')",
                (old, old),
            )
        video_id, _path = self._add("pc", device="MAY-BAM")
        stale_video, _path = self._add("pc", device="MAY-HEN")
        with connect(self.db) as conn:
            conn.execute(
                """
                UPDATE videos SET helper = 'MAY-BAM', status = 'pc', lease_at = ? WHERE id = ?
                """,
                (fresh, video_id),
            )
            conn.execute(
                """
                UPDATE videos SET helper = 'MAY-HEN', status = 'pc', lease_at = NULL WHERE id = ?
                """,
                (stale_video,),
            )
            conn.execute(
                "INSERT INTO pcs (name, first_seen, last_seen, state, video_id) VALUES ('MAY-BAM', ?, ?, 'reading', ?)",
                (old, old, video_id),
            )
            conn.execute(
                "INSERT INTO pcs (name, first_seen, last_seen, state, video_id) VALUES ('MAY-HEN', ?, ?, 'reading', ?)",
                (old, old, stale_video),
            )
        moment = datetime.now(timezone.utc)
        notes = heal_once(self.db, self.videos, now=moment, pc_offline_seconds=90)
        with connect(self.db) as conn:
            old_row = conn.execute("SELECT state, last_seen, last_error FROM pcs WHERE name = 'MAY-CU'").fetchone()
            live_row = conn.execute("SELECT state FROM pcs WHERE name = 'MAY-MOI'").fetchone()
            disk_row = conn.execute("SELECT state FROM pcs WHERE name = 'MAY-DIA'").fetchone()
            busy_row = conn.execute("SELECT state, last_seen FROM pcs WHERE name = 'MAY-BAM'").fetchone()
            stale_pc = conn.execute("SELECT state, last_seen FROM pcs WHERE name = 'MAY-HEN'").fetchone()
            video = conn.execute("SELECT status FROM videos WHERE id = ?", (video_id,)).fetchone()
            returned = conn.execute("SELECT status, priority FROM videos WHERE id = ?", (stale_video,)).fetchone()
        self.assertEqual(old_row["state"], "idle")
        self.assertEqual(old_row["last_seen"], old)
        self.assertIn("Mất nhịp", old_row["last_error"])
        self.assertEqual(live_row["state"], "reading")
        self.assertEqual(disk_row["state"], "wait-disk")
        self.assertEqual(video["status"], "pc")
        self.assertEqual(busy_row["state"], "reading")
        self.assertEqual(busy_row["last_seen"], old)
        self.assertEqual(returned["status"], "queued")
        self.assertEqual(stale_pc["state"], "idle")
        self.assertEqual(stale_pc["last_seen"], old)
        self.assertTrue(any("MAY-CU" in line for line in notes))
        self.assertTrue(any("MAY-HEN" in line for line in notes))
        self.assertFalse(any("MAY-MOI" in line for line in notes))
        self.assertFalse(any("MAY-BAM" in line for line in notes))
        self.assertFalse(any("MAY-DIA" in line for line in notes))

    def test_a_second_pass_changes_nothing(self) -> None:
        self._add("error", error="image file is truncated (4 bytes not processed)")
        self._add("queued", file=False)
        self._add("running", pid=999999)
        first = self._heal()
        self.assertGreaterEqual(len(first), 3)
        self.assertEqual(self._heal(), [])

    def test_one_pass_repairs_a_mixed_library_without_touching_the_live_work(self) -> None:
        live, _path = self._add("running", pid=os.getpid())
        fresh, _path = self._add("uploading", file=False)
        waiting, _path = self._add("queued", device="nghia")
        done, done_path = self._add("done")
        broken, _path = self._add("error", error="cannot identify image file 'f.jpg'")
        self._add("uploading", file=False, created="2020-01-01T00:00:00+00:00")
        orphan = self.videos / "leftover.small.mp4"
        orphan.write_bytes(b"small")
        with connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO results (phone, name, username, bucket, video_id, created_at)
                VALUES ('0900000002', 'Binh', '@binh', 1, ?, ?)
                """,
                (done, utcnow()),
            )
        notes = self._heal(datetime.now(timezone.utc) - timedelta(seconds=0))
        self.assertEqual(self._status(live), "running")
        self.assertEqual(self._status(fresh), "uploading")
        self.assertEqual(self._status(waiting), "queued")
        self.assertEqual(self._status(done), "done")
        self.assertTrue(done_path.is_file())
        self.assertEqual(self._status(broken), "queued")
        self.assertEqual(queue_order(self.db)[0], waiting)
        self.assertFalse(orphan.exists())
        self.assertEqual(stats(self.db)["saved"], 1)
        self.assertTrue(notes)
        self.assertNotIn("@binh", " ".join(notes))

    def test_each_known_failure_is_repaired_and_a_second_pass_is_quiet(self) -> None:
        live, _path = self._add("running", pid=os.getpid())
        waiting, _path = self._add("queued")
        locked, _path = self._add("error", error="database is locked")
        io_error, _path = self._add("error", error="disk I/O error")
        stalled, _path = self._add("error", error="Một khung không đọc xong sau 120 giây.")
        opened, _path = self._add("error", error="Không mở được video clip.mp4: Invalid data")
        unread, _path = self._add("error", error="Không đọc được video.")
        ffmpeg, _path = self._add("error", error="Cần cài ffmpeg để đọc video.")
        letters, _path = self._add("error", error="Chưa cài tesseract. Ubuntu: sudo apt install tesseract-ocr")
        kept, kept_path = self._add("error", error="database is locked")
        blank, blank_path = self._add("queued")
        blank_path.write_bytes(b"")
        hollow, hollow_path = self._add("error", error="image file is truncated (0 bytes not processed)")
        hollow_path.write_bytes(b"")
        finished, finished_path = self._add("uploading", created="2020-01-01T00:00:00+00:00")
        finished_path.write_bytes(b"0123456789")
        fresh_done, fresh_path = self._add("uploading")
        fresh_path.write_bytes(b"0123456789")
        denied, _path = self._add("error", error="Permission denied")
        with connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO results (phone, name, username, bucket, video_id, created_at)
                VALUES ('0900000003', 'Giu', '@giu', 1, ?, ?)
                """,
                (kept, utcnow()),
            )
            conn.execute(
                """
                INSERT INTO pcs (name, first_seen, last_seen, state, excluded)
                VALUES ('MAY-LOAI', ?, ?, 'idle', 1)
                """,
                (utcnow(), utcnow()),
            )
        now = time.time()
        stale_upload = self.videos / "up-stale.bin"
        fresh_upload = self.videos / "up-fresh.bin"
        young_shrink = self.videos / "up-shrink-1.mp4"
        old_shrink = self.videos / "up-shrink-2.mp4"
        for path, age, payload in (
            (stale_upload, 2000, b"old"),
            (fresh_upload, 10, b"new"),
            (young_shrink, 1000, b"encoding"),
            (old_shrink, 4000, b"abandoned"),
        ):
            path.write_bytes(payload)
            os.utime(path, (now - age, now - age))
        notes = self._heal()
        order = queue_order(self.db)
        self.assertEqual(self._status(live), "running")
        self.assertEqual(order[0], waiting)
        for video_id in (locked, io_error, stalled, opened, unread, denied):
            self.assertEqual(self._status(video_id), "queued")
            self.assertLess(order.index(waiting), order.index(video_id))
        self.assertEqual(self._status(ffmpeg), "error")
        self.assertEqual(self._status(letters), "error")
        self.assertEqual(self._status(kept), "error")
        self.assertEqual(kept_path.read_bytes(), b"video")
        self.assertEqual(self._status(blank), "error")
        self.assertEqual(self._status(hollow), "error")
        with connect(self.db) as conn:
            blank_error = conn.execute("SELECT error FROM videos WHERE id = ?", (blank,)).fetchone()
            hollow_error = conn.execute("SELECT error, priority FROM videos WHERE id = ?", (hollow,)).fetchone()
            opened_try = conn.execute("SELECT heal_tries, priority FROM videos WHERE id = ?", (opened,)).fetchone()
            locked_try = conn.execute("SELECT heal_tries, priority FROM videos WHERE id = ?", (locked,)).fetchone()
            finished_row = conn.execute("SELECT status, priority FROM videos WHERE id = ?", (finished,)).fetchone()
            fresh_row = conn.execute("SELECT status FROM videos WHERE id = ?", (fresh_done,)).fetchone()
            excluded = conn.execute("SELECT excluded, state FROM pcs WHERE name = 'MAY-LOAI'").fetchone()
            saved = conn.execute("SELECT username FROM results WHERE video_id = ?", (kept,)).fetchone()
        self.assertEqual(blank_error["error"], "File video rỗng.")
        self.assertEqual(hollow_error["error"], "File video rỗng.")
        self.assertEqual(int(hollow_error["priority"]), 0)
        self.assertEqual(int(opened_try["heal_tries"]), 1)
        self.assertEqual(int(opened_try["priority"]), -1)
        self.assertEqual(int(locked_try["heal_tries"]), 0)
        self.assertEqual(int(locked_try["priority"]), -1)
        self.assertEqual(finished_row["status"], "queued")
        self.assertEqual(int(finished_row["priority"]), 0)
        self.assertEqual(fresh_row["status"], "uploading")
        self.assertEqual(int(excluded["excluded"]), 1)
        self.assertEqual(excluded["state"], "idle")
        self.assertEqual(saved["username"], "@giu")
        self.assertFalse(stale_upload.exists())
        self.assertTrue(fresh_upload.is_file())
        self.assertTrue(young_shrink.is_file())
        self.assertFalse(old_shrink.exists())
        self.assertTrue(any("lỗi tạm thời" in line for line in notes))
        self.assertTrue(any("không mở được lần trước" in line for line in notes))
        self.assertFalse(any("ffmpeg" in line.lower() for line in notes))
        self.assertFalse(any("MAY-LOAI" in line for line in notes))
        self.assertNotIn("@giu", " ".join(notes))
        self.assertEqual(self._heal(), [])
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET status = 'error', error = 'disk I/O error' WHERE id = ?",
                (io_error,),
            )
        retried = self._heal()
        self.assertEqual(self._status(io_error), "queued")
        self.assertTrue(any(f"Video {io_error} lỗi tạm thời" in line for line in retried))
        self.assertEqual(self._heal(), [])

    def test_a_file_that_comes_back_is_read_once_and_a_saved_row_stays(self) -> None:
        waiting, _path = self._add("queued")
        back, path = self._add("error", error="File video không còn trên máy chủ.")
        claimed, _path = self._add("error", error="Không còn file video.")
        saved, _path = self._add("error", error="File video không còn trên máy chủ.")
        with connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO results (phone, name, username, bucket, video_id, created_at)
                VALUES ('0900000004', 'Con', '@con', 1, ?, ?)
                """,
                (saved, utcnow()),
            )
        notes = self._heal()
        order = queue_order(self.db)
        self.assertEqual(self._status(back), "queued")
        self.assertEqual(self._status(claimed), "queued")
        self.assertLess(order.index(waiting), order.index(back))
        self.assertLess(order.index(waiting), order.index(claimed))
        self.assertEqual(self._status(saved), "error")
        self.assertTrue(path.is_file())
        self.assertTrue(any(f"Video {back} được đánh mất nhưng file đã có lại" in line for line in notes))
        self.assertFalse(any(str(saved) in line for line in notes))
        self.assertEqual(self._heal(), [])
        with connect(self.db) as conn:
            conn.execute(
                "UPDATE videos SET status = 'error', error = 'File video không còn trên máy chủ.' WHERE id = ?",
                (back,),
            )
        self.assertEqual(self._heal(), [])
        self.assertEqual(self._status(back), "error")

    def _ids(self) -> set[int]:
        with connect(self.db) as conn:
            return {int(row["id"]) for row in conn.execute("SELECT id FROM videos")}
