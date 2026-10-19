"""Tự sửa: từ trường hợp rỗng tới một lượt lẫn nhiều lỗi, không đụng việc đang chạy."""

from __future__ import annotations

import os
import tempfile
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
        self.assertEqual(self._status(real), "error")
        self.assertEqual(self._status(gone), "error")
        self.assertEqual(self._status(escaped), "error")
        self.assertLess(order.index(waiting), order.index(broken))
        self.assertLess(order.index(waiting), order.index(unknown))
        with connect(self.db) as conn:
            priority = conn.execute(
                "SELECT priority, started_at, progress_same_at FROM videos WHERE id = ?",
                (broken,),
            ).fetchone()
        self.assertEqual(int(priority["priority"]), -1)
        self.assertIsNone(priority["started_at"])
        self.assertIsNone(priority["progress_same_at"])
        self.assertTrue(any(f"Video {broken} lỗi vì một khung ảnh hỏng" in line for line in notes))
        self.assertFalse(any(str(real) in line for line in notes))
        self.assertFalse(any(str(escaped) in line for line in notes))

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

    def _ids(self) -> set[int]:
        with connect(self.db) as conn:
            return {int(row["id"]) for row in conn.execute("SELECT id FROM videos")}
