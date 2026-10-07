"""Mục lục MP4 trong phần đã nhận, và lần gửi dở được đọc khi moov đã đủ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_plane import app as app_module
from control_plane import db
from control_plane import video_repair
from control_plane.media_boxes import moov_ready
from control_plane.settings import settings
from control_plane.video_jobs import jobs


def _box(kind: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + kind + payload


class MoovReadyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _file(self, payload: bytes) -> str:
        target = Path(self._dir.name) / "clip.mp4"
        target.write_bytes(payload)
        return str(target)

    def test_a_complete_moov_inside_the_prefix_is_ready(self) -> None:
        path = Path(self._file(_box(b"ftyp", b"isom") + _box(b"moov", b"\x00" * 16) + _box(b"mdat", b"\x00" * 32)))
        self.assertTrue(moov_ready(path, path.stat().st_size))

    def test_a_moov_that_runs_past_the_prefix_is_not_ready(self) -> None:
        payload = _box(b"ftyp", b"isom") + _box(b"moov", b"\x00" * 40)
        path = Path(self._file(payload))
        self.assertFalse(moov_ready(path, 16))

    def test_a_moov_after_an_unfinished_mdat_is_not_ready(self) -> None:
        body = _box(b"ftyp", b"isom") + _box(b"mdat", b"\x00" * 80) + _box(b"moov", b"\x00" * 12)
        path = Path(self._file(body))
        self.assertFalse(moov_ready(path, 40))
        self.assertTrue(moov_ready(path, len(body)))

    def test_a_box_that_runs_to_the_end_of_a_prefix_is_not_ready(self) -> None:
        path = Path(self._file(b"\x00\x00\x00\x00moov" + b"\x00" * 20))
        self.assertFalse(moov_ready(path, 28))

    def test_a_wide_moov_that_fits_is_ready(self) -> None:
        payload = b"\x00" * 8
        wide = (16 + len(payload)).to_bytes(8, "big")
        body = b"\x00\x00\x00\x01moov" + wide + payload
        path = Path(self._file(body))
        self.assertTrue(moov_ready(path, len(body)))
        self.assertFalse(moov_ready(path, 20))

    def test_a_missing_file_is_not_ready(self) -> None:
        self.assertFalse(moov_ready(Path(self._dir.name) / "missing.mp4", 100))


class PartialReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = settings.db_path
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._dir.name) / "partial.db"
        settings.db_path = self.db_path
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        app_module._PARTIAL_IDS.clear()
        for job_id in list(jobs._jobs):
            video_repair.drop(job_id)
        jobs._jobs.clear()
        settings.db_path = self._previous
        self._dir.cleanup()

    def test_a_playable_prefix_starts_a_read_without_faststart(self) -> None:
        prefix = _box(b"ftyp", b"isom") + _box(b"moov", b"\x00" * 24)
        prefix += _box(b"mdat", b"\x00" * (app_module._PARTIAL_FLOOR + 64))
        path = Path(self._dir.name) / "ready.mp4"
        path.write_bytes(prefix)
        item = {
            "path": path,
            "name": "san.mp4",
            "source": "may",
            "size": len(prefix) + 1000,
            "ranges": [(0, len(prefix))],
            "finished": False,
            "jobId": "ledgerpartial1",
            "readFrontier": 0,
        }
        spawned: list[tuple[str, str]] = []

        def spawn(job_id: str, video: Path, kind: str) -> bool:
            del video
            spawned.append((job_id, kind))
            return True

        with (
            patch.object(app_module, "_upload_snapshot", return_value=[("up-1", item)]),
            patch.object(app_module, "_spawn_video", side_effect=spawn),
        ):
            app_module._consider_partial_reads()
        self.assertEqual(spawned, [("ledgerpartial1", "partial")])
        self.assertIn("ledgerpartial1", app_module._PARTIAL_IDS)
        job = jobs.get("ledgerpartial1")
        self.assertIsNotNone(job)
        assert job is not None
        self.assertIsNone(job.path)

    def test_a_prefix_without_moov_is_left_alone(self) -> None:
        blob = b"\x00" * (app_module._PARTIAL_FLOOR + 32)
        path = Path(self._dir.name) / "blind.mp4"
        path.write_bytes(blob)
        item = {
            "path": path,
            "name": "mu.mp4",
            "source": "may",
            "size": len(blob) + 1000,
            "ranges": [(0, len(blob))],
            "finished": False,
            "jobId": "ledgerblind1",
        }
        with (
            patch.object(app_module, "_upload_snapshot", return_value=[("up-2", item)]),
            patch.object(app_module, "_spawn_video") as spawn,
        ):
            app_module._consider_partial_reads()
        spawn.assert_not_called()
        self.assertIsNone(jobs.get("ledgerblind1"))
