"""Mảnh đã ghi phải còn nguyên khi gửi lại, và không được chừa lỗ giữa file."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from control_plane.upload_parts import (
    LOCK_STALE_SECONDS,
    PARTIAL_TTL_SECONDS,
    TIGHT_PARTIAL_SECONDS,
    UploadRejected,
    _hold,
    lookup_part,
    reclaim_stale_partials,
    sweep_parts,
    write_part,
)


class UploadPartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_overlap_rewrites_only_the_new_tail(self) -> None:
        upload_id = "d4" * 16
        first = write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=10,
            device="Máy",
            offset=0,
            data=b"abcdef",
            max_bytes=100,
        )
        self.assertEqual(first["received"], 6)
        second = write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=10,
            device="Máy",
            offset=2,
            data=b"cdefghij",
            max_bytes=100,
        )
        self.assertTrue(second["complete"])
        self.assertEqual(Path(str(second["path"])).read_bytes(), b"abcdefghij")

    def test_a_repeated_prefix_does_not_fail_or_grow(self) -> None:
        upload_id = "e5" * 16
        write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=10,
            device="",
            offset=0,
            data=b"abcdef",
            max_bytes=100,
        )
        again = write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=10,
            device="",
            offset=0,
            data=b"abcdef",
            max_bytes=100,
        )
        self.assertEqual(again["received"], 6)
        self.assertFalse(again["complete"])
        self.assertEqual((self.root / "partial" / f"{upload_id}.part").read_bytes(), b"abcdef")

    def test_a_hole_is_refused_with_the_byte_already_stored(self) -> None:
        upload_id = "f6" * 16
        write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=10,
            device="",
            offset=0,
            data=b"abc",
            max_bytes=100,
        )
        with self.assertRaises(UploadRejected) as caught:
            write_part(
                self.root,
                upload_id=upload_id,
                name="clip.mp4",
                size=10,
                device="",
                offset=5,
                data=b"xyzab",
                max_bytes=100,
            )
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.received, 3)
        seen = lookup_part(self.root, upload_id)
        assert seen is not None
        self.assertEqual(seen["received"], 3)

    def test_old_partials_are_removed(self) -> None:
        upload_id = "a7" * 16
        write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=4,
            device="",
            offset=0,
            data=b"abcd",
            max_bytes=100,
        )
        part = self.root / "partial" / f"{upload_id}.part"
        old = time.time() - PARTIAL_TTL_SECONDS - 10
        part.touch()
        os.utime(part, (old, old))
        meta = self.root / "partial" / f"{upload_id}.json"
        os.utime(meta, (old, old))
        sweep_parts(self.root)
        self.assertFalse(part.exists())
        self.assertFalse(meta.exists())

    def _partial(self, upload_id: str, payload: bytes, age: float) -> Path:
        write_part(
            self.root,
            upload_id=upload_id,
            name="clip.mp4",
            size=len(payload),
            device="",
            offset=0,
            data=payload,
            max_bytes=100,
        )
        moment = time.time() - age
        folder = self.root / "partial"
        for path in folder.iterdir():
            if path.name.startswith(upload_id):
                os.utime(path, (moment, moment))
        return folder / f"{upload_id}.part"

    def test_a_tight_disk_drops_quiet_partials_and_keeps_a_live_one(self) -> None:
        old = self._partial("ab" * 16, b"old-bytes", TIGHT_PARTIAL_SECONDS + 5)
        live = self._partial("cd" * 16, b"live", 5)
        freed = reclaim_stale_partials(self.root)
        self.assertGreaterEqual(freed, len(b"old-bytes"))
        self.assertFalse(old.exists())
        self.assertEqual(live.read_bytes(), b"live")

    def test_prepare_upload_room_clears_old_partials_before_accepting(self) -> None:
        from control_plane.video_api import prepare_upload_room

        gb = 1024 * 1024 * 1024
        old = self._partial("ef" * 16, b"stale", TIGHT_PARTIAL_SECONDS + 5)
        seen = {"n": 0}

        def measure() -> object:
            seen["n"] += 1
            if seen["n"] == 1:
                return type("Usage", (), {"used": 90 * gb, "free": 10 * gb, "total": 100 * gb})()
            return type("Usage", (), {"used": 70 * gb, "free": 30 * gb, "total": 100 * gb})()

        detail = prepare_upload_room(1024, root=self.root, measure=measure)
        self.assertIsNone(detail)
        self.assertFalse(old.exists())

    def test_prepare_upload_room_refuses_in_vietnamese_when_space_stays_gone(self) -> None:
        from control_plane.video_api import prepare_upload_room

        gb = 1024 * 1024 * 1024
        self._partial("11" * 16, b"stale", TIGHT_PARTIAL_SECONDS + 5)

        def measure() -> object:
            return type("Usage", (), {"used": 95 * gb, "free": 5 * gb, "total": 100 * gb})()

        detail = prepare_upload_room(1024, root=self.root, measure=measure)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIn("Ổ đĩa", detail)
        self.assertNotIn("parsing the body", detail)

    def test_a_held_lock_keeps_its_part_while_a_poll_sweeps(self) -> None:
        upload_id = "b8" * 16
        part = self._partial(upload_id, b"abc", LOCK_STALE_SECONDS + 30)
        meta = part.with_name(f"{upload_id}.json")
        lock = part.with_name(f"{upload_id}.lock")
        with _hold(lock, interval=0.05, keep=(part, meta)):
            time.sleep(0.2)
            sweep_parts(self.root)
            reclaim_stale_partials(self.root)
            self.assertTrue(lock.is_dir())
            self.assertEqual(part.read_bytes(), b"abc")
        self.assertFalse(lock.exists())

    def test_a_lock_left_by_a_dead_writer_is_removed(self) -> None:
        folder = self.root / "partial"
        folder.mkdir()
        lock = folder / f"{'c9' * 16}.lock"
        lock.mkdir()
        old = time.time() - LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        sweep_parts(self.root)
        self.assertFalse(lock.exists())
