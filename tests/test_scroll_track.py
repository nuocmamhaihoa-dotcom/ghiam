"""Cuộn danh bạ chỉ đọc dải mới, và khung mờ lúc chuyển trang bị bỏ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from control_plane import scroll_track, stage_timing
from control_plane.screen_people import TextLine
from control_plane.screen_steps import _FrameRead, _apply_scroll, _drop_fades


def _bar(top: int, *, busy: bool = False) -> Image.Image:
    image = Image.new("L", (80, 130), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, top, 50, top + 12), fill=0)
    if busy:
        for y in range(19, 110):
            for x in range(62, 68):
                if (x + y) % 2 == 0:
                    draw.point((x, y), fill=0)
    return image


def _sharp() -> Image.Image:
    image = Image.new("L", (80, 130), 255)
    draw = ImageDraw.Draw(image)
    for x in range(0, 80, 3):
        draw.line((x, 0, x, 130), fill=0)
    return image


class ScrollTrackTests(unittest.TestCase):
    def tearDown(self) -> None:
        stage_timing.reset()

    def test_a_pure_vertical_move_is_the_shift(self) -> None:
        self.assertEqual(scroll_track.vertical_shift(_bar(70), _bar(62)), scroll_track.Shift(8, True))
        self.assertEqual(scroll_track.vertical_shift(_bar(62), _bar(70)), scroll_track.Shift(-8, True))
        self.assertEqual(scroll_track.vertical_shift(_bar(70), _bar(70)), scroll_track.Shift(0, False))
        self.assertFalse(scroll_track.vertical_shift(_bar(70), Image.new("L", (80, 130), 0)).confident)

    def test_a_taller_image_keeps_the_fraction_of_the_shift(self) -> None:
        def tall(top: int) -> Image.Image:
            image = Image.new("L", (80, 520), 255)
            ImageDraw.Draw(image).rectangle((8, top, 50, top + 36), fill=0)
            return image

        found = scroll_track.vertical_shift(tall(280), tall(248))
        self.assertTrue(found.confident)
        self.assertEqual(found.dy, 32)
        self.assertTrue(scroll_track.overlaps(TextLine("a", 0, 100, 140), TextLine("b", 0, 120, 160)))
        self.assertFalse(scroll_track.overlaps(TextLine("a", 0, 100, 140), TextLine("b", 0, 200, 240)))

    def test_thumb_pixels_scale_onto_the_prepared_frame(self) -> None:
        self.assertEqual(scroll_track.prepared_dy(16, 1373), 40)
        self.assertEqual(scroll_track.prepared_dy(4, 1373, 130), 40)
        self.assertEqual(scroll_track.prepared_dy(0, 1373), 0)
        top, bottom = scroll_track.strip_bounds(1000, 29)
        self.assertGreater(top, 0)
        self.assertEqual(bottom, 1000)
        top, bottom = scroll_track.strip_bounds(1000, -29)
        self.assertEqual(top, 0)
        self.assertLess(bottom, 1000)

    def test_safe_lines_move_and_a_strip_line_is_not_kept_twice(self) -> None:
        safe = TextLine("Lan", 10, 200, 240)
        edge = TextLine("Mép", 10, 10, 40)
        self.assertTrue(scroll_track.in_safe(safe, 1000))
        self.assertFalse(scroll_track.in_safe(edge, 1000))
        moved = scroll_track.shift_lines([safe, edge], 50, 1000)
        self.assertEqual([(line.text, line.top) for line in moved], [("Lan", 150)])
        self.assertTrue(scroll_track.outside_strip(safe, 800, 1000))
        self.assertFalse(scroll_track.outside_strip(TextLine("Mới", 10, 860, 900), 800, 1000))

    def test_tsv_tops_follow_the_crop(self) -> None:
        raw = "5\t1\t1\t1\t1\t1\t12\t4\t20\t8\t90\tLan"
        self.assertIn("\t44\t", scroll_track.offset_tsv(raw, 40))
        self.assertEqual(scroll_track.offset_tsv(raw, 0), raw)

    def test_a_blurry_still_frame_is_dropped_and_a_scroll_is_kept(self) -> None:
        self.assertEqual(scroll_track.fade_indexes([_sharp(), Image.new("L", (80, 130), 180), _sharp()]), [1])
        scrolling = [_bar(70, busy=True), _bar(62), _bar(54, busy=True)]
        self.assertEqual(scroll_track.fade_indexes(scrolling), [])
        flat = Image.new("L", (80, 130), 180)
        many = [_sharp(), flat, flat, flat, _sharp()]
        self.assertEqual(scroll_track.fade_indexes(many), [])

    def test_a_contact_list_keeps_old_lines_and_adds_the_new_strip(self) -> None:
        stage_timing.reset()
        height = 1000
        old = TextLine("Danh bạ", 40, 200, 240)
        fresh = TextLine("Người mới", 40, 860, 900)
        reads: list[_FrameRead | None] = [
            _FrameRead(0.0, ["Danh bạ"], [], 1, 1, [old], height, False),
            _FrameRead(0.5, ["Người mới"], [], 1, 1, [fresh], height, True),
        ]
        shifts = [scroll_track.Shift(0, False), scroll_track.Shift(4, True)]
        _apply_scroll([(0.0, Path("a.png")), (0.5, Path("b.png"))], reads, shifts)
        second = reads[1]
        assert second is not None
        texts = [line.text for line in second.lines]
        self.assertEqual(texts, ["Danh bạ", "Người mới"])
        self.assertEqual(second.lines[0].top, 200 - scroll_track.prepared_dy(4, height))
        self.assertEqual(stage_timing.snapshot()["scroll.strips"][1], 1)

    def test_a_strip_after_a_frame_that_is_not_a_list_is_read_again_in_full(self) -> None:
        stage_timing.reset()
        height = 1000
        reads: list[_FrameRead | None] = [
            _FrameRead(0.0, [], [], 0, 0, [TextLine("Xin chào", 40, 200, 240)], height, False),
            _FrameRead(0.5, [], [], 0, 0, [TextLine("Dải", 40, 860, 900)], height, True),
        ]
        full = _FrameRead(0.5, ["cả khung"], [], 2, 2, [TextLine("Cả khung", 40, 300, 340)], height, False)

        def _full(item: tuple[float, Path], thumb_dy: int | None = None) -> _FrameRead:
            del item, thumb_dy
            return full

        with patch("control_plane.screen_steps._read_one", side_effect=_full):
            _apply_scroll(
                [(0.0, Path("a.png")), (0.5, Path("b.png"))],
                reads,
                [scroll_track.Shift(0, False), scroll_track.Shift(4, True)],
            )
        second = reads[1]
        assert second is not None
        self.assertFalse(second.strip)
        self.assertEqual([line.text for line in second.lines], ["Cả khung"])
        self.assertNotIn("scroll.strips", stage_timing.snapshot())

    def test_drop_fades_removes_the_blurry_profile_frame(self) -> None:
        stage_timing.reset()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths: list[tuple[float, Path]] = []
            for index, kind in enumerate(("sharp", "flat", "sharp")):
                path = root / f"{index}.png"
                image = Image.new("L", (200, 400), 255)
                if kind == "sharp":
                    ImageDraw.Draw(image).rectangle((20, 40, 180, 360), outline=0, width=3)
                    for y in range(40, 360, 8):
                        ImageDraw.Draw(image).line((20, y, 180, y), fill=0)
                image.save(path)
                paths.append((float(index), path))
            kept = _drop_fades(paths)
        self.assertEqual([item[0] for item in kept], [0.0, 2.0])
        self.assertEqual(stage_timing.snapshot()["scroll.fades"][1], 1)


if __name__ == "__main__":
    unittest.main()
