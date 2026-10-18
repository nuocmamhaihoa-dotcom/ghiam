"""Bộ đọc mới doc_video: chuẩn hoá chữ, ghép theo tên, và đọc thật trên ảnh mẫu."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from doc_video.frame import Contact, FrameRead
from doc_video.join import build
from doc_video import ocr
from doc_video.ocr import TextLine, _run, _run_cli
from doc_video.frame import Layout, ListRow
from doc_video.video import extract_midpoints, fast_scroll_spans, plan_reads, resolve_taps
from doc_video.text import (
    clean_name,
    find_handle,
    fix_underscores,
    handle_from_read,
    handle_from_viet,
    handle_matches_name,
    looks_like_phone,
    name_key,
    normalize_phone,
    vote_chars,
    vote_is_tied,
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
        self.assertEqual(find_handle("@31776814785"), "@31776814785")
        self.assertEqual(find_handle("@0749"), "")
        self.assertEqual(handle_from_read("(@thanhson190ó"), "@thanhson1906")
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

    def test_a_character_tie_is_flagged_without_changing_the_text(self) -> None:
        reads = [("An", 1.0), ("In", 1.0)]
        self.assertEqual(vote_is_tied(reads), ("An", True))
        self.assertEqual(vote_chars(reads), "An")
        majority = [("Hữu Nhung", 1.0), ("Hữ Nhung", 1.0), ("Hữu Nhung", 1.0)]
        text, tied = vote_is_tied(majority)
        self.assertEqual(text, vote_chars(majority))
        self.assertFalse(tied)
        self.assertFalse(vote_is_tied([("An", 2.0), ("In", 1.0)])[1])


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

    def test_profile_name_cut_short_joins_the_number_on_screen(self) -> None:
        result = build(
            [
                listing(("0919200084", "Phương Anh"), ("0900000002", "Lan")),
                profile("Phương An", "@anhphonglacuatao"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0919200084", "@anhphonglacuatao")])

    def test_name_read_one_letter_off_joins_the_number_on_screen(self) -> None:
        result = build(
            [
                listing(("0919325515", "LêLee"), ("0919225515", "Đức Khánh"), ("0900000003", "Mai")),
                profile("LêlLee", "@llee6745"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0919325515", "@llee6745")])

    def test_profile_joins_the_only_emoji_named_number_on_screen(self) -> None:
        result = build(
            [
                FrameRead("list", [Contact("0919253108", ""), Contact("0900000002", "Lan"), Contact("0900000003", "Mai")]),
                profile("AK", "@user520413055142"),
                FrameRead("list", [Contact("0919253108", ""), Contact("0900000002", "Lan"), Contact("0900000003", "Mai")]),
                profile("Lan", "@lan"),
            ]
        )
        self.assertIn(("0919253108", "@user520413055142"), [(row.phone, row.handle) for row in result.rows])

    def test_two_emoji_named_numbers_on_screen_are_not_guessed(self) -> None:
        result = build(
            [
                FrameRead("list", [Contact("0919253108", ""), Contact("0919208000", ""), Contact("0900000003", "Mai")]),
                profile("AK", "@user520413055142"),
            ]
        )
        self.assertEqual(result.rows, [])

    def test_similar_name_on_another_screen_is_not_attached(self) -> None:
        result = build(
            [
                listing(("0919325515", "LêLee"), ("0900000002", "Lan")),
                listing(("0900000005", "Cuong"), ("0900000006", "Dung"), ("0900000007", "Hoa")),
                profile("LêlLee", "@llee6745"),
            ]
        )
        self.assertEqual(result.rows, [])

    def test_short_name_inside_a_longer_one_is_not_attached(self) -> None:
        result = build([listing(("0900000001", "Kim Linh"), ("0900000002", "Lan")), profile("Linh", "@linh234_50")])
        self.assertEqual(result.rows, [])

    def test_similar_name_off_screen_is_not_attached(self) -> None:
        result = build(
            [
                listing(("0919200084", "Phương Anh"), ("0900000002", "Lan")),
                listing(("0900000005", "Cuong"), ("0900000006", "Dung")),
                profile("Phương An", "@anhphonglacuatao"),
            ]
        )
        self.assertEqual(result.rows, [])

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

    def test_a_tap_a_moment_before_the_profile_still_counts(self) -> None:
        screen = [Contact("0900000001", "Nhà gỗ"), Contact("0900000002", "Quốc Thanh"), Contact("0900000003", "Kim Hoa")]
        result = build(
            [
                FrameRead("list", screen, at=10.0, tapped="0900000002"),
                FrameRead("list", screen, at=10.5),
                FrameRead("list", screen, at=11.0),
                FrameRead("profile", profile_name="TV", handle="@televisionnn", at=11.5),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000002", "@televisionnn")])

    def test_an_old_tap_is_not_used(self) -> None:
        screen = [Contact("0900000001", "Nhà gỗ"), Contact("0900000002", "Quốc Thanh"), Contact("0900000003", "Kim Hoa")]
        result = build(
            [
                FrameRead("list", screen, at=10.0, tapped="0900000002"),
                FrameRead("list", screen, at=20.0),
                FrameRead("profile", profile_name="TV", handle="@televisionnn", at=20.5),
            ]
        )
        self.assertEqual(result.rows, [])

    def test_profile_name_inside_the_contact_name_joins(self) -> None:
        result = build(
            [
                listing(("0919285619", "Lê Trang Phan Thiết II"), ("0919337222", "Phan Thiết")),
                profile("Lê Trang Phan Thiết", "@thuy.trang07688"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0919285619", "@thuy.trang07688")])

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

    def test_number_misread_at_two_lookalike_digits_merges(self) -> None:
        frames = [listing(("0913327358", "A Hải"), ("0913363956", "Vựa Mực")) for _ in range(4)]
        frames.append(listing(("0915527558", "A Hải"), ("0913273681", "huythuc82")))
        frames.append(profile("A Hải", "@a.hi572"))
        result = build(frames)
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0913327358", "@a.hi572")])
        self.assertNotIn("0915527558", [phone for phone, _ in result.unopened])

    def test_rare_lookalike_misread_merges_even_with_a_misread_name(self) -> None:
        frames = [listing(("0913326577", "dy5bpm8kp8fh"), ("0913258209", "mmm")) for _ in range(8)]
        frames += [listing(("0915526577", "Uy IVI"), ("0913330677", "yamate")) for _ in range(2)]
        result = build(frames)
        phones = [phone for phone, _ in result.unopened]
        self.assertIn("0913326577", phones)
        self.assertNotIn("0915526577", phones)

    def test_often_seen_number_with_another_name_is_not_merged(self) -> None:
        frames = [listing(("0913326577", "Lan"), ("0913258209", "mmm")) for _ in range(8)]
        frames += [listing(("0915526577", "Hoa"), ("0913330677", "yamate")) for _ in range(6)]
        result = build(frames)
        phones = [phone for phone, _ in result.unopened]
        self.assertIn("0915526577", phones)

    def test_two_real_numbers_with_one_name_are_not_merged(self) -> None:
        frames = [listing(("0913327358", "Lan"), ("0915527558", "Lan"))]
        frames.append(profile("Lan", "@lan"))
        result = build(frames)
        self.assertEqual({item.reason for item in result.review}, {"nhiều số cùng tên"})

    def test_misread_number_merged_away_does_not_break_order_joining(self) -> None:
        screen = [("0915288126", "An"), ("0900000002", "Binh")]
        frames = [listing(*screen) for _ in range(3)]
        frames.append(listing(("0915288128", "An"), ("0900000002", "Binh")))
        frames.append(profile("Biệt danh", "@xyzxyz"))
        frames.append(listing(("0915288128", "An"), ("0900000002", "Binh")))
        frames.append(profile("Tên khác", "@abcabc"))
        result = build(frames)
        self.assertEqual({row.phone for row in result.rows}, {"0915288126", "0900000002"})

    def test_most_frequent_name_wins(self) -> None:
        frames = [listing(("0908035416", "thovahanh"), ("0908031961", "Thu Vân")) for _ in range(3)]
        frames.append(listing(("0908035416", "Am _ Â 2m ls")))
        frames.append(profile("thovahanh", "@thova"))
        self.assertEqual(build(frames).rows[0].name, "thovahanh")

    def test_a_tied_character_on_the_chosen_name_goes_to_review(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan")),
                profile("Lan", "@lan"),
                profile("Lin", "@lan"),
            ]
        )
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened, [])
        self.assertEqual([(item.phone, item.reason) for item in result.review], [("0900000001", "tên đọc chưa chắc")])

    def test_one_plain_misread_digit_goes_to_review(self) -> None:
        frames = [listing(("0919313090", "Truc")) for _ in range(3)]
        frames.append(listing(("0919313092", "Truc")))
        frames.append(profile("Truc", "@truc31798"))
        result = build(frames)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened, [])
        self.assertEqual([(item.phone, item.reason) for item in result.review], [("0919313090", "số đọc chưa chắc")])

    def test_one_plain_misread_digit_left_closed_goes_to_review(self) -> None:
        frames = [listing(("0919313090", "Truc"), ("0908031961", "Thu")) for _ in range(3)]
        frames.append(listing(("0919313092", "Truc")))
        result = build(frames)
        self.assertIn("0908031961", [phone for phone, _name in result.unopened])
        self.assertNotIn("0919313090", [phone for phone, _name in result.unopened])
        self.assertEqual(
            [(item.phone, item.reason) for item in result.review],
            [("0919313090", "số đọc chưa chắc")],
        )

    def test_a_tied_handle_in_one_visit_goes_to_review(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan")),
                profile("Lan", "@lan1"),
                profile("Lan", "@lanl"),
            ]
        )
        self.assertEqual(result.rows, [])
        self.assertIn("id đọc chưa chắc", [item.reason for item in result.review])


def list_layout(phones: list[str], tap_y: float = -1.0, clear: bool = True) -> Layout:
    line = TextLine("x", 0, 0, 10, 10)
    rows = [ListRow(phone, line, clear, 100 + 230 * index) for index, phone in enumerate(phones)]
    return Layout("list", rows=rows, tap_y=tap_y, row_pitch=230.0)


class PoolTests(unittest.TestCase):
    def test_a_frame_that_never_finishes_breaks_the_run_instead_of_hanging(self) -> None:
        import time
        from concurrent.futures import BrokenExecutor, ThreadPoolExecutor

        from doc_video.video import _run

        with ThreadPoolExecutor(1) as pool:
            with self.assertRaises(BrokenExecutor):
                _run(pool.submit, [(time.sleep, 0.5)], timeout=0.05)


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
        self.assertEqual(sorted(index for index, rows in plan.items() if rows is None), [1, 4])

    def test_profile_frames_that_show_the_handle_are_read_first(self) -> None:
        line = TextLine("@a", 0, 0, 10, 10)
        layouts = [list_layout(["0900000001", "0900000002"])]
        layouts.append(Layout("profile", handle_line=line, handle_hint=""))
        layouts += [Layout("profile", handle_line=line, handle_hint="@thanhson1906", clear=False) for _ in range(3)]
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

    def test_a_fast_flick_needs_two_rows_each_way(self) -> None:
        left = list_layout(["0900000001", "0900000002", "0900000003", "0900000004"])
        left.at = 0.0
        slow = list_layout(["0900000002", "0900000003", "0900000004", "0900000005"])
        slow.at = 0.5
        self.assertEqual(fast_scroll_spans([left, slow]), [])
        fast = list_layout(["0900000003", "0900000004", "0900000005", "0900000006"])
        fast.at = 0.5
        self.assertEqual(fast_scroll_spans([left, fast]), [(0.0, 0.5)])
        paused = list_layout(["0900000003", "0900000004", "0900000005", "0900000006"])
        paused.at = 1.0
        self.assertEqual(fast_scroll_spans([left, paused]), [])
        quick = list_layout(["0900000003", "0900000004", "0900000005", "0900000006"])
        quick.at = 0.1
        self.assertEqual(fast_scroll_spans([left, quick]), [])

    def test_a_profile_between_lists_is_not_a_flick(self) -> None:
        left = list_layout(["0900000001", "0900000002", "0900000003", "0900000004"])
        left.at = 0.0
        right = list_layout(["0900000003", "0900000004", "0900000005", "0900000006"])
        right.at = 0.5
        self.assertEqual(fast_scroll_spans([left, Layout("profile", at=0.25), right]), [])
        self.assertEqual(fast_scroll_spans([left, Layout("other", at=0.25), right]), [(0.0, 0.5)])

    def test_touching_fast_spans_merge(self) -> None:
        first = list_layout(["0900000001", "0900000002", "0900000003", "0900000004"])
        first.at = 0.0
        second = list_layout(["0900000003", "0900000004", "0900000005", "0900000006"])
        second.at = 0.5
        third = list_layout(["0900000005", "0900000006", "0900000007", "0900000008"])
        third.at = 1.0
        self.assertEqual(fast_scroll_spans([first, second, third]), [(0.0, 1.0)])

    @unittest.skipUnless(shutil.which("ffmpeg"), "không có ffmpeg")
    def test_one_midpoint_frame_is_taken_from_a_fast_span(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "in.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=white:s=32x32:d=2:r=30",
                    str(video),
                ],
                check=True,
            )
            found = extract_midpoints(video, Path(tmp), [(0.4, 1.1)])
            self.assertEqual(len(found), 1)
            self.assertAlmostEqual(found[0][1], 0.75, delta=0.05)
            self.assertEqual(extract_midpoints(Path(tmp) / "missing.mp4", Path(tmp), [(0.0, 1.0)]), [])


class EngineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tesseract"), "không có tesseract")
    def test_loaded_engine_reads_the_same_words_as_the_command(self) -> None:
        image = Image.new("L", (360, 80), 255)
        ImageDraw.Draw(image).text((12, 24), "0901234567", fill=0)
        loaded = [part[11] for part in _run([image], "eng", 7, "", False)[0]]
        command = [part[11] for part in _run_cli([image], "eng", 7, "", False)[0]]
        self.assertEqual(loaded, command)
        self.assertEqual(loaded, ["0901234567"])

    def test_command_decode_is_utf8(self) -> None:
        image = Image.new("L", (10, 10), 255)
        with patch("doc_video.ocr.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            _run_cli([image], "eng", 7, "", False)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    def test_windows_does_not_load_a_tesseract_dll(self) -> None:
        ocr._ENGINE.pop("lib", None)
        try:
            with patch("doc_video.ocr.os.name", "nt"):
                self.assertIsNone(ocr._library())
        finally:
            ocr._ENGINE.pop("lib", None)


class EarlyReadTests(unittest.TestCase):
    def test_agreed_names_skip_the_plain_reader_and_vietocr(self) -> None:
        from doc_video.frame import _read_names

        images = [Image.new("L", (8, 8), 255), Image.new("L", (8, 8), 255)]

        def fake(batch, lang, whitelist="", best=True):
            self.assertEqual(lang, "vie")
            return ["Lan", "Mai"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake) as read,
            patch("doc_video.frame.viet.available", return_value=True),
            patch("doc_video.frame.viet.read_lines") as viet_lines,
        ):
            self.assertEqual(_read_names(images, images), ["Lan", "Mai"])
        self.assertEqual(read.call_count, 2)
        viet_lines.assert_not_called()

    def test_a_mark_difference_still_reads_vietocr(self) -> None:
        from doc_video.frame import _read_names

        images = [Image.new("L", (8, 8), 255)]
        seen: list[str] = []

        def fake(batch, lang, whitelist="", best=True):
            seen.append(lang)
            if lang == "vie" and best:
                return ["Hữu"]
            if lang == "vie":
                return ["Huu"]
            return ["Huu"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake),
            patch("doc_video.frame.viet.available", return_value=True),
            patch("doc_video.frame.viet.read_lines", return_value=["Hữu"]) as viet_lines,
        ):
            self.assertEqual(_read_names(images, images), ["Hữu"])
        self.assertIn("eng", seen)
        viet_lines.assert_called_once()

    def test_an_empty_agreement_does_not_skip(self) -> None:
        from doc_video.frame import _read_names

        images = [Image.new("L", (8, 8), 255)]
        seen: list[str] = []

        def fake(batch, lang, whitelist="", best=True):
            seen.append(lang)
            return [""]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake),
            patch("doc_video.frame.viet.available", return_value=False),
        ):
            self.assertEqual(_read_names(images, images), [""])
        self.assertIn("eng", seen)

    def test_an_agreed_handle_skips_the_other_crops_and_still_fixes_underscores(self) -> None:
        from doc_video.frame import _read_handle

        line = TextLine("@lanshop", 4, 4, 90, 28)
        screen = Image.new("RGB", (120, 48), (255, 255, 255))

        def fake(batch, lang, whitelist="", best=True):
            self.assertEqual((lang, best), ("vie", False))
            return ["@lanshop"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake) as read,
            patch("doc_video.frame.viet.available", return_value=True),
            patch("doc_video.frame.viet.read_lines") as viet_lines,
            patch("doc_video.frame._underscore_strokes", return_value=[(1.2, 0.9)]),
        ):
            self.assertEqual(_read_handle(screen, line), "@_lanshop")
        self.assertEqual(read.call_count, 1)
        viet_lines.assert_not_called()

    def test_an_agreed_handle_with_a_digit_still_reads_the_english_crops(self) -> None:
        from doc_video.frame import _read_handle

        line = TextLine("@truc51798", 4, 4, 90, 28)
        screen = Image.new("RGB", (120, 48), (255, 255, 255))
        calls: list[tuple[str, bool]] = []

        def fake(batch, lang, whitelist="", best=True):
            calls.append((lang, best))
            return ["@truc51798"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake),
            patch("doc_video.frame.viet.available", return_value=False),
        ):
            _read_handle(screen, line)
        self.assertEqual(len(calls), 5)
        self.assertIn(("eng", True), calls)

    def test_a_different_handle_still_reads_the_other_crops(self) -> None:
        from doc_video.frame import _read_handle

        line = TextLine("@lanshop", 4, 4, 90, 28)
        screen = Image.new("RGB", (120, 48), (255, 255, 255))
        calls: list[tuple[str, bool]] = []

        def fake(batch, lang, whitelist="", best=True):
            calls.append((lang, best))
            return ["@lan_sh0p"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake),
            patch("doc_video.frame.viet.available", return_value=False),
        ):
            _read_handle(screen, line)
        self.assertEqual(len(calls), 5)

    def test_an_empty_scan_handle_does_not_stop_early(self) -> None:
        from doc_video.frame import _read_handle

        line = TextLine("", 4, 4, 90, 28)
        screen = Image.new("RGB", (120, 48), (255, 255, 255))
        calls: list[tuple[str, bool]] = []

        def fake(batch, lang, whitelist="", best=True):
            calls.append((lang, best))
            return ["@lanshop"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=fake),
            patch("doc_video.frame.viet.available", return_value=False),
        ):
            _read_handle(screen, line)
        self.assertEqual(len(calls), 5)

    def test_the_digit_vote_breaks_a_tie_and_does_not_override_agreement(self) -> None:
        from doc_video.frame import _read_phones

        screen = Image.new("RGB", (80, 60), (255, 255, 255))
        name = TextLine("Lan", 2, 24, 40, 40)

        def disagree(batch, lang, whitelist="", best=True):
            return ["0912345678" if best else "0912345679"]

        phone = TextLine("không phải số", 2, 2, 70, 20)
        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=disagree),
            patch("doc_video.frame.read_digit_line", return_value="0912345679"),
        ):
            self.assertEqual(_read_phones(screen, [(phone, name)]), ["0912345679"])

        def agree(batch, lang, whitelist="", best=True):
            return ["0912345678"]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=agree),
            patch("doc_video.frame.read_digit_line", return_value="0998765432"),
        ):
            self.assertEqual(_read_phones(screen, [(phone, name)]), ["0912345678"])

        blank = TextLine("", 2, 2, 70, 20)

        def empty(batch, lang, whitelist="", best=True):
            return [""]

        with (
            patch("doc_video.frame.ocr.read_texts", side_effect=empty),
            patch("doc_video.frame.read_digit_line", return_value="0912345678"),
        ):
            self.assertEqual(_read_phones(screen, [(blank, name)]), ["0912345678"])


class DigitTests(unittest.TestCase):
    def test_three_fonts_read_every_digit(self) -> None:
        from PIL import ImageDraw, ImageFont

        from doc_video.digits import read_digit_line

        fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        for path in fonts:
            self.assertTrue(Path(path).is_file(), path)
            font = ImageFont.truetype(path, 80)
            for text in ("0123456789", "0912345678"):
                box = ImageDraw.Draw(Image.new("L", (10, 10), 255)).textbbox((0, 0), text, font=font)
                image = Image.new("L", (box[2] - box[0] + 48, box[3] - box[1] + 48), 255)
                ImageDraw.Draw(image).text((24 - box[0], 24 - box[1]), text, fill=0, font=font)
                self.assertEqual(read_digit_line(image), text)
            letters = Image.new("L", (200, 80), 255)
            ImageDraw.Draw(letters).text((8, 8), "abcdefghij", fill=0, font=font)
            self.assertEqual(read_digit_line(letters), "")


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
