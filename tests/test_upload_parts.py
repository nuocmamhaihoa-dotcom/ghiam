"""Mảnh đã ghi phải còn nguyên khi gửi lại, và không được chừa lỗ giữa file."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from control_plane.upload_parts import (
    PARTIAL_TTL_SECONDS,
    UploadRejected,
    lookup_part,
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
