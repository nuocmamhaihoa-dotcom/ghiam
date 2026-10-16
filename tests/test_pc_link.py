"""Trần 80% trên PC, và bản nối tự cập nhật khi nội dung file đổi."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from control_plane.pc_connector import (
    CONNECTOR_VERSION,
    clean_pc_name,
    connector_outdated,
    disk_has_headroom,
    restored_template,
    text_sha256,
)
from control_plane.pc_reader import CONNECTOR_FILE, connector_template_sha256
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

    def test_pc_name_is_one_short_line(self) -> None:
        self.assertEqual(clean_pc_name("  May\nA  "), "May A")
        self.assertEqual(clean_pc_name(""), "PC")
        self.assertEqual(len(clean_pc_name("x" * 80)), 40)
        self.assertGreaterEqual(CONNECTOR_VERSION, 3)
        source = CONNECTOR_FILE.read_text(encoding="utf-8")
        self.assertIn("socket.gethostname", source)
        self.assertIn('{"name": name}', source)


if __name__ == "__main__":
    unittest.main()
