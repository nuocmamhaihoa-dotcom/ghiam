"""Hàng thiếu ô được đọc lại 4 giây trước và 4 giây sau khung thiếu."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_plane.screen_table import ContactHit, FrameObs, Review, Table, build_table
from control_plane.video_scan import (
    REFILL_FPS,
    REFILL_RADIUS_SEC,
    incomplete_anchors,
    merge_time_windows,
    refill_frame_numbers,
    _refill_missing_fields,
)


def _list(phone: str, name: str, at: float, selected: bool = False) -> FrameObs:
    return FrameObs("list", (ContactHit(phone, name, selected),), at=at)


def _profile(name: str, username: str, at: float) -> FrameObs:
    return FrameObs("profile", (), name, username, at=at)


class RefillWindowTests(unittest.TestCase):
    def test_windows_cover_four_seconds_each_side_and_merge(self) -> None:
        self.assertEqual(REFILL_RADIUS_SEC, 4.0)
        merged = merge_time_windows([10.0, 12.0])
        self.assertEqual(merged, [(6.0, 16.0)])
        apart = merge_time_windows([10.0, 30.0])
        self.assertEqual(apart, [(6.0, 14.0), (26.0, 34.0)])

    def test_complete_row_is_not_reread(self) -> None:
        frames = [
            _list("0982117072", "Đặng Thị Tâm", 10.0, selected=True),
            _profile("Đặng Thị Tâm", "@dangtam.3", 12.0),
        ]
        table = build_table(frames)
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(incomplete_anchors(frames, table), [])

    def test_missing_username_rereads_around_the_phone(self) -> None:
        frames = [_list("0982117072", "Đặng Thị Tâm", 10.0, selected=True)]
        table = build_table(frames)
        self.assertEqual([item.phone for item in table.unopened], ["0982117072"])
        self.assertEqual(incomplete_anchors(frames, table), [10.0])
        numbers = refill_frame_numbers(merge_time_windows([10.0]), [10.0], duration=30.0)
        times = [number / REFILL_FPS for number in numbers]
        self.assertGreaterEqual(min(times), 6.0)
        self.assertLessEqual(max(times), 14.0)
        self.assertNotIn(10.0, times)
        self.assertIn(6.0, times)
        self.assertIn(14.0, times)

    def test_missing_phone_rereads_around_the_profile(self) -> None:
        frames = [_profile("Đặng Thị Tâm", "@dangtam.3", 20.0)]
        table = Table(review=[Review("", "", "Đặng Thị Tâm", "@dangtam.3", "đã mở hồ sơ nhưng chưa thấy số")])
        self.assertEqual(incomplete_anchors(frames, table), [20.0])

    def test_samples_stop_at_the_end_of_the_video(self) -> None:
        numbers = refill_frame_numbers([(6.0, 14.0)], [], duration=12.0)
        self.assertTrue(numbers)
        self.assertLess(max(numbers) / REFILL_FPS, 12.0)
        self.assertGreaterEqual(min(numbers) / REFILL_FPS, 6.0)

    def test_refill_inserts_the_profile_found_inside_the_four_second_window(self) -> None:
        observations = [
            (Path("list.jpg"), _list("0982117072", "Đặng Thị Tâm", 10.0, selected=True)),
        ]
        messages: list[str] = []

        def fake_extract(path, folder, numbers, fps, on_progress=None, prefix="f"):
            self.assertEqual(fps, REFILL_FPS)
            self.assertEqual(prefix, "g")
            self.assertTrue(numbers)
            self.assertTrue(all(6.0 <= number / fps <= 14.0 for number in numbers))
            return {number: folder / f"g_{number}.jpg" for number in numbers}

        def fake_read(path, at, layout=None):
            if abs(at - 12.4) < 0.06:
                return FrameObs("profile", (), "Đặng Thị Tâm", "@dangtam.3", at=at)
            return FrameObs("unknown", at=at)

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch("control_plane.video_scan.extract_frames", fake_extract),
                patch("control_plane.video_scan.read_frame_aggressive_at", fake_read),
                patch("control_plane.video_validate.probe_duration_sec", return_value=30.0),
                patch("control_plane.video_scan._collapse_similar_frames", side_effect=lambda targets: (targets, [])),
            ):
                merged = _refill_missing_fields(
                    Path("clip.mp4"),
                    Path(folder),
                    observations,
                    submit=None,
                    on_progress=messages.append,
                    layout_dict={},
                )
        self.assertTrue(any("±4 giây" in message for message in messages))
        table = build_table([obs for _path, obs in merged])
        self.assertEqual([(row.phone, row.username) for row in table.rows], [("0982117072", "@dangtam.3")])
        self.assertFalse(table.unopened)
        times = [obs.at for _path, obs in merged]
        self.assertEqual(times, sorted(times))


if __name__ == "__main__":
    unittest.main()
