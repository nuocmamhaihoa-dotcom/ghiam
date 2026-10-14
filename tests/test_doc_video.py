"""Bộ đọc mới doc_video: chuẩn hoá chữ, ghép theo tên, và đọc thật trên ảnh mẫu."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from doc_video.frame import Contact, FrameRead
from doc_video.join import build
from doc_video.text import find_handle, handle_matches_name, looks_like_phone, merge_name, name_key, normalize_phone


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
        self.assertTrue(handle_matches_name("@thuvan99", "Thu Vân"))
        self.assertTrue(handle_matches_name("@thoa.6736599813370", "Thoa 6736599813370"))
        self.assertFalse(handle_matches_name("@anhphonglacuatao2", "Phương Anh"))
        self.assertFalse(handle_matches_name("@linh234_50", "Kim Linh"))

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

    def test_handle_built_from_the_contact_name_joins(self) -> None:
        result = build(
            [
                listing(("0919313090", "Truc"), ("0908031961", "Thu Vân")),
                profile("Tên khác hẳn", "@thuvan99"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0908031961", "@thuvan99")])
        self.assertEqual(result.unopened, [("0919313090", "Truc")])

    def test_similar_name_without_a_matching_handle_is_not_attached(self) -> None:
        result = build(
            [
                listing(("0919200084", "Phương Anh"), ("0900000002", "Lan")),
                profile("Phương An", "@anhphonglacuatao"),
            ]
        )
        self.assertEqual(result.rows, [])
        self.assertEqual([phone for phone, _ in result.unopened], ["0919200084", "0900000002"])

    def test_opening_every_row_on_a_screen_joins_in_order(self) -> None:
        screen = (("0900000001", "Nhà gỗ"), ("0900000002", "Quốc Thanh"), ("0900000003", "Kim Hoa"))
        result = build(
            [
                listing(*screen),
                profile("Wood", "@woodhouse"),
                listing(*screen),
                profile("TV", "@televisionnn"),
                listing(*screen),
                profile("Vinh", "@shopvinh"),
            ]
        )
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [("0900000001", "@woodhouse"), ("0900000002", "@televisionnn"), ("0900000003", "@shopvinh")],
        )
        self.assertEqual(result.unopened, [])
        self.assertEqual(result.review, [])

    def test_scrolling_one_row_at_a_time_still_joins_in_order(self) -> None:
        people = [(f"090000000{i}", f"Khach{i}qq") for i in range(1, 5)]
        frames: list[FrameRead] = []
        for index, person in enumerate(people):
            window = people[max(0, index - 2) : index + 1]
            frames.append(listing(*window))
            frames.append(profile("Tên TikTok", f"@khac{index}xxxx"))
        result = build(frames)
        self.assertEqual([row.phone for row in result.rows], [phone for phone, _ in people])
        self.assertEqual(result.unopened, [])

    def test_one_profile_on_a_full_screen_is_not_guessed(self) -> None:
        result = build(
            [
                listing(("0900000001", "Nhà gỗ"), ("0900000002", "Quốc Thanh"), ("0900000003", "Kim Hoa")),
                profile("Wood", "@woodhouse"),
            ]
        )
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.unopened), 3)

    def test_holding_one_profile_on_screen_counts_as_one_visit(self) -> None:
        result = build(
            [
                listing(("0900000001", "An")),
                profile("Biệt danh", "@xxxx"),
                profile("Biệt danh", "@xxxx"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000001", "@xxxx")])

    def test_the_last_unopened_row_on_the_screen_gets_the_profile(self) -> None:
        screen = (("0900000001", "Truc"), ("0900000002", "Lan"))
        result = build(
            [
                listing(*screen),
                profile("Truc", "@truc"),
                listing(*screen),
                profile("Biệt danh", "@khac"),
            ]
        )
        self.assertEqual(
            {(row.phone, row.handle) for row in result.rows},
            {("0900000001", "@truc"), ("0900000002", "@khac")},
        )

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
