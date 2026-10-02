"""Bảng âm tiết: chuyển dấu thanh về đúng nguyên âm, không đoán dấu khi có nhiều cách."""

from __future__ import annotations

import unittest

from control_plane.syllables import restore_name, restore_word, valid_syllable

_SURNAMES = (
    "Nguyễn Trần Lê Phạm Hoàng Huỳnh Phan Vũ Võ Đặng Bùi Đỗ Hồ Ngô Dương Lý".split()
)


class SyllableTests(unittest.TestCase):
    def test_common_surnames_are_real_syllables_and_stay_put(self) -> None:
        for name in _SURNAMES:
            self.assertTrue(valid_syllable(name), name)
            self.assertEqual(restore_word(name, allow_unique=False), name)
            self.assertEqual(restore_word(name, allow_unique=True), name)

    def test_a_tone_on_the_wrong_vowel_moves_to_the_real_syllable(self) -> None:
        self.assertEqual(restore_word("Tóan", allow_unique=False), "Toán")
        self.assertEqual(restore_word("Híêu", allow_unique=False), "Hiếu")
        self.assertEqual(restore_word("hoà", allow_unique=False), "hòa")
        self.assertEqual(restore_word("thuỷ", allow_unique=False), "thủy")
        self.assertEqual(restore_word("hùynh", allow_unique=False), "huỳnh")
        self.assertEqual(restore_word("Qúôc", allow_unique=False), "Quốc")
        self.assertEqual(restore_name("Tóan Văn Híêu", allow_unique=False), "Toán Văn Hiếu")

    def test_quynh_and_thuy_keep_the_standard_spelling(self) -> None:
        self.assertTrue(valid_syllable("Quỳnh"))
        self.assertEqual(restore_word("Quỳnh", allow_unique=False), "Quỳnh")
        self.assertEqual(restore_word("Thủy", allow_unique=False), "Thủy")

    def test_an_unaccented_name_is_not_given_a_tone(self) -> None:
        self.assertEqual(restore_name("Nguyen Van Lan", allow_unique=True), "Nguyen Van Lan")
        self.assertEqual(restore_word("Lan", allow_unique=True), "Lan")

    def test_two_tone_marks_on_one_word_are_left_alone(self) -> None:
        self.assertEqual(restore_word("Híếu", allow_unique=False), "Híếu")


if __name__ == "__main__":
    unittest.main()
