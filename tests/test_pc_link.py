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
    lang_names,
    reader_import_path,
    reader_launch_lines,
    restored_template,
    text_sha256,
    zip_entry_safe,
)
from control_plane.pc_reader import CONNECTOR_FILE, OPEN_SCRIPT, connector_template_sha256, launcher_text
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
        self.assertEqual(opened.count("###GHIAM_PS1###"), 1)
        mark = "###GHIAM_PS1###"
        script = opened[opened.index(mark) + len(mark):].strip()
        self.assertTrue(script.startswith("$ErrorActionPreference"))
        self.assertIn("while ($true)", script)
        self.assertNotIn("exit 0", opened)
        self.assertNotIn("exit /b", opened)
        self.assertIn("('#'*3)+'GHIAM_PS1'+('#'*3)", opened)
        self.assertIn("/v1/pc/open.ps1", opened)
        self.assertIn("[scriptblock]::Create", opened)
        self.assertIn("# GHIAM-OPEN", opened)
        self.assertIn("$env:PUBLIC", opened)
        self.assertIn("%PUBLIC%\\GhiamPC", opened)
        self.assertIn("GetSystemWebProxy", opened)
        self.assertIn("HttpWebRequest", opened)
        self.assertIn("Local\\GhiamPCWindow", opened)
        self.assertNotIn("Global\\GhiamPC", opened)
        self.assertIn("Đang thử lại", opened)
        self.assertIn(":run", opened)
        self.assertNotIn("/v1/pc/tools/", opened)
        for text in (opened, OPEN_SCRIPT.read_text(encoding="utf-8")):
            self.assertNotIn("--max-time", text)
            self.assertNotIn("curl.exe", text)
            self.assertNotIn("WaitForExit", text)
            self.assertNotIn("Program Files\\Tesseract-OCR", text)
            self.assertNotIn("python.org", text)
            self.assertNotIn("gyan.dev", text)
            self.assertNotIn("github.com", text)
            self.assertNotIn("pypi.org", text)
            self.assertNotIn("pythonhosted.org", text)

    def test_server_installer_needs_no_outside_network_and_never_closes_the_window(self) -> None:
        text = OPEN_SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# GHIAM-OPEN"))
        self.assertFalse(text.startswith("\ufeff"))
        self.assertNotIn("__GHIAM_", text)
        statements = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        self.assertFalse([line for line in statements if line == "exit" or line.startswith("exit ")])
        self.assertLess(text.index("/v1/pc/connector.py"), text.index("/v1/pc/tools/python.zip"))
        for route in ("crt.zip", "python.zip", "wheels.zip", "ffmpeg.zip", "tesseract.zip"):
            self.assertIn(f"/v1/pc/tools/{route}", text)
        self.assertNotIn("/v1/pc/tools/runtime.zip", text)
        self.assertNotIn("/v1/pc/tools/get-pip.py", text)
        self.assertIn("pip-*.whl", text)
        self.assertIn("--no-index", text)
        self.assertIn("System.IO.Compression.ZipFile", text)
        self.assertIn("System32\\tar.exe", text)
        self.assertIn("torch\\lib", text)
        self.assertIn("return", text)
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
        self.assertIn("PC chưa sẵn sàng. Cứ để cửa sổ này mở, PC tự thử lại.", source)
        self.assertNotIn("Mở lại file ghiam-pc.bat", source)
        self.assertIn("Local\\\\GhiamPCReader", source)
        self.assertIn("/v1/pc/tessdata-default.zip", source)
        self.assertIn("TESSDATA_PREFIX", source)

    def test_windows_relaunch_hands_back_to_the_window(self) -> None:
        with (
            patch.object(pc_connector.os, "name", "nt"),
            patch.object(pc_connector.os, "execv", side_effect=AssertionError("Windows không được execv")),
        ):
            self.assertEqual(pc_connector._relaunch(), 2)

    def test_lang_names_skip_the_folder_line(self) -> None:
        listed = 'List of available languages in "C:\\vie\\eng/" (2):\neng\nvie\n'
        self.assertEqual(lang_names(listed), {"eng", "vie"})
        self.assertEqual(lang_names('List of available languages in "/vie/eng/" (0):\n'), set())
        self.assertEqual(lang_names(""), set())


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        pc_connector._RUNTIME_FAIL = ("", 0.0)
        pc_connector._RUNTIME_OK = ""

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
                patch("control_plane.pc_connector._runtime_problem", return_value="") as probe,
                patch("control_plane.pc_connector._download", side_effect=AssertionError("không tải lại")),
            ):
                self.assertEqual(_sync_runtime({"sha256": digest, "bytes": len(payload)}, True), (True, False))
                self.assertEqual(_sync_runtime({"sha256": digest, "bytes": len(payload)}, True), (True, False))
                self.assertEqual(probe.call_count, 1)

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

    def test_runtime_that_cannot_load_is_reinstalled_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            payload = b"wheel-zip"
            digest = hashlib.sha256(payload).hexdigest()
            (cache / "runtime.zip").write_bytes(payload)
            (cache / "runtime.sha256").write_text(digest, encoding="utf-8")
            installs: list[bool] = []

            def install(_folder: Path, force: bool = False) -> bool:
                installs.append(force)
                return True

            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._disk_ready", return_value=True),
                patch("control_plane.pc_connector._extract_wheels", return_value=True),
                patch("control_plane.pc_connector._install_wheels", install),
                patch(
                    "control_plane.pc_connector._runtime_problem",
                    return_value="ModuleNotFoundError: No module named 'typing_extensions'",
                ),
                patch("control_plane.pc_connector._download", side_effect=AssertionError("không tải lại")),
                patch("builtins.print") as shown,
            ):
                self.assertEqual(_sync_runtime({"sha256": digest, "bytes": len(payload)}, True), (False, False))
                self.assertEqual(_sync_runtime({"sha256": digest, "bytes": len(payload)}, True), (False, False))
            self.assertEqual(installs, [True])
            said = " ".join(str(call.args[0]) for call in shown.call_args_list if call.args)
            self.assertIn("typing_extensions", said)

            with (
                patch("control_plane.pc_connector.CACHE", cache),
                patch("control_plane.pc_connector._disk_ready", return_value=True),
                patch("control_plane.pc_connector._extract_wheels", return_value=True),
                patch("control_plane.pc_connector._install_wheels", install),
                patch("control_plane.pc_connector._runtime_problem", return_value=""),
                patch("control_plane.pc_connector._download", lambda _path, dest, timeout=600: dest.write_bytes(b"new")),
            ):
                fresh = hashlib.sha256(b"new").hexdigest()
                self.assertEqual(_sync_runtime({"sha256": fresh, "bytes": 3}, True), (True, True))
            self.assertEqual((cache / "runtime.sha256").read_text(encoding="utf-8"), fresh)

    def test_runtime_probe_reports_the_import_error(self) -> None:
        failed = type("Done", (), {"returncode": 1, "stdout": "", "stderr": "Traceback\nModuleNotFoundError: No module named 'sympy'\n"})()
        with patch("control_plane.pc_connector.subprocess.run", return_value=failed):
            self.assertEqual(pc_connector._runtime_problem(), "ModuleNotFoundError: No module named 'sympy'")
        passed = type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch("control_plane.pc_connector.subprocess.run", return_value=passed):
            self.assertEqual(pc_connector._runtime_problem(), "")

    def test_plain_letters_sync_into_their_own_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            source = cache / "src"
            source.mkdir()
            (source / "eng.traineddata").write_bytes(b"plain-eng")
            (source / "vie.traineddata").write_bytes(b"plain-vie")
            digest = bundle_sha256(source, ["eng.traineddata", "vie.traineddata"])
            routes: list[str] = []

            def fetch(path: str, dest: Path, timeout: float = 600) -> None:
                del timeout
                routes.append(path)
                with zipfile.ZipFile(dest, "w") as archive:
                    archive.write(source / "eng.traineddata", "eng.traineddata")
                    archive.write(source / "vie.traineddata", "vie.traineddata")

            with patch("control_plane.pc_connector.CACHE", cache), patch("control_plane.pc_connector._download", fetch):
                self.assertEqual(pc_connector._sync_plain_letters({"sha256": digest, "bytes": 18}), (True, False))
            self.assertEqual(routes, ["/v1/pc/tessdata-default.zip"])
            self.assertEqual((cache / "tessdata-default" / "vie.traineddata").read_bytes(), b"plain-vie")
            self.assertEqual((cache / "tessdata-default.sha256").read_text(encoding="utf-8"), digest)
            self.assertFalse((cache / "tessdata").exists())
            with patch("control_plane.pc_connector.CACHE", cache), patch.dict("os.environ", {}, clear=False):
                with (
                    patch("control_plane.pc_connector._limit_pc"),
                    patch("control_plane.pc_connector._windows_temp"),
                    patch("control_plane.pc_connector._publish_reader_path"),
                ):
                    pc_connector._prepare_env()
                    self.assertEqual(os.environ["TESSDATA_PREFIX"], str(cache / "tessdata-default"))

    def test_tools_need_the_plain_letters_used_by_the_frame_scan(self) -> None:
        listed = {
            (): 'List of available languages in "C:/t/" (1):\neng\n',
        }

        def run(command, **_kwargs):
            key = tuple(command[1:-1])
            return type("Done", (), {"stdout": listed.get(key, 'List (2):\neng\nvie\n')})()

        with (
            patch("control_plane.pc_connector.shutil.which", return_value="tool"),
            patch("control_plane.pc_connector.subprocess.run", run),
            patch("builtins.print") as shown,
        ):
            self.assertFalse(pc_connector._tools_ok())
        said = " ".join(str(call.args[0]) for call in shown.call_args_list if call.args)
        self.assertIn("bộ chữ mặc định", said)
        listed[()] = 'List of available languages in "C:/t/" (2):\neng\nvie\n'
        with (
            patch("control_plane.pc_connector.shutil.which", return_value="tool"),
            patch("control_plane.pc_connector.subprocess.run", run),
        ):
            self.assertTrue(pc_connector._tools_ok())


if __name__ == "__main__":
    unittest.main()
