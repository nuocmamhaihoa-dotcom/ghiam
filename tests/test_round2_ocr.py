"""Vòng 2 OCR: chỉ đọc lại khung unknown, không đụng job tap."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from control_plane.screen_read import read_pillow_aggressive
from control_plane.screen_table import FrameObs
from control_plane.video_scan import (
    _alias_single_frames,
    _round2_unknowns,
    _select_expr,
    read_frame_at,
    read_tap_at,
)


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

    def test_round2_copies_profile_onto_near_duplicate(self) -> None:
        jobs = [
            (0.10, 0, Path("a.jpg"), read_frame_at, ("a.jpg", 0.10, {})),
            (0.40, 0, Path("b.jpg"), read_frame_at, ("b.jpg", 0.40, {})),
            (0.55, 0, Path("c.jpg"), read_frame_at, ("c.jpg", 0.55, {})),
        ]
        results = [FrameObs("unknown", at=0.10), FrameObs("unknown", at=0.40), FrameObs("unknown", at=0.55)]
        rescued = FrameObs("profile", at=0.10, profile_name="Tam", profile_username="@tam")
        other = FrameObs("profile", at=0.55, profile_name="Lan", profile_username="@lan")

        def fake_hash(path: str) -> int:
            # a và b giống hệt; c lệch hơn 4 bit dù sát thời gian.
            return 1 if Path(path).name in {"a.jpg", "b.jpg"} else 0xFFFFFFFF

        def fake_read(path: str, at: float, _layout: dict) -> FrameObs:
            if Path(path).name == "c.jpg":
                return other
            return rescued

        with (
            mock.patch("control_plane.video_scan._frame_dhash", side_effect=fake_hash),
            mock.patch("control_plane.video_scan.read_frame_aggressive_at", side_effect=fake_read) as aggressive,
        ):
            updated = _round2_unknowns(jobs, results, submit=None, on_progress=None, layout_dict={})

        self.assertEqual(aggressive.call_count, 2)
        self.assertEqual(updated[1].profile_username, "@tam")
        self.assertEqual(updated[1].at, 0.40)
        self.assertEqual(updated[2].profile_username, "@lan")
        self.assertEqual(updated[2].at, 0.55)

    def test_alias_skips_duplicate_single_frame_not_tap(self) -> None:
        jobs = [
            (0.1, 0, Path("a.jpg"), read_frame_at, ()),
            (0.4, 0, Path("b.jpg"), read_frame_at, ()),
            (0.5, 1, Path("c.jpg"), read_tap_at, ()),
            (0.6, 0, Path("d.jpg"), read_frame_at, ()),
        ]

        def fake_hash(path: str) -> int:
            return 1 if Path(path).name in {"a.jpg", "b.jpg"} else 0xFFFFFFFF

        with mock.patch("control_plane.video_scan._frame_dhash", side_effect=fake_hash):
            aliases = _alias_single_frames(jobs)
        self.assertEqual(aliases, {1: 0})

    def test_header_change_is_read_again(self) -> None:
        jobs = [
            (0.10, 0, Path("a.jpg"), read_frame_at, ("a.jpg", 0.10, {})),
            (0.30, 0, Path("b.jpg"), read_frame_at, ("b.jpg", 0.30, {})),
        ]
        results = [FrameObs("unknown", at=0.10), FrameObs("unknown", at=0.30)]

        def fake_hash(path: str) -> tuple[int, int]:
            # Cùng nền, khác vùng tên/@.
            if Path(path).name == "a.jpg":
                return (1, 1)
            return (1, 0xFFFF)

        with (
            mock.patch("control_plane.video_scan._frame_dhash", side_effect=fake_hash),
            mock.patch(
                "control_plane.video_scan.read_frame_aggressive_at",
                return_value=FrameObs("profile", at=0.1, profile_username="@tam"),
            ) as aggressive,
        ):
            _round2_unknowns(jobs, results, submit=None, on_progress=None, layout_dict={})
        self.assertEqual(aggressive.call_count, 2)

    def test_select_expr_collapses_ranges(self) -> None:
        self.assertEqual(_select_expr([1, 2, 3, 8]), "between(n,1,3)+eq(n,8)")
        self.assertEqual(_select_expr([4]), "eq(n,4)")

    def test_aggressive_skips_invert_on_light_screen(self) -> None:
        image = Image.new("RGB", (90, 160), "white")
        with (
            mock.patch("control_plane.screen_read._require_tesseract"),
            mock.patch("control_plane.screen_read._aggressive_once", return_value=FrameObs("unknown")) as once,
            mock.patch("control_plane.screen_read.ImageOps.invert") as invert,
        ):
            obs = read_pillow_aggressive(image)
        self.assertEqual(obs.kind, "unknown")
        self.assertEqual(once.call_count, 1)
        invert.assert_not_called()

    def test_aggressive_inverts_dark_screen(self) -> None:
        image = Image.new("RGB", (90, 160), (8, 8, 8))
        inverted = Image.new("RGB", (90, 160), "white")
        with (
            mock.patch("control_plane.screen_read._require_tesseract"),
            mock.patch("control_plane.screen_read._aggressive_once", return_value=FrameObs("unknown")) as once,
            mock.patch("control_plane.screen_read.ImageOps.invert", return_value=inverted) as invert,
        ):
            read_pillow_aggressive(image)
        self.assertEqual(once.call_count, 2)
        invert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
