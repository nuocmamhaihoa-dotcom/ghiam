"""Bộ đọc mới doc_video: chuẩn hoá chữ, ghép theo tên, và đọc thật trên ảnh mẫu."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from doc_video.frame import Contact, FrameRead
from doc_video.join import build
from doc_video.text import find_handle, looks_like_phone, merge_name, name_key, normalize_phone


def listing(*pairs: tuple[str, str]) -> FrameRead:
    return FrameRead("list", [Contact(phone, name) for phone, name in pairs])


def profile(name: str, handle: str) -> FrameRead:
    return FrameRead("profile", profile_name=name, handle=handle)


class TextTests(unittest.TestCase):
    def test_name_key_ignores_case_marks_and_spaces(self) -> None:
        self.assertEqual(name_key("Trúc"), name_key("TRUC"))
        self.assertEqual(name_key("Lê Lee"), name_key("LeLee"))
        self.assertNotEqual(name_key("Truc"), name_key("Trung"))

    def test_phone_and_handle(self) -> None:
        self.assertEqual(normalize_phone("0919313090"), "0919313090")
        self.assertEqual(normalize_phone("09193130ó0"), "0919313060")
        self.assertEqual(normalize_phone("0123456789"), "")
        self.assertTrue(looks_like_phone("0919 313 090"))
        self.assertFalse(looks_like_phone("Thoa 6736599813370"))
        self.assertEqual(find_handle("(@truc31798"), "@truc31798")
        self.assertEqual(find_handle("@ 07:49"), "")

    def test_merge_keeps_marks_and_fixes_case_and_digits(self) -> None:
        self.assertEqual(merge_name(["TrUC"], "Truc"), "Truc")
        self.assertEqual(merge_name(["TrUcC"], "Truc"), "Truc")
        self.assertEqual(merge_name(["Thoa ó756599815570"], "Thoa 6736599813370"), "Thoa 6736599813370")
        self.assertEqual(merge_name(["Phước Lê"], "Phudc Le"), "Phước Lê")
        self.assertEqual(merge_name(["LêLee"], "LELee"), "LêLee")
        self.assertEqual(merge_name(["Hồng Mến"], "Hong Men"), "Hồng Mến")


class JoinTests(unittest.TestCase):
    def test_same_name_makes_one_row(self) -> None:
        result = build([listing(("0919313090", "Truc"), ("0908031961", "Thu Vân")), profile("Truc", "@truc31798")])
        self.assertEqual([(r.phone, r.name, r.handle) for r in result.rows], [("0919313090", "Truc", "@truc31798")])
        self.assertEqual(result.unopened, [("0908031961", "Thu Vân")])
        self.assertEqual(result.review, [])

    def test_case_and_marks_do_not_block_the_join(self) -> None:
        result = build([listing(("0919313090", "Trúc"), ("0908031961", "Thu Vân")), profile("TRUC", "@truc31798")])
        self.assertEqual(result.rows[0].phone, "0919313090")
        self.assertEqual(result.rows[0].name, "Trúc")

    def test_different_name_is_not_joined(self) -> None:
        result = build([listing(("0919313090", "Truc"), ("0908031961", "Thu Vân")), profile("Phuong Huynh", "@phuong")])
        self.assertEqual(result.rows, [])
        self.assertEqual(result.review[0].handle, "@phuong")

    def test_two_numbers_with_one_name_use_the_screen_before_the_profile(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan"), ("0900000005", "Mai")),
                listing(("0900000002", "Lan"), ("0900000003", "Hoa")),
                profile("Lan", "@lan.2"),
            ]
        )
        self.assertEqual([(r.phone, r.handle) for r in result.rows], [("0900000002", "@lan.2")])

    def test_two_numbers_with_one_name_on_the_same_screen_go_to_review(self) -> None:
        result = build([listing(("0900000001", "Lan"), ("0900000002", "Lan")), profile("Lan", "@lan")])
        self.assertEqual(result.rows, [])
        self.assertEqual({item.reason for item in result.review}, {"nhiều số cùng tên"})

    def test_one_misread_digit_merges_into_the_common_number(self) -> None:
        frames = [listing(("0919313090", "Truc"), ("0908031961", "Thu Vân")) for _ in range(3)]
        frames.append(listing(("0919313080", "Truc")))
        frames.append(profile("Truc", "@truc31798"))
        result = build(frames)
        self.assertEqual([r.phone for r in result.rows], ["0919313090"])
        self.assertNotIn("0919313080", [phone for phone, _ in result.unopened])

    def test_most_frequent_name_wins(self) -> None:
        frames = [listing(("0908035416", "thovahanh"), ("0908031961", "Thu Vân")) for _ in range(3)]
        frames.append(listing(("0908035416", "Am _ Â 2m ls")))
        frames.append(profile("thovahanh", "@thova"))
        self.assertEqual(build(frames).rows[0].name, "thovahanh")


ASSETS = Path("/home/ubuntu/.cursor/projects/workspace/assets")
SAMPLE_LIST = ASSETS / "01a139a4-3c61-7ad0-8ba9-210e4e292125.jpg"
SAMPLE_PROFILE = ASSETS / "01a139a4-3c81-75eb-821e-baec8d04dae2.jpg"


@unittest.skipUnless(SAMPLE_LIST.exists() and SAMPLE_PROFILE.exists() and shutil.which("tesseract"), "không có ảnh mẫu hoặc tesseract")
class SampleTests(unittest.TestCase):
    def test_two_sample_screens(self) -> None:
        from doc_video.video import read_inputs

        frames = read_inputs([SAMPLE_LIST, SAMPLE_PROFILE], workers=1)
        self.assertEqual(
            [(c.phone, c.name) for c in frames[0].contacts],
            [
                ("0919313090", "Truc"),
                ("0908031961", "Thu Vân"),
                ("0919277810", "Phuong Huynh"),
                ("0919244616", "Thoa 6736599813370"),
                ("0919279715", "Phước Lê"),
                ("0919325515", "LêLee"),
                ("0919188997", "Hồng Mến"),
                ("0908035416", "thovahanh"),
            ],
        )
        self.assertEqual((frames[1].profile_name, frames[1].handle), ("Truc", "@truc31798"))
        result = build(frames)
        self.assertEqual([(r.phone, r.name, r.handle) for r in result.rows], [("0919313090", "Truc", "@truc31798")])


if __name__ == "__main__":
    unittest.main()
