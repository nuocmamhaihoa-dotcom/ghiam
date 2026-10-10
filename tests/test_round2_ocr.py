"""Vòng 2 OCR: chỉ đọc lại khung unknown, không đụng job tap."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from control_plane.screen_table import FrameObs
from control_plane.video_scan import _round2_unknowns, read_frame_at, read_tap_at


class Round2OcrTests(unittest.TestCase):
    def test_round2_replaces_unknown_frame_jobs_only(self) -> None:
        jobs = [
            (0.1, 0, Path("a.jpg"), read_frame_at, ("a.jpg", 0.1, {})),
            (0.2, 1, Path("b.jpg"), read_tap_at, (["b.jpg"], 0.2)),
            (0.3, 0, Path("c.jpg"), read_frame_at, ("c.jpg", 0.3, {})),
        ]
        results = [
            FrameObs("unknown", at=0.1),
            FrameObs("unknown", at=0.2),
            FrameObs("list", at=0.3),
        ]
        rescued = FrameObs("profile", at=0.1, profile_name="Tam", profile_username="@tam")

        with mock.patch("control_plane.video_scan.read_frame_aggressive_at", return_value=rescued) as aggressive:
            updated = _round2_unknowns(
                jobs,
                results,
                submit=None,
                on_progress=None,
                layout_dict={},
            )

        self.assertEqual(aggressive.call_count, 1)
        self.assertEqual(updated[0].kind, "profile")
        self.assertEqual(updated[0].profile_username, "@tam")
        self.assertEqual(updated[1].kind, "unknown")  # tap không vào vòng 2
        self.assertEqual(updated[2].kind, "list")

    def test_round2_keeps_unknown_when_aggressive_also_fails(self) -> None:
        jobs = [(0.1, 0, Path("a.jpg"), read_frame_at, ("a.jpg", 0.1, {}))]
        results = [FrameObs("unknown", at=0.1)]
        with mock.patch(
            "control_plane.video_scan.read_frame_aggressive_at",
            return_value=FrameObs("unknown", at=0.1),
        ):
            updated = _round2_unknowns(
                jobs,
                results,
                submit=None,
                on_progress=None,
                layout_dict={},
            )
        self.assertEqual(updated[0].kind, "unknown")


if __name__ == "__main__":
    unittest.main()
