"""Ba hướng đọc một dòng, và năm lần đọc lại khi cả ba lệch."""

from __future__ import annotations

import unittest

from PIL import Image

from control_plane.read_vote import five_variants, needs_reread, vote_line


class ReadVoteTests(unittest.TestCase):
    def test_two_names_agree_and_keep_the_marks(self) -> None:
        text, agreed = vote_line("Lan Anh", "Lân Anh", None, [], kind="name")
        self.assertTrue(agreed)
        self.assertEqual(text, "Lan Anh")
        text, agreed = vote_line("Lan Anh", "Lân Anh", "Lân Anh", [], kind="name")
        self.assertTrue(agreed)
        self.assertEqual(text, "Lân Anh")
        self.assertFalse(needs_reread("Lan Anh", "Lân Anh", None, "name"))

    def test_handle_ignores_letter_case(self) -> None:
        text, agreed = vote_line("@User.name", "@user.name", "@user.name", [], kind="handle")
        self.assertTrue(agreed)
        self.assertEqual(text, "@User.name")

    def test_a_single_read_stays_unconfirmed(self) -> None:
        text, agreed = vote_line("Trần Tùng", "", "", [], kind="name")
        self.assertFalse(agreed)
        self.assertEqual(text, "Trần Tùng")
        self.assertFalse(needs_reread("Trần Tùng", "", None, "name"))

    def test_three_different_names_take_the_rerun_majority(self) -> None:
        self.assertTrue(needs_reread("An", "Bình", "Cường", "name"))
        text, agreed = vote_line(
            "An",
            "Bình",
            "Cường",
            ["Bình", "Binh", "Khác", "Bình", "Một"],
            kind="name",
        )
        self.assertTrue(agreed)
        self.assertEqual(text, "Bình")

    def test_a_tie_or_a_single_vote_is_dropped(self) -> None:
        text, agreed = vote_line("An", "Bình", "Cường", ["An", "Bình", "Cường", "Dũng", "Em"], kind="name")
        self.assertFalse(agreed)
        self.assertEqual(text, "")
        text, agreed = vote_line("An", "Bình", "Cường", ["An", "An", "Bình", "Bình", ""], kind="name")
        self.assertFalse(agreed)
        self.assertEqual(text, "")

    def test_five_variants_change_the_pixels(self) -> None:
        crop = Image.new("RGB", (12, 8), "white")
        variants = five_variants(crop)
        self.assertEqual(len(variants), 5)
        sizes = {(image.width, image.height) for image in variants}
        self.assertIn((12, 8), sizes)
        self.assertIn((24, 16), sizes)
        self.assertIn((36, 24), sizes)


if __name__ == "__main__":
    unittest.main()
