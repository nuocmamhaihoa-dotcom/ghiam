"""Thu nhỏ video chờ đọc: chỉ đổi file khi video vẫn chờ, PC kiểm bằng mã băm mới."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from control_plane.video_shrink import SHRINK_TIMEOUT_SECONDS, shrink, shrink_command
from control_plane.video_store import (
    SMALL_SUFFIX,
    adopt_shrunk,
    begin_upload,
    claim_pc,
    commit_upload,
    connect,
    drop_orphan_small,
    get_meta,
    init_db,
    next_to_shrink,
    set_meta,
    shrink_failed,
    shrink_totals,
)
from control_plane.video_worker import SHRINK_CRF_KEY, SHRINK_KEY, _drop_old_parts, shrink_next, shrink_setting


class ShrinkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, sha: str, size: int = 1000) -> int:
        created = begin_upload(self.db, name=f"{sha}.mov", size_bytes=size, sha256=sha)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mov"
        path.write_bytes(b"x" * size)
        commit_upload(self.db, video_id, str(path))
        return video_id

    def _small(self, video_id: int, size: int = 100) -> Path:
        path = self.root / f"up-shrink-{video_id}.mp4"
        path.write_bytes(b"s" * size)
        return path

    def test_next_skips_the_videos_about_to_be_read(self) -> None:
        first, second, third = (self._add(f"next-{index}") for index in range(3))
        self.assertEqual(next_to_shrink(self.db)["id"], third)
        self.assertEqual(next_to_shrink(self.db, skip=0)["id"], first)
        shrink_failed(self.db, third)
        self.assertIsNone(next_to_shrink(self.db))
        self.assertIsNotNone(second)

    def test_adopt_swaps_the_file_and_pcs_check_the_new_hash(self) -> None:
        video_id = self._add("adopt")
        original = self.root / f"{video_id}.mov"
        small = self._small(video_id)
        digest = hashlib.sha256(small.read_bytes()).hexdigest()
        self.assertTrue(adopt_shrunk(self.db, video_id, str(original), small, digest))
        self.assertFalse(original.exists())
        self.assertFalse(small.exists())
        final = self.root / f"{video_id}{SMALL_SUFFIX}"
        self.assertTrue(final.is_file())
        job = claim_pc(self.db, "May")
        assert job is not None
        self.assertEqual((job["sha256"], job["bytes"]), (digest, 100))
        with connect(self.db) as conn:
            row = conn.execute("SELECT path, sha256, upload_bytes, shrunk FROM videos WHERE id = ?", (video_id,)).fetchone()
        self.assertEqual((row["path"], row["sha256"], row["upload_bytes"], row["shrunk"]), (str(final), "adopt", 1000, 1))
        self.assertEqual(shrink_totals(self.db), {"shrunk": 1, "saved_bytes": 900})

    def test_adopt_leaves_a_claimed_video_alone(self) -> None:
        video_id = self._add("claimed")
        original = self.root / f"{video_id}.mov"
        claim_pc(self.db, "May")
        small = self._small(video_id)
        self.assertFalse(adopt_shrunk(self.db, video_id, str(original), small, "x"))
        self.assertTrue(original.is_file())
        self.assertFalse(small.exists())

    def test_bigger_result_keeps_the_original(self) -> None:
        video_id = self._add("bigger", size=50)
        original = self.root / f"{video_id}.mov"
        self.assertFalse(adopt_shrunk(self.db, video_id, str(original), self._small(video_id, 80), "x"))
        self.assertTrue(original.is_file())
        self.assertIsNone(next_to_shrink(self.db, skip=0))

    def test_reupload_clears_the_shrunk_file_details(self) -> None:
        video_id = self._add("again")
        original = self.root / f"{video_id}.mov"
        adopt_shrunk(self.db, video_id, str(original), self._small(video_id), "small-hash")
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET status = 'done' WHERE id = ?", (video_id,))
        reopened = begin_upload(self.db, name="again.mov", size_bytes=1000, sha256="again")
        self.assertTrue(reopened["reopened"])
        with connect(self.db) as conn:
            row = conn.execute("SELECT file_sha256, shrunk, upload_bytes FROM videos WHERE id = ?", (video_id,)).fetchone()
        self.assertEqual((row["file_sha256"], row["shrunk"], row["upload_bytes"]), ("", 0, None))

    def test_orphan_small_files_are_removed(self) -> None:
        video_id = self._add("orphan")
        (self.root / f"999{SMALL_SUFFIX}").write_bytes(b"o")
        adopt_shrunk(self.db, video_id, str(self.root / f"{video_id}.mov"), self._small(video_id), "h")
        self.assertEqual(drop_orphan_small(self.db, self.root), 1)
        self.assertTrue((self.root / f"{video_id}{SMALL_SUFFIX}").is_file())

    def test_switch_is_off_by_default_and_worker_shrinks_when_on(self) -> None:
        self.assertEqual(shrink_setting(self.db), (False, 12))
        set_meta(self.db, SHRINK_KEY, "on")
        set_meta(self.db, SHRINK_CRF_KEY, "18")
        self.assertEqual(shrink_setting(self.db), (True, 18))
        self.assertEqual(get_meta(self.db, "missing", "d"), "d")
        ids = [self._add(f"worker-{index}") for index in range(3)]
        seen: list[int] = []

        def fake(src: Path, dst: Path, crf: int) -> bool:
            seen.append(crf)
            dst.write_bytes(b"tiny")
            return True

        self.assertTrue(shrink_next(self.db, 18, fake))
        self.assertEqual(seen, [18])
        self.assertTrue((self.root / f"{ids[2]}{SMALL_SUFFIX}").is_file())
        self.assertFalse(shrink_next(self.db, 18, fake))

        def broken(src: Path, dst: Path, crf: int) -> bool:
            return False

        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET shrunk = 0 WHERE id = ?", (ids[2],))
            conn.execute("UPDATE videos SET path = ? WHERE id = ?", (str(self.root / f"{ids[2]}{SMALL_SUFFIX}"), ids[2]))
        self.assertTrue(shrink_next(self.db, 18, broken))
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT shrunk FROM videos WHERE id = ?", (ids[2],)).fetchone()["shrunk"], 2)


@unittest.skipUnless(shutil.which("ffmpeg"), "cần ffmpeg")
class ShrinkVideoTests(unittest.TestCase):
    def test_keeps_two_frames_per_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.mp4"
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=3", "-pix_fmt", "yuv420p", str(source)],
                check=True,
            )
            target = Path(tmp) / "out.mp4"
            self.assertTrue(shrink(source, target, crf=12, threads=1, nice=0))
            frames = subprocess.run(
                ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(target)],
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(int(frames), 6)
            self.assertFalse(shrink(Path(tmp) / "missing.mp4", Path(tmp) / "bad.mp4", nice=0))
            self.assertFalse((Path(tmp) / "bad.mp4").exists())
        self.assertIn("fps=2", shrink_command(Path("a"), Path("b")))


class ShrinkPartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_slow_shrink_file_outlives_the_upload_part_limit(self) -> None:
        self.assertEqual(SHRINK_TIMEOUT_SECONDS, 3600)
        folder = self.root
        shrink_part = folder / "up-shrink-9.mp4"
        upload_part = folder / "up-token.mov"
        shrink_part.write_bytes(b"still-encoding")
        upload_part.write_bytes(b"abandoned")
        old = time.time() - 1800 - 5
        os.utime(shrink_part, (old, old))
        os.utime(upload_part, (old, old))
        _drop_old_parts(folder)
        self.assertEqual(shrink_part.read_bytes(), b"still-encoding")
        self.assertFalse(upload_part.exists())

    def test_an_abandoned_shrink_file_is_removed_after_the_encode_timeout(self) -> None:
        shrink_part = self.root / "up-shrink-9.mp4"
        shrink_part.write_bytes(b"left-behind")
        very_old = time.time() - SHRINK_TIMEOUT_SECONDS - 300 - 5
        os.utime(shrink_part, (very_old, very_old))
        _drop_old_parts(self.root)
        self.assertFalse(shrink_part.exists())

    def test_sweep_during_shrink_does_not_mark_the_video_failed(self) -> None:
        db = self.root / "video.db"
        init_db(db)
        for index in range(2):
            other = begin_upload(db, name=f"ahead-{index}.mov", size_bytes=1000, sha256=f"ahead-{index}")
            path = self.root / f"{int(other['id'])}.mov"
            path.write_bytes(b"x" * 1000)
            commit_upload(db, int(other["id"]), str(path))
        created = begin_upload(db, name="slow.mov", size_bytes=1000, sha256="slow-1")
        video_id = int(created["id"])
        source = self.root / f"{video_id}.mov"
        source.write_bytes(b"x" * 1000)
        commit_upload(db, video_id, str(source))
        set_meta(db, SHRINK_KEY, "on")

        def fake(src: Path, dst: Path, crf: int) -> bool:
            dst.write_bytes(b"tiny")
            old = time.time() - 1900
            os.utime(dst, (old, old))
            _drop_old_parts(src.parent)
            if not dst.is_file():
                return False
            return True

        self.assertTrue(shrink_next(db, 18, fake))
        with connect(db) as conn:
            row = conn.execute("SELECT shrunk FROM videos WHERE id = ?", (video_id,)).fetchone()
        self.assertEqual(row["shrunk"], 1)


if __name__ == "__main__":
    unittest.main()
