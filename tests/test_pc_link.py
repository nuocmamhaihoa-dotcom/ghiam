"""Trần 80% trên PC, và bản nối tự cập nhật khi nội dung file đổi."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from control_plane import pc_connector
from control_plane.pc_connector import (
    CONNECTOR_VERSION,
    _replace_tree,
    _sync_reader,
    _sync_runtime,
    _sync_tessdata,
    _sync_weights,
    adoption,
    bundle_sha256,
    clean_pc_name,
    connector_outdated,
    disk_has_headroom,
    reader_import_path,
    reader_launch_lines,
    restored_template,
    text_sha256,
    zip_entry_safe,
)
from control_plane.pc_reader import CONNECTOR_FILE, connector_template_sha256, launcher_text
from doc_video.video import ffmpeg_thread_count, read_worker_count, within_resource_ceiling


class BudgetTests(unittest.TestCase):
    def test_unset_limits_use_every_cpu(self) -> None:
        env = {"DOC_VIDEO_CPU_FRACTION": "", "DOC_VIDEO_RAM_FRACTION": ""}
        with patch.dict("os.environ", env, clear=False), patch("doc_video.video.os.cpu_count", return_value=16):
            self.assertEqual(read_worker_count(None), 16)
            self.assertEqual(read_worker_count(3), 3)
            self.assertEqual(ffmpeg_thread_count(), 0)
            self.assertTrue(within_resource_ceiling())

    def test_pc_ceiling_stays_at_or_under_80_percent(self) -> None:
        env = {"DOC_VIDEO_CPU_FRACTION": "0.8", "DOC_VIDEO_RAM_FRACTION": "0.8", "DOC_VIDEO_VIETOCR": "0"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("doc_video.video.os.cpu_count", return_value=16),
            patch("doc_video.video._memory_bytes", return_value=(10_000, 10_000)),
            patch("doc_video.video._bytes_per_worker", return_value=100),
        ):
            workers = read_worker_count(None)
            threads = ffmpeg_thread_count()
            self.assertEqual(workers, 12)
            self.assertEqual(threads, 12)
            self.assertLessEqual(workers, 16 * 0.8)
            self.assertEqual(read_worker_count(100), 12)

    def test_exact_80_percent_on_five_cores(self) -> None:
        env = {"DOC_VIDEO_CPU_FRACTION": "0.8", "DOC_VIDEO_RAM_FRACTION": "0.8"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("doc_video.video.os.cpu_count", return_value=5),
            patch("doc_video.video._memory_bytes", return_value=(10_000, 10_000)),
            patch("doc_video.video._bytes_per_worker", return_value=100),
        ):
            self.assertEqual(read_worker_count(None), 4)
            self.assertEqual(ffmpeg_thread_count(), 4)

    def test_low_ram_uses_one_worker_and_can_refuse_a_new_video(self) -> None:
        env = {"DOC_VIDEO_CPU_FRACTION": "0.8", "DOC_VIDEO_RAM_FRACTION": "0.8"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("doc_video.video.os.cpu_count", return_value=16),
            patch("doc_video.video._memory_bytes", return_value=(1000, 250)),
            patch("doc_video.video._bytes_per_worker", return_value=100),
        ):
            self.assertEqual(read_worker_count(None), 1)
            self.assertFalse(within_resource_ceiling())
        with (
            patch.dict("os.environ", env, clear=False),
            patch("doc_video.video._memory_bytes", return_value=(1000, 300)),
            patch("doc_video.video._bytes_per_worker", return_value=100),
        ):
            self.assertTrue(within_resource_ceiling())

    def test_one_core_still_reads(self) -> None:
        env = {"DOC_VIDEO_CPU_FRACTION": "0.8"}
        with patch.dict("os.environ", env, clear=False), patch("doc_video.video.os.cpu_count", return_value=1):
            self.assertEqual(ffmpeg_thread_count(), 1)
            self.assertEqual(read_worker_count(None), 1)


class LinkTests(unittest.TestCase):
    def test_disk_keeps_twenty_percent_free(self) -> None:
        self.assertTrue(disk_has_headroom(100, 30, 0))
        self.assertTrue(disk_has_headroom(100, 20, 0))
        self.assertFalse(disk_has_headroom(100, 19, 0))
        self.assertFalse(disk_has_headroom(100, 30, 15))
        self.assertTrue(disk_has_headroom(0, 0, 999))

    def test_connector_updates_when_the_file_changes(self) -> None:
        self.assertFalse(connector_outdated("aaa", "aaa", CONNECTOR_VERSION))
        self.assertTrue(connector_outdated("aaa", "bbb", CONNECTOR_VERSION))
        self.assertTrue(connector_outdated("aaa", "aaa", CONNECTOR_VERSION + 1))
        self.assertTrue(connector_outdated("aaa", "", CONNECTOR_VERSION + 1))
        self.assertFalse(connector_outdated("aaa", "", CONNECTOR_VERSION))

    def test_filled_script_hashes_back_to_the_template(self) -> None:
        template = CONNECTOR_FILE.read_text(encoding="utf-8")
        server = "http://203.0.113.10:8088"
        token = "abc12345tokenvalue"
        filled = template.replace("__GHIAM_SERVER__", server).replace("__GHIAM_TOKEN__", token)
        self.assertNotIn("__GHIAM_", filled)
        self.assertEqual(text_sha256(restored_template(filled, server, token)), connector_template_sha256())

    def test_phone_upload_chunk_matches_the_server_piece(self) -> None:
        page = (CONNECTOR_FILE.parent / "static" / "video.html").read_text(encoding="utf-8")
        self.assertIn("var CHUNK_BYTES = 8 * 1024 * 1024;", page)
        self.assertIn("120000", page)
        self.assertNotIn("4 * 1024 * 1024", page)
        self.assertIn("Đọc bởi", page)
        self.assertIn("body.link", page)
        self.assertIn("Nhiều PC có thể mở cùng lúc", page)
        self.assertIn("id=\"checkStart\"", page)
        self.assertIn("id=\"checkStop\"", page)
        self.assertIn("/v1/results/check/start", page)
        self.assertIn("/v1/results/check/stop", page)
        self.assertIn("ghiam-pc.bat", page)
        self.assertIn("Không cần gõ địa chỉ, không cần giải nén.", page)
        self.assertNotIn("ghiam-pc.zip", page)

    def test_pc_name_is_one_short_line(self) -> None:
        self.assertEqual(clean_pc_name("  May\nA  "), "May A")
        self.assertEqual(clean_pc_name(""), "PC")
        self.assertEqual(len(clean_pc_name("x" * 80)), 40)
        self.assertGreaterEqual(CONNECTOR_VERSION, 4)
        opened = launcher_text("http://203.0.113.10:8088", "abc12345tokenvalue")
        self.assertTrue(opened.startswith("@echo off\r\n"))
        self.assertIn("http://203.0.113.10:8088", opened)
        self.assertIn("abc12345tokenvalue", opened)
        self.assertNotIn("__GHIAM_", opened)
        self.assertIn("###GHIAM_PS1###", opened)
        self.assertLess(opened.index("/v1/pc/connector.py"), opened.index("/v1/pc/tools/python.zip"))
        self.assertIn("/v1/pc/tools/ffmpeg.zip", opened)
        self.assertIn("/v1/pc/tools/tesseract.exe", opened)
        self.assertIn("/v1/pc/tools/get-pip.py", opened)
        self.assertIn("/v1/pc/tools/wheels.zip", opened)
        self.assertIn("/v1/pc/tools/runtime.zip", opened)
        self.assertIn("--no-index", opened)
        self.assertIn("--no-deps", opened)
        self.assertIn("--speed-time 30", opened)
        self.assertIn("WaitForExit(180000)", opened)
        self.assertNotIn("python.org", opened)
        self.assertNotIn("gyan.dev", opened)
        self.assertNotIn("github.com", opened)
        self.assertNotIn("pypi.org", opened)
        self.assertNotIn("pythonhosted.org", opened)
        self.assertEqual(reader_import_path("/reader", "/other"), "/reader" + os.pathsep + "/other")
        self.assertEqual(reader_import_path("/reader", "/reader" + os.pathsep + "/other"), "/reader" + os.pathsep + "/other")
        existing = ["python312.zip", ".", "Lib\\site-packages", "import site"]
        added = reader_launch_lines(existing, "/reader")
        self.assertEqual(added, [*existing, "/reader"])
        self.assertEqual(reader_launch_lines(added, "/reader"), added)
        source = CONNECTOR_FILE.read_text(encoding="utf-8")
        self.assertIn("socket.gethostname", source)
        self.assertIn('{"name": name}', source)
        self.assertIn("PYTHONPATH", source)
        self.assertIn("--no-index", source)
        self.assertIn("--no-deps", source)
        self.assertIn("/v1/pc/tools/runtime.zip", source)
        self.assertIn("bundle_sha256", source)
        self.assertNotIn("pypi.org", source)
        self.assertNotIn("download.pytorch.org", source)
        self.assertNotIn('DOC_VIDEO_VIETOCR"] = "0"', source)
        self.assertIn('return "fail"', source)


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        pc_connector._RUNTIME_FAIL_AT = 0.0

    def test_bundle_hash_follows_names_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"one")
            (root / "b.txt").write_bytes(b"two")
            forward = bundle_sha256(root, ["a.txt", "b.txt"])
            backward = bundle_sha256(root, ["b.txt", "a.txt"])
            (root / "a.txt").write_bytes(b"changed")
            changed = bundle_sha256(root, ["a.txt", "b.txt"])
        self.assertNotEqual(forward, backward)
        self.assertNotEqual(forward, changed)
        self.assertEqual(len(forward), 64)

    def test_zip_names_stay_inside_the_folder(self) -> None:
        self.assertTrue(zip_entry_safe("doc_video/frame.py"))
        self.assertFalse(zip_entry_safe("../frame.py"))
        self.assertFalse(zip_entry_safe("/tmp/frame.py"))
        self.assertFalse(zip_entry_safe("doc_video\\frame.py"))
        self.assertFalse(zip_entry_safe("C:/frame.py"))
        self.assertFalse(zip_entry_safe(""))

    def test_adoption_waits_until_the_hash_matches(self) -> None:
        self.assertEqual(adoption("old", "new", False, True), (False, False))
        self.assertEqual(adoption("old", "", True, True), (False, False))
        self.assertEqual(adoption("same", "same", True, True), (True, False))
        self.assertEqual(adoption("old", "new", True, False), (True, False))
        self.assertEqual(adoption("old", "new", True, True), (True, True))

    def test_failed_replace_keeps_the_reader_already_there(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "reader"
            old.mkdir()
            (old / "a.py").write_bytes(b"old")
            new = root / "reader-new"
            new.mkdir()
            (new / "a.py").write_bytes(b"new")
            real_rename = Path.rename

            def boom(self: Path, target: Path) -> Path:
                if self == new:
                    raise OSError("locked")
                return real_rename(self, target)

            with patch.object(Path, "rename", boom):
                with self.assertRaises(OSError):
                    _replace_tree(new, old)
            self.assertEqual((old / "a.py").read_bytes(), b"old")
            self.assertEqual((new / "a.py").read_bytes(), b"new")

    def test_reader_is_used_only_after_its_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            reader = cache / "reader"
            reader.mkdir()
            (reader / "a.py").write_bytes(b"old")
            digest = bundle_sha256(reader, ["a.py"])
            with patch("control_plane.pc_connector.CACHE", cache), patch("control_plane.pc_connector._LOADED", False):
                ready, relaunch = _sync_reader(digest, ["a.py"])
            self.assertEqual((ready, relaunch), (True, False))
            self.assertEqual((cache / "reader.sha256").read_text(encoding="utf-8"), digest)

            def wrong_download(_path: str, dest: Path, timeout: float = 600) -> None:
                del timeout
                with zipfile.ZipFile(dest, "w") as archive:
                    archive.writestr("a.py", b"wrong")

            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._download", wrong_download),
            ):
                ready, relaunch = _sync_reader("f" * 64, ["a.py"])
            self.assertEqual((ready, relaunch), (False, False))
            self.assertEqual((reader / "a.py").read_bytes(), b"old")
            self.assertEqual((cache / "reader.sha256").read_text(encoding="utf-8"), digest)

            def good_download(_path: str, dest: Path, timeout: float = 600) -> None:
                del timeout
                with zipfile.ZipFile(dest, "w") as archive:
                    archive.writestr("a.py", b"new")

            new_root = cache / "staged"
            new_root.mkdir()
            (new_root / "a.py").write_bytes(b"new")
            new_digest = bundle_sha256(new_root, ["a.py"])
            (cache / "reader.sha256").write_text("stale", encoding="utf-8")
            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._LOADED", True),
                patch("control_plane.pc_connector._download", good_download),
            ):
                ready, relaunch = _sync_reader(new_digest, ["a.py"])
            self.assertEqual((ready, relaunch), (True, True))
            self.assertEqual((reader / "a.py").read_bytes(), b"new")
            self.assertFalse(_sync_reader("abc", ["../a.py"])[0])

    def test_weights_and_letters_match_before_a_video_is_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            self.assertEqual(_sync_weights(None), (True, False))
            self.assertEqual(_sync_tessdata(None), (True, False))
            folder = cache / "weights"
            folder.mkdir()
            path = folder / "vgg_transformer.pth"
            path.write_bytes(b"weights")
            digest = hashlib.sha256(b"weights").hexdigest()
            with patch("control_plane.pc_connector.CACHE", cache), patch("control_plane.pc_connector._LOADED", True):
                ready, relaunch = _sync_weights({"sha256": digest, "bytes": 7})
            self.assertEqual((ready, relaunch), (True, True))
            called = {"download": 0}

            def blocked(_path: str, dest: Path, timeout: float = 600) -> None:
                del dest, timeout
                called["download"] += 1
                raise AssertionError("không tải khi ổ đĩa không còn chỗ")

            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._disk_ready", return_value=False),
                patch("control_plane.pc_connector._download", blocked),
            ):
                ready, relaunch = _sync_weights({"sha256": "a" * 64, "bytes": 10})
            self.assertEqual((ready, relaunch), (False, False))
            self.assertEqual(called["download"], 0)
            self.assertEqual(path.read_bytes(), b"weights")

            letters = cache / "tessdata"
            letters.mkdir()
            (letters / "eng.traineddata").write_bytes(b"eng")
            (letters / "vie.traineddata").write_bytes(b"vie")
            letter_digest = bundle_sha256(letters, ["eng.traineddata", "vie.traineddata"])
            with patch("control_plane.pc_connector.CACHE", cache), patch("control_plane.pc_connector._LOADED", False):
                ready, relaunch = _sync_tessdata({"sha256": letter_digest, "bytes": 6})
            self.assertEqual((ready, relaunch), (True, False))

    def test_runtime_is_required_when_the_server_has_the_model(self) -> None:
        self.assertEqual(_sync_runtime(None, False), (True, False))
        self.assertEqual(_sync_runtime(None, True), (False, False))
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            archive = cache / "runtime.zip"
            payload = b"wheel-zip"
            archive.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            (cache / "runtime.sha256").write_text(digest, encoding="utf-8")
            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._diacritics_importable", return_value=True),
                patch("control_plane.pc_connector._download", side_effect=AssertionError("không tải lại")),
            ):
                self.assertEqual(_sync_runtime({"sha256": digest, "bytes": len(payload)}, True), (True, False))

            def bad_download(_path: str, dest: Path, timeout: float = 600) -> None:
                del timeout
                dest.write_bytes(b"other")

            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._disk_ready", return_value=True),
                patch("control_plane.pc_connector._download", bad_download),
            ):
                ready, relaunch = _sync_runtime({"sha256": "b" * 64, "bytes": 4}, True)
            self.assertEqual((ready, relaunch), (False, False))
            self.assertFalse((cache / "runtime.sha256").read_text(encoding="utf-8") == "b" * 64)


if __name__ == "__main__":
    unittest.main()
