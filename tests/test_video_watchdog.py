"""Quyết định cập nhật mã đọc video khi PC rảnh."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from pc_agent.video_watchdog import (
    clear_replaced_marker,
    process_alive,
    publish_support_files,
    read_local_version,
    read_reading_state,
    replace_tree,
    should_start_worker,
    upgrade_allowed,
    verify_sha256,
)
from pc_agent.video_worker import (
    _JobSlots,
    ensure_fast_models,
    machine_ram_bytes,
    reader_self_test,
    update_due,
    worker_budget,
    write_worker_state,
)


class VideoWorkerStartTests(unittest.TestCase):
    def test_pc_updates_only_when_it_holds_no_video(self) -> None:
        now = 10_000.0
        self.assertTrue(update_due(12, 13, 0, {}, now))
        self.assertFalse(update_due(12, 13, 1, {}, now))
        self.assertFalse(update_due(13, 13, 0, {}, now))
        self.assertFalse(update_due(13, 0, 0, {}, now))
        self.assertFalse(update_due(12, 13, 0, {"build": 13, "at": now - 60}, now))
        self.assertTrue(update_due(12, 13, 0, {"build": 13, "at": now - 3600}, now))
        self.assertTrue(update_due(12, 14, 0, {"build": 13, "at": now - 60}, now))

    def test_fast_models_come_from_the_hub_then_github(self) -> None:
        asked: list[str] = []

        def hub(name: str) -> bytes:
            asked.append("hub:" + name)
            if name == "vie":
                return b"v" * 8_000_000
            return b"e" * 4_000_000

        def github(url: str) -> bytes:
            asked.append("github:" + url.rsplit("/", 1)[-1])
            return b"v" * 531_275

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "tessdata-fast"
            self.assertTrue(ensure_fast_models(hub, folder, github))
            self.assertEqual((folder / "eng.traineddata").stat().st_size, 4_000_000)
            self.assertEqual((folder / "vie.traineddata").stat().st_size, 531_275)
            self.assertEqual(asked, ["hub:eng", "hub:vie", "github:vie.traineddata"])
            asked.clear()
            self.assertTrue(ensure_fast_models(hub, folder, github))
            self.assertEqual(asked, [])

    def test_missing_fast_models_keep_the_old_ones(self) -> None:
        def broken(_name: str) -> bytes:
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "tessdata-fast"
            self.assertFalse(ensure_fast_models(broken, folder, broken))
            self.assertFalse((folder / "eng.traineddata").exists())

    def test_pc_reads_a_sample_before_taking_videos(self) -> None:
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is required")
        if not Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").is_file():
            self.skipTest("font missing")
        result = reader_self_test()
        self.assertIs(result["readerOk"], True, result)
        self.assertEqual(result["readerNote"], "")
        self.assertGreaterEqual(int(str(result["readerMs"])), 0)


class VideoWatchdogTests(unittest.TestCase):
    def test_pc_keeps_twenty_percent_of_cpu_and_ram(self) -> None:
        workers, reserve = worker_budget(20, 32 * 1024 * 1024 * 1024)
        self.assertEqual((workers, reserve), (16, 4))
        small_workers, small_reserve = worker_budget(20, 2 * 1024 * 1024 * 1024)
        self.assertEqual(small_workers, 6)
        self.assertEqual(small_reserve, 14)
        self.assertEqual(worker_budget(10, None), (8, 2))
        self.assertEqual(worker_budget(4, 64 * 1024 * 1024 * 1024), (3, 1))
        self.assertEqual(worker_budget(1, 32 * 1024 * 1024 * 1024), (1, 0))
        self.assertGreater(machine_ram_bytes(), 0)

    def test_two_videos_split_the_reader_cores(self) -> None:
        slots = _JobSlots(16)
        self.assertTrue(slots.take())
        self.assertEqual(slots.share(), 16)
        self.assertTrue(slots.take())
        self.assertEqual(slots.share(), 8)
        self.assertFalse(slots.take())
        slots.give()
        self.assertEqual(slots.share(), 16)

    def test_one_reading_blocks_an_update(self) -> None:
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=True, age_sec=5, pid_alive=True),
            "busy",
        )
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=False, age_sec=1, pid_alive=True),
            "apply",
        )
        self.assertEqual(
            upgrade_allowed(local=2, remote=2, reading=False, age_sec=0, pid_alive=False),
            "current",
        )
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=True, age_sec=181, pid_alive=True),
            "apply",
        )
        self.assertEqual(
            upgrade_allowed(local=1, remote=2, reading=True, age_sec=5, pid_alive=False),
            "apply",
        )

    def test_replace_keeps_config_outside_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "config.json").write_text('{"hub":"http://hub","token":"secret"}', encoding="utf-8")
            current = base / "current"
            current.mkdir()
            (current / "VERSION").write_text("1", encoding="utf-8")
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, "w") as archive:
                archive.writestr("VERSION", "2")
                archive.writestr("pc_agent/video_worker.py", "print('new')\n")
            payload = blob.getvalue()
            replace_tree(current, payload)
            self.assertEqual(read_local_version(base / "current"), 2)
            self.assertEqual((base / "current" / "pc_agent" / "video_worker.py").read_text(encoding="utf-8"), "print('new')\n")
            self.assertIn("secret", (base / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(verify_sha256(payload, hashlib.sha256(payload).hexdigest()))

    def test_bad_checksum_and_zip_slip_are_rejected(self) -> None:
        self.assertFalse(verify_sha256(b"abc", "0" * 64))
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("../config.json", "stolen")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                replace_tree(Path(folder) / "current", blob.getvalue())
            self.assertFalse((Path(folder) / "config.json").exists())

    def test_support_files_replace_the_connector_and_keep_the_previous_copy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "video_watchdog.py").write_text("old\n", encoding="utf-8")
            current = base / "current" / "pc_agent" / "windows"
            current.mkdir(parents=True)
            (base / "current" / "pc_agent" / "video_watchdog.py").write_text("new\n", encoding="utf-8")
            (current / "Run-VideoWorker.ps1").write_text("run\n", encoding="utf-8")
            publish_support_files(base)
            self.assertEqual((base / "video_watchdog.py").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((base / "video_watchdog.py.prev").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((base / "Run-VideoWorker.ps1").read_text(encoding="utf-8"), "run\n")
            self.assertTrue((base / "watchdog-replaced").is_file())
            clear_replaced_marker(base)
            self.assertFalse((base / "watchdog-replaced").exists())
        self.assertFalse(should_start_worker(pid_alive=True))
        self.assertTrue(should_start_worker(pid_alive=False))

    def test_worker_state_marks_a_live_reading(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            write_worker_state(True, path)
            reading, age, pid = read_reading_state(path)
            self.assertTrue(reading)
            self.assertLess(age, 5)
            self.assertEqual(pid, os.getpid())
            self.assertTrue(process_alive(pid))
            write_worker_state(False, path)
            reading, _age, pid = read_reading_state(path)
            self.assertFalse(reading)
            self.assertEqual(pid, os.getpid())

    def test_say_utf8_when_stdout_is_charmap(self) -> None:
        import sys

        from pc_agent.video_watchdog import say as watchdog_say
        from pc_agent.video_worker import say as worker_say

        raw = io.BytesIO()

        class Stream:
            encoding = "cp1252"
            buffer = raw

            def write(self, text: str) -> int:
                text.encode("cp1252")
                return len(text)

            def flush(self) -> None:
                return None

        old = sys.stdout
        sys.stdout = Stream()
        try:
            message = "PC có card NVIDIA nhưng chưa cài bộ đọc GPU. Đang đọc bằng CPU."
            worker_say(message)
            watchdog_say(message)
        finally:
            sys.stdout = old
        self.assertGreaterEqual(raw.getvalue().count("đ".encode("utf-8")), 2)


if __name__ == "__main__":
    unittest.main()
