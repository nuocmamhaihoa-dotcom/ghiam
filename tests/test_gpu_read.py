"""GPU text boxes become the same contact rows, without loading a model."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from control_plane.gpu_read import (
    clear_reader,
    fallback_note,
    lines_from_boxes,
    read_lines,
)
from control_plane.screen_people import sightings_from_lines


class GpuReadTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("CONTROL_OCR_ENGINE", None)
        clear_reader()

    def test_contact_boxes_keep_the_pair_and_drop_a_weak_line(self) -> None:
        lines = lines_from_boxes(
            [
                ("A Tùng Bán Gạch", 0.9, 40, 100, 300, 140),
                ("Trần Tùng", 0.92, 40, 150, 220, 190),
                ("Danh bạ", 0.8, 40, 40, 180, 80),
                ("nhiễu", 0.2, 40, 210, 120, 240),
            ]
        )
        self.assertEqual([line.text for line in lines], ["Danh bạ", "A Tùng Bán Gạch", "Trần Tùng"])
        sightings = sightings_from_lines(lines)
        self.assertEqual(
            sightings,
            [{"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch", "username": ""}],
        )

    def test_read_lines_stays_empty_without_the_gpu_engine(self) -> None:
        os.environ.pop("CONTROL_OCR_ENGINE", None)
        self.assertIsNone(read_lines(Path("missing.png")))

    def test_read_lines_stays_empty_when_the_gpu_libraries_are_missing(self) -> None:
        os.environ["CONTROL_OCR_ENGINE"] = "gpu"
        clear_reader()
        blocked = {"paddleocr": None, "easyocr": None, "torch": None}
        with mock.patch.dict(sys.modules, blocked):
            self.assertIsNone(read_lines(Path("missing.png")))
        self.assertEqual(fallback_note(), "PC có card NVIDIA nhưng chưa cài bộ đọc GPU. Đang đọc bằng CPU.")
        self.assertEqual(fallback_note(), "")
