"""Bộ đọc mới doc_video: chuẩn hoá chữ, ghép theo tên, và đọc thật trên ảnh mẫu."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from doc_video.frame import Contact, FrameRead
from doc_video.join import build
from doc_video.ocr import TextLine
from doc_video.frame import Layout, ListRow
from doc_video.video import plan_reads, resolve_taps
from doc_video.text import (
    clean_name,
    find_handle,
    fix_underscores,
    handle_from_viet,
    handle_matches_name,
    looks_like_phone,
    name_key,
    normalize_phone,
    vote_chars,
)


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
        self.assertFalse(handle_matches_name("@user06867539", "user606720361"))
        self.assertFalse(handle_matches_name("@user54308927", "user6289208064852"))
        self.assertTrue(handle_matches_name("@user6289208064852", "user6289208064852"))

    def test_handles_are_lowercase_like_tiktok(self) -> None:
        self.assertEqual(find_handle("@User_Abc.12"), "@user_abc.12")
        self.assertEqual(handle_from_viet("Oquocnguyet81792"), "@quocnguyet81792")
        self.assertEqual(handle_from_viet("Qhuong.tv"), "@huong.tv")
        self.assertEqual(handle_from_viet("0090fy5xr"), "@090fy5xr")

    def test_names_keep_special_characters_except_bar(self) -> None:
        self.assertEqual(clean_name("Huyền Phương (Cà)"), "Huyền Phương (Cà)")
        self.assertEqual(clean_name("Lan & Hoa"), "Lan & Hoa")
        self.assertEqual(clean_name("Thắm | Shop"), "Thắm Shop")

    def test_vote_keeps_what_most_readers_agree_on(self) -> None:
        reads = [("Hữu Nhung", 1.5), ("Hữ Nhung", 1.0), ("Hữu Nhung", 1.0), ("Huu Nhung", 0.7)]
        self.assertEqual(vote_chars(reads), "Hữu Nhung")
        reads = [("Ngô Lan", 1.5), ("Ng6 Lan", 1.0), ("Ngô Lan", 1.0), ("Ngo Lan", 0.7)]
        self.assertEqual(vote_chars(reads), "Ngô Lan")
        reads = [("nhungphuong 8712", 1.5), ("nhungphuong8712", 1.0), ("nhungphuong8712", 1.0), ("nhungphuong8712", 0.7)]
        self.assertEqual(vote_chars(reads), "nhungphuong8712")
        reads = [("_thoa.xuan_", 0.7), ("thoa.xuan", 1.5), ("_thoa.xuan_", 1.0), ("_thoa.xuan_", 1.0)]
        self.assertEqual(vote_chars(reads), "_thoa.xuan_")

    def test_underscore_strokes_fix_double_and_leading_underscores(self) -> None:
        # Nét ở vị trí 0 là chân chữ @, không phải gạch dưới.
        self.assertEqual(fix_underscores("@tam_400", [(0.4, 0.9), (4.5, 1.5)]), "@tam__400")
        self.assertEqual(fix_underscores("@tam_400", [(0.4, 0.9), (4.5, 0.8)]), "@tam_400")
        self.assertEqual(fix_underscores("@vantuyet._", [(0.3, 0.9), (1.6, 0.8), (10.2, 0.8)]), "@_vantuyet._")
        self.assertEqual(fix_underscores("@ydieu", [(0.3, 0.9), (1.5, 0.8)]), "@ydieu")
        self.assertEqual(fix_underscores("@lan", []), "@lan")

    def test_vote_on_handles_is_exact(self) -> None:
        reads = [("tamphuongca14899", 1), ("tamphuongcal4899", 1), ("tamphuongca14899", 1)]
        self.assertEqual(vote_chars(reads, loose=False), "tamphuongca14899")
        self.assertEqual(vote_chars([], loose=False), "")


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

    def test_the_tapped_row_joins_a_profile_with_another_name(self) -> None:
        screen = (("0900000001", "Nhà gỗ"), ("0900000002", "Quốc Thanh"), ("0900000003", "Kim Hoa"))
        result = build(
            [
                FrameRead("list", [Contact(p, n) for p, n in screen], tapped="0900000002"),
                profile("TV", "@televisionnn"),
                FrameRead("list", [Contact(p, n) for p, n in screen]),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000002", "@televisionnn")])
        self.assertEqual(len(result.unopened), 2)

    def test_two_profiles_claiming_one_tapped_row_are_not_joined(self) -> None:
        screen = (("0900000001", "Nhà gỗ"), ("0900000002", "Quốc Thanh"), ("0900000003", "Kim Hoa"))
        result = build(
            [
                FrameRead("list", [Contact(p, n) for p, n in screen], tapped="0900000002"),
                profile("TV", "@televisionnn"),
                FrameRead("list", [Contact(p, n) for p, n in screen], tapped="0900000002"),
                profile("Wood", "@woodhouse"),
            ]
        )
        self.assertEqual(result.rows, [])

    def test_a_tap_seen_before_scrolling_away_is_not_used(self) -> None:
        result = build(
            [
                FrameRead("list", [Contact("0900000001", "An"), Contact("0900000002", "Binh")], tapped="0900000001"),
                FrameRead("list", [Contact("0900000005", "Cuong"), Contact("0900000006", "Dung")]),
                profile("Biệt danh", "@xyzxyz"),
            ]
        )
        self.assertNotIn("0900000001", [row.phone for row in result.rows])

    def test_misread_handle_frames_of_one_visit_count_as_one_profile(self) -> None:
        screen = (("0900000001", "Nhà gỗ"), ("0900000002", "Quốc Thanh"))
        result = build(
            [
                listing(*screen),
                profile("Wood", "@woodhouse1"),
                profile("Wood", "@woodhousel"),
                profile("Wood", "@woodhouse1"),
                listing(*screen),
                profile("TV", "@televisionnn"),
            ]
        )
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [("0900000001", "@woodhouse1"), ("0900000002", "@televisionnn")],
        )
        self.assertEqual(result.review, [])

    def test_lookalike_handle_from_another_visit_merges_into_the_common_one(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan")),
                profile("Lan", "@lan_shop"),
                profile("Lan", "@lan_shop"),
                listing(("0900000001", "Lan")),
                profile("Lan", "@lan__shop"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000001", "@lan_shop")])
        self.assertEqual(result.review, [])

    def test_different_people_with_lookalike_handles_stay_apart(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan"), ("0900000002", "Linh")),
                profile("Lan", "@lan1"),
                profile("Lan", "@lan1"),
                listing(("0900000001", "Lan"), ("0900000002", "Linh")),
                profile("Linh", "@lanl"),
            ]
        )
        self.assertEqual(
            {(row.phone, row.handle) for row in result.rows},
            {("0900000001", "@lan1"), ("0900000002", "@lanl")},
        )

    def test_name_read_differently_on_each_frame_is_voted_by_character(self) -> None:
        frames = [
            listing(("0908035416", "Hữ Nhung")),
            listing(("0908035416", "Hữu Nhưng")),
            listing(("0908035416", "Hữu Nhung")),
            profile("Hữu Nhung", "@huunhung"),
        ]
        self.assertEqual(build(frames).rows[0].name, "Hữu Nhung")

    def test_most_frequent_name_wins(self) -> None:
        frames = [listing(("0908035416", "thovahanh"), ("0908031961", "Thu Vân")) for _ in range(3)]
        frames.append(listing(("0908035416", "Am _ Â 2m ls")))
        frames.append(profile("thovahanh", "@thova"))
        self.assertEqual(build(frames).rows[0].name, "thovahanh")


def list_layout(phones: list[str], tap_y: float = -1.0, clear: bool = True) -> Layout:
    line = TextLine("x", 0, 0, 10, 10)
    rows = [ListRow(phone, line, clear, 100 + 230 * index) for index, phone in enumerate(phones)]
    return Layout("list", rows=rows, tap_y=tap_y, row_pitch=230.0)


class PlanTests(unittest.TestCase):
    def test_each_number_is_read_in_at_most_three_clear_frames(self) -> None:
        layouts = [list_layout(["0900000001", "0900000002"]) for _ in range(10)]
        layouts[0] = list_layout(["0900000001", "0900000002"], clear=False)
        plan = plan_reads(layouts)
        reads = [index for index, rows in plan.items() if rows and 0 in rows]
        self.assertEqual(len(reads), 3)
        self.assertNotIn(0, reads)

    def test_each_profile_visit_is_read_in_two_frames(self) -> None:
        layouts = [list_layout(["0900000001", "0900000002"])]
        layouts += [Layout("profile", handle_line=TextLine("@a", 0, 0, 10, 10), handle_hint="@abc") for _ in range(4)]
        layouts += [list_layout(["0900000001", "0900000002"])]
        plan = plan_reads(layouts)
        self.assertEqual(sorted(index for index, rows in plan.items() if rows is None), [2, 4])

    def test_a_tap_that_erased_its_row_is_found_on_the_next_frame(self) -> None:
        phones = ["0900000001", "0900000002", "0900000003"]
        tapped = list_layout(["0900000001", "0900000003"], tap_y=100 + 230 + 60)
        tapped.rows[1] = ListRow("0900000003", tapped.rows[1].name_line, True, 100 + 460)
        layouts = [tapped, Layout("profile"), list_layout(phones)]
        resolve_taps(layouts)
        self.assertEqual(layouts[0].tapped, "0900000002")


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
