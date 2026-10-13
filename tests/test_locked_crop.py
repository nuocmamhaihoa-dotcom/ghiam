"""Khóa OCR vào dòng danh bạ và dòng @. Model nhìn ảnh chỉ chạy khi còn thiếu @."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from control_plane.read_fallback import apply_read_fallbacks
from control_plane.screen_layout import LayoutProfile
from control_plane.screen_read import (
    Box,
    at_zone_box,
    contact_row_box,
    header_line_bands,
    split_locked_row,
)
from control_plane.screen_table import ContactHit, FrameObs, Row, Table, Unopened
from control_plane.vision_gap import apply_vision_for_gaps, vision_enabled, vision_header_crop


class LockedCropTests(unittest.TestCase):
    def test_contact_row_stops_before_the_follow_button(self) -> None:
        image = Image.new("RGB", (400, 800), "white")
        button = Box(300, 200, 380, 232)
        row = contact_row_box(image, button, 300, 80)
        self.assertLessEqual(row.x1, button.x0)
        self.assertGreater(row.w, 40)
        self.assertLess(row.h, 80)
        self.assertGreaterEqual(row.y0, 0)
        self.assertLess(row.y1, image.height)
        phone, name = split_locked_row(row)
        self.assertLess(phone.h, row.h)
        self.assertLess(name.h, row.h)
        self.assertLess(phone.y0, name.y0)
        self.assertGreaterEqual(phone.y1, name.y0)

    def test_at_zone_is_the_name_and_handle_band(self) -> None:
        zone = at_zone_box(400, 800, None)
        self.assertGreater(zone.y0, int(0.06 * 800))
        self.assertLess(zone.y1, int(0.28 * 800))
        self.assertLess(zone.h, int(0.20 * 800))
        learned = LayoutProfile(samples=4, header_x0=0.04, header_y0=0.06, header_x1=0.82, header_y1=0.48)
        tightened = at_zone_box(400, 800, learned)
        self.assertGreater(tightened.y0, int(0.06 * 800))
        self.assertLess(tightened.y1, int(0.30 * 800))
        self.assertLess(tightened.h, int(0.48 * 800) - int(0.06 * 800))

    def test_header_bands_are_single_lines(self) -> None:
        header = Box(10, 80, 320, 480)
        bands = header_line_bands(header)
        self.assertGreaterEqual(len(bands), 2)
        self.assertLessEqual(len(bands), 8)
        self.assertTrue(all(band.h <= 48 for band in bands))
        self.assertTrue(all(band.h * 4 < header.h for band in bands))
        self.assertTrue(all(band.y0 >= header.y0 and band.y1 <= header.y1 for band in bands))

    def test_vision_crop_is_smaller_than_the_frame(self) -> None:
        image = Image.new("RGB", (400, 800), "white")
        crop = vision_header_crop(image)
        self.assertLess(crop.height, image.height * 0.5)
        self.assertLess(crop.width * crop.height, image.width * image.height * 0.55)


class VisionGapTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        old = os.environ.pop("CONTROL_VISION", None)
        try:
            self.assertFalse(vision_enabled())
        finally:
            if old is not None:
                os.environ["CONTROL_VISION"] = old

    def test_complete_row_does_not_call_the_reader(self) -> None:
        table = Table(rows=[Row("0982117072", "Đặng Thị Tâm", "@dangtam.3")])
        frames = [(Path("x.jpg"), FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=1.0))]

        def reader(_path: Path) -> tuple[str, str]:
            raise AssertionError("hàng đã có @")

        self.assertIs(apply_vision_for_gaps(table, frames, reader=reader), table)
        self.assertIs(apply_read_fallbacks(table, frames), table)

    def test_reader_fills_only_a_phone_that_lacks_a_handle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            near = Path(folder) / "near.jpg"
            far = Path(folder) / "far.jpg"
            Image.new("RGB", (40, 40), "white").save(near)
            Image.new("RGB", (40, 40), "white").save(far)
            frames = [
                (far, FrameObs("list", (ContactHit("0900000001", "Người Khác", True),), at=1.0)),
                (near, FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=20.0)),
            ]
            table = Table(
                rows=[Row("0900000001", "Người Khác", "@khac.1")],
                unopened=[Unopened("0982117072", "Đặng Thị Tâm")],
            )
            seen: list[Path] = []

            def reader(path: Path) -> tuple[str, str]:
                seen.append(path)
                return "Đặng Thị Tâm", "@dangtam.3"

            result = apply_vision_for_gaps(table, frames, reader=reader)
        self.assertEqual(seen, [near])
        self.assertEqual(
            [(row.phone, row.username) for row in result.rows],
            [("0900000001", "@khac.1"), ("0982117072", "@dangtam.3")],
        )
        self.assertFalse(result.unopened)

    def test_unpaired_vision_handle_is_not_stored(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frame.jpg"
            Image.new("RGB", (40, 40), "white").save(path)
            frames = [(path, FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=5.0))]
            table = Table(unopened=[Unopened("0982117072", "Đặng Thị Tâm")])

            def reader(_path: Path) -> tuple[str, str]:
                return "Người Lạ", "@nguoila.9"

            result = apply_vision_for_gaps(table, frames, reader=reader)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened[0].phone, "0982117072")
        self.assertFalse(result.review)


if __name__ == "__main__":
    unittest.main()
