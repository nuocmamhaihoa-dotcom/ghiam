from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from control_plane.screen_layout import (
    LayoutProfile,
    calibrate_from_paths,
    load_layout,
    measure_list_layout,
    merge_layout,
    save_layout,
)
from control_plane.video_scan import burst_frame_numbers


class LayoutTests(unittest.TestCase):
    def test_merge_and_roundtrip(self) -> None:
        base = LayoutProfile(device="iPhone", follow_x0=0.70, samples=4)
        measured = LayoutProfile(device="iPhone", follow_x0=0.80, samples=2)
        merged = merge_layout(base, measured, weight=0.5)
        self.assertAlmostEqual(merged.follow_x0, 0.75)
        self.assertEqual(merged.samples, 6)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            save_layout(data, merged)
            loaded = load_layout(data, "iPhone")
            self.assertAlmostEqual(loaded.follow_x0, 0.75)
            self.assertEqual(loaded.device, "iPhone")

    def test_burst_frames_cover_transition_window(self) -> None:
        numbers = burst_frame_numbers([1.0], burst_fps=60.0, radius=0.12)
        self.assertIn(60, numbers)  # t=1.0s @60fps
        self.assertTrue(min(numbers) <= 53)
        self.assertTrue(max(numbers) >= 67)

    def test_measure_list_layout_from_pink_column(self) -> None:
        # Cột Follow hồng giả lập bên phải — đủ 4 nút.
        image = Image.new("RGB", (400, 800), "white")
        draw = ImageDraw.Draw(image)
        for index in range(4):
            y = 120 + index * 140
            # aspect ~2.5, rộng ≤25% khung, cột phải — khớp bộ lọc Follow danh bạ.
            draw.rectangle((300, y, 380, y + 32), fill=(255, 20, 90))
        hit = measure_list_layout(image)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertGreater(hit.follow_x0, 0.6)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.jpg"
            image.save(path)
            image.save(Path(folder) / "b.jpg")
            cal = calibrate_from_paths([path, Path(folder) / "b.jpg"])
            self.assertIsNotNone(cal)


if __name__ == "__main__":
    unittest.main()
