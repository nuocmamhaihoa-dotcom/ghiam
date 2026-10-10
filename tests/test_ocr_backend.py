from __future__ import annotations

import os
import unittest
from unittest import mock

from control_plane import ocr_backend
from control_plane.video_store import feeder_count, worker_count


class OcrBackendTests(unittest.TestCase):
    def tearDown(self) -> None:
        ocr_backend.tesserocr_available.cache_clear()
        ocr_backend.system_tessdata.cache_clear()

    def test_resolve_prefers_tesserocr_when_available(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_OCR_ENGINE": "auto"}, clear=False):
            with mock.patch.object(ocr_backend, "gpu_available", return_value=False):
                with mock.patch.object(ocr_backend, "tesserocr_available", return_value=True):
                    self.assertEqual(ocr_backend.resolve_ocr_engine(), "tesserocr")

    def test_paddle_without_gpu_falls_back_to_tesseract_family(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_OCR_ENGINE": "paddle"}, clear=False):
            with mock.patch.object(ocr_backend, "gpu_available", return_value=False):
                with mock.patch.object(ocr_backend, "tesserocr_available", return_value=True):
                    self.assertEqual(ocr_backend.resolve_ocr_engine(), "tesserocr")

    def test_worker_count_uses_all_cpus_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_VIDEO_WORKERS": ""}, clear=False):
            with mock.patch("control_plane.video_store.os.cpu_count", return_value=16):
                self.assertEqual(worker_count(), 16)

    def test_feeder_count_auto_two_on_large_host(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_VIDEO_FEEDERS": ""}, clear=False):
            with mock.patch("control_plane.video_store.os.cpu_count", return_value=16):
                with mock.patch("control_plane.video_store.Path") as path_cls:
                    path_cls.return_value.read_text.return_value = "MemAvailable: 20000000 kB\n"
                    self.assertEqual(feeder_count(), 2)


if __name__ == "__main__":
    unittest.main()
