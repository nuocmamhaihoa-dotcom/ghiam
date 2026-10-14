"""Chấm xám che một đoạn: ghép mảnh nhìn thấy, không đoán chữ bị che."""

from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from control_plane.cover_read import (
    apply_cover_stitch,
    assemble_pieces,
    mask_digits,
    phone_from_slots,
)
from control_plane.screen_read import _cover_boxes
from control_plane.screen_table import COVER_REASON, ContactHit, FrameObs, build_table
from control_plane.video_store import BUCKET_REVIEW, classify_result


def _part(left: str, right: str, name: str, at: float, selected: bool = False) -> FrameObs:
    return FrameObs(
        "list",
        (ContactHit("", name, selected, seen_left=left, seen_right=right),),
        at=at,
    )


class MaskTests(unittest.TestCase):
    def test_prefix_and_suffix_fill_ten_digits(self) -> None:
        slots = mask_digits("0989696", "96820")
        self.assertEqual(phone_from_slots(slots), "0989696820")

    def test_conflicting_overlap_is_rejected(self) -> None:
        self.assertIsNone(mask_digits("098211", "20072"))

    def test_gap_in_the_middle_is_not_guessed(self) -> None:
        slots = mask_digits("09821", "7072")
        self.assertEqual(phone_from_slots(slots), "")
        self.assertIsNotNone(slots)


class StitchTests(unittest.TestCase):
    def test_two_frames_of_one_row_become_one_phone(self) -> None:
        table = build_table(
            [
                _part("0989696", "", "Hằng", 1.0, selected=True),
                _part("", "96820", "Hằng", 1.4),
                FrameObs("profile", (), "Hằng", "@hang.dau", at=2.0),
            ]
        )
        self.assertEqual([(row.phone, row.username) for row in table.rows], [("0989696820", "@hang.dau")])
        self.assertEqual(table.review, [])

    def test_clean_frame_wins_over_a_covered_fragment(self) -> None:
        table = build_table(
            [
                FrameObs("list", (ContactHit("0989696820", "Hằng", True),), at=1.0),
                _part("0989696", "", "Hằng", 1.3),
                FrameObs("profile", (), "Hằng", "@hang.dau", at=2.0),
            ]
        )
        self.assertEqual([row.phone for row in table.rows], ["0989696820"])
        self.assertFalse(any(item.reason == COVER_REASON for item in table.review))

    def test_different_names_are_not_glued(self) -> None:
        table = build_table(
            [
                _part("0989696", "", "Hằng", 1.0),
                _part("", "64799", "Nhung Le", 1.2),
            ]
        )
        self.assertEqual(table.rows, [])
        self.assertEqual(table.unopened, [])
        self.assertTrue(any(item.reason == COVER_REASON for item in table.review))

    def test_unfinished_number_is_review_not_a_fake_phone(self) -> None:
        _frames, notes = apply_cover_stitch([_part("0989696", "", "Hằng", 1.0)])
        self.assertEqual(notes[0].reason, COVER_REASON)
        self.assertEqual(notes[0].phone, "")
        self.assertEqual(classify_result("", notes[0].contact_name, "", notes[0].reason), (BUCKET_REVIEW, COVER_REASON))

    def test_phone_without_name_stays_for_review(self) -> None:
        table = build_table(
            [
                _part("09896968", "", "", 1.0),
                _part("", "9696820", "", 1.2),
            ]
        )
        self.assertEqual(table.rows, [])
        self.assertEqual([item.phone for item in table.review], ["0989696820"])

    def test_handle_pieces_join_without_inventing_letters(self) -> None:
        frames, _notes = apply_cover_stitch(
            [
                FrameObs("profile", (), "Hằng", "", at=2.0, handle_left="@thch.n.da", handle_right=""),
                FrameObs("profile", (), "", "", at=2.3, handle_left="", handle_right="n.da.hu145"),
            ]
        )
        self.assertEqual(frames[0].profile_username, "@thch.n.da.hu145")
        self.assertEqual(frames[1].profile_username, "@thch.n.da.hu145")

    def test_handle_hole_is_not_filled(self) -> None:
        self.assertEqual(assemble_pieces(["@thch.n.da", "hu145"]), "")

    def test_plain_pair_is_unchanged(self) -> None:
        table = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=1.0),
                FrameObs("profile", (), "Đặng Thị Tâm", "@dangtam.3", at=2.0),
            ]
        )
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].username, "@dangtam.3")
        self.assertEqual(table.review, [])


class CoverBoxTests(unittest.TestCase):
    def test_gray_circle_is_found_and_a_photo_blob_is_not(self) -> None:
        image = Image.new("RGB", (750, 1334), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((300, 230, 390, 320), fill=(112, 112, 112))
        draw.ellipse((500, 700, 590, 790), fill=(180, 40, 40))
        boxes = _cover_boxes(image)
        self.assertTrue(any(box.x0 < 360 < box.x1 and box.y0 < 270 < box.y1 for box in boxes))
        self.assertFalse(any(box.x0 > 450 for box in boxes))


if __name__ == "__main__":
    unittest.main()
