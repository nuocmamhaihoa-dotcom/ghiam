"""Bộ đọc mới doc_video: chuẩn hoá chữ, ghép theo tên, và đọc thật trên ảnh mẫu."""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

import numpy as np

from doc_video.frame import (
    Contact,
    FrameRead,
    Layout,
    ListRow,
    _list_pairs,
    _supplement_pairs,
    _tap_center,
    promote_unclassified_profile,
    row_at,
)
from doc_video.join import Result, Review, Row, add_full_name_rows, add_side_rows, build
from doc_video.ocr import TextLine
from doc_video.side import select_left_profile, select_right_pairs
from doc_video.video import plan_full_reads, plan_reads, resolve_taps, side_frames
from doc_video.text import (
    clean_name,
    find_handle,
    fix_underscores,
    handle_from_read,
    handle_from_viet,
    handle_matches_name,
    is_name,
    subtitle_handle,
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
        self.assertEqual(find_handle("@31776814785"), "@31776814785")
        self.assertEqual(find_handle("@0749"), "")
        self.assertEqual(handle_from_read("(@thanhson190ó"), "@thanhson1906")
        self.assertEqual(handle_from_read("@I.th.li078"), "@l.th.li078")
        self.assertEqual(handle_from_read("@hng.In6732"), "@hng.ln6732")
        self.assertEqual(handle_from_read("@mSg6vpbq6"), "@m5g6vpbq6")
        self.assertEqual(handle_from_read("@xB2k"), "@x82k")
        self.assertEqual(handle_from_viet("Oquocnguyet81792"), "@quocnguyet81792")
        self.assertEqual(handle_from_viet("Qhuong.tv"), "@huong.tv")
        self.assertEqual(handle_from_viet("0090fy5xr"), "@090fy5xr")
        self.assertEqual(handle_from_viet("@Om5g6vpbqó"), "@m5g6vpbq6")

    def test_a_printed_username_is_an_id_and_a_plain_name_is_not(self) -> None:
        self.assertEqual(subtitle_handle("user896273910"), "@user896273910")
        self.assertEqual(subtitle_handle("camtruong1954"), "@camtruong1954")
        self.assertEqual(subtitle_handle("@660"), "@660")
        self.assertEqual(subtitle_handle("teehairsalon"), "")
        self.assertEqual(subtitle_handle("Kem"), "")
        self.assertEqual(subtitle_handle("Lan 2"), "")
        self.assertEqual(subtitle_handle("123456"), "")
        self.assertEqual(subtitle_handle("user89627391ó"), "@user896273916")
        self.assertEqual(subtitle_handle("camtruong1954!"), "@camtruong1954")
        self.assertEqual(subtitle_handle("Binh"), "")

    def test_names_keep_special_characters_except_bar(self) -> None:
        self.assertEqual(clean_name("Huyền Phương (Cà)"), "Huyền Phương (Cà)")
        self.assertEqual(clean_name("Lan & Hoa"), "Lan & Hoa")
        self.assertEqual(clean_name("Thắm | Shop"), "Thắm Shop")
        self.assertTrue(is_name("Ý"))
        self.assertFalse(is_name("A"))

    def test_vote_keeps_what_most_readers_agree_on(self) -> None:
        reads = [("Hữu Nhung", 1.5), ("Hữ Nhung", 1.0), ("Hữu Nhung", 1.0), ("Huu Nhung", 0.7)]
        self.assertEqual(vote_chars(reads), "Hữu Nhung")
        reads = [("Ngô Lan", 1.5), ("Ng6 Lan", 1.0), ("Ngô Lan", 1.0), ("Ngo Lan", 0.7)]
        self.assertEqual(vote_chars(reads), "Ngô Lan")
        reads = [("Ngô Lan", 1.5), ("Ngõ Lan", 1.0), ("Ngõ Lan", 1.0), ("Ngo Lan", 0.7)]
        self.assertEqual(vote_chars(reads), "Ngô Lan")
        reads = [("Hải Yến", 1.5), ("Hài Yến", 1.0), ("Hài Yến", 1.0), ("Hai Yen", 0.7)]
        self.assertEqual(vote_chars(reads), "Hải Yến")
        reads = [("Định Khánh Hải", 1.5), ("Đình Khánh Hải", 1.0), ("Đỉnh Khánh Hải", 1.0), ("Dinh Khanh Hai", 0.7)]
        self.assertEqual(vote_chars(reads), "Đinh Khánh Hải")
        reads = [("nhungphuong 8712", 1.5), ("nhungphuong8712", 1.0), ("nhungphuong8712", 1.0), ("nhungphuong8712", 0.7)]
        self.assertEqual(vote_chars(reads), "nhungphuong8712")
        reads = [("_thoa.xuan_", 0.7), ("thoa.xuan", 1.5), ("_thoa.xuan_", 1.0), ("_thoa.xuan_", 1.0)]
        self.assertEqual(vote_chars(reads), "_thoa.xuan_")

    def test_list_pairs_merges_split_pieces_of_a_name_row(self) -> None:
        lines = [
            TextLine("0813663062", 273, 1817, 502, 1844, 27),
            TextLine("|", 274, 1861, 276, 1891, 30),
            TextLine("Trinh", 330, 1863, 413, 1889, 26),
        ]
        pairs = _list_pairs(lines)
        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0][1].x0, pairs[0][1].x1), (273, 413))
        self.assertTrue(is_name(clean_name(pairs[0][1].text)))

    def test_a_wide_name_box_under_the_phone_is_kept_when_the_rough_text_is_not_a_name(self) -> None:
        phone = TextLine("0912345678", 80, 400, 360, 440, 28)
        name = TextLine("x", 80, 450, 280, 490, 28)
        kept_phone = TextLine("0900000001", 80, 520, 360, 560, 28)
        kept_name = TextLine("Lan", 80, 570, 200, 610, 28)
        found = _supplement_pairs([(kept_phone, kept_name)], [(phone, name), (kept_phone, kept_name)], set(), None)
        self.assertEqual([(item[0].text, item[2]) for item in found], [("0912345678", "")])

    def test_a_complete_number_just_under_the_title_is_kept(self) -> None:
        title = TextLine("Danh bạ", 40, 20, 220, 70, 36)
        phone = TextLine("0987654321", 80, 80, 360, 116, 28)
        name = TextLine("Mai", 80, 124, 180, 160, 28)
        found = _supplement_pairs([], [(phone, name)], set(), title)
        self.assertEqual([(item[0].text, item[2]) for item in found], [("0987654321", "0987654321")])

    def test_a_number_hidden_under_the_title_stays_out(self) -> None:
        title = TextLine("Danh bạ", 40, 20, 220, 90, 40)
        phone = TextLine("0987654321", 80, 70, 360, 106, 28)
        name = TextLine("Mai", 80, 114, 180, 150, 28)
        self.assertEqual(_supplement_pairs([], [(phone, name)], set(), title), [])

    def test_a_button_or_a_thin_mark_under_the_phone_is_not_a_name(self) -> None:
        follow = TextLine("0912345678", 80, 400, 360, 440, 28)
        follow_name = TextLine("Follow", 80, 450, 240, 490, 28)
        mark = TextLine("0900000002", 80, 520, 360, 560, 28)
        mark_name = TextLine("|", 80, 570, 84, 600, 28)
        self.assertEqual(
            _supplement_pairs([], [(follow, follow_name), (mark, mark_name)], set(), None),
            [],
        )

    def test_a_number_already_scanned_is_not_added_again(self) -> None:
        phone = TextLine("0912345678", 80, 80, 360, 116, 28)
        name = TextLine("Mai", 80, 124, 180, 160, 28)
        title = TextLine("Danh bạ", 40, 20, 220, 70, 36)
        self.assertEqual(_supplement_pairs([], [(phone, name)], {"0912345678"}, title), [])

    def test_underscore_strokes_fix_double_and_leading_underscores(self) -> None:
        # Nét ở vị trí 0 là chân chữ @, không phải gạch dưới.
        self.assertEqual(fix_underscores("@tam_400", [(0.4, 0.9), (4.5, 1.5)]), "@tam__400")
        self.assertEqual(fix_underscores("@tam_400", [(0.4, 0.9), (4.1, 0.72), (5.0, 0.72)]), "@tam__400")
        self.assertEqual(fix_underscores("@tam_400", [(0.4, 0.9), (4.5, 0.8)]), "@tam_400")
        self.assertEqual(fix_underscores("@vantuyet._", [(0.3, 0.9), (1.6, 0.8), (10.2, 0.8)]), "@_vantuyet._")
        self.assertEqual(fix_underscores("@ydieu", [(0.3, 0.9), (1.5, 0.8)]), "@ydieu")
        self.assertEqual(fix_underscores("@lan", []), "@lan")

    def test_vote_on_handles_is_exact(self) -> None:
        reads = [("tamphuongca14899", 1), ("tamphuongcal4899", 1), ("tamphuongca14899", 1)]
        self.assertEqual(vote_chars(reads, loose=False), "tamphuongca14899")
        reads = [
            ("sunr0o971xra", 1.0, 0.6),
            ("sunr0o971xra", 1.0, 0.6),
            ("sunr0o971xra", 1.0, 0.6),
            ("sunr00971xra", 1.0, 1.0),
            ("sunr00971xra", 1.0, 1.0),
            ("sunr0o0971xra", 1.0, 1.0),
            ("sunr00971xra", 1.0, 1.0),
        ]
        self.assertEqual(vote_chars(reads, loose=False), "sunr0o971xra")
        reads = [
            ("umrv96cp5ws", 1.0, 0.6),
            ("umrv96cp5ws", 1.0, 0.6),
            ("umrv96cp5ws", 1.0, 0.6),
            ("iumrv96cp5ws", 1.0, 1.0),
            ("iumrv96cp5ws", 1.0, 1.0),
            ("lumrvo6cp5ws", 1.0, 1.0),
            ("lumrv96cp5ws", 1.0, 1.0),
        ]
        self.assertEqual(len(vote_chars(reads, loose=False)), 12)
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

    def test_rows_left_after_a_name_match_still_join_in_order(self) -> None:
        screen = (
            ("0900000001", "Nha Go"),
            ("0900000002", "Quoc Thanh"),
            ("0900000003", "Kim Hoa"),
            ("0900000004", "Mai Lan"),
        )
        result = build(
            [
                listing(*screen),
                profile("Nha Go", "@nhago"),
                listing(*screen),
                profile("Ten TikTok", "@televisionnn"),
                listing(*screen),
                profile("Ten Khac", "@shopvinh"),
                listing(*screen),
                profile("Mai Lan", "@mailan"),
            ]
        )
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [
                ("0900000001", "@nhago"),
                ("0900000002", "@televisionnn"),
                ("0900000003", "@shopvinh"),
                ("0900000004", "@mailan"),
            ],
        )
        self.assertEqual(result.unopened, [])
        self.assertEqual(result.review, [])

    def test_a_full_screen_keeps_open_order_when_a_profile_name_matches_another_row(self) -> None:
        screen = (("0900000001", "Lan"), ("0900000002", "Hoa"), ("0900000003", "Mai"))
        result = build(
            [
                listing(*screen),
                profile("Hoa", "@cua.lan"),
                listing(*screen),
                profile("TikTok", "@khac.mot"),
                listing(*screen),
                profile("TikTok Hai", "@khac.hai"),
            ]
        )
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [("0900000001", "@cua.lan"), ("0900000002", "@khac.mot"), ("0900000003", "@khac.hai")],
        )
        self.assertEqual(result.unopened, [])
        self.assertEqual(result.review, [])

    def test_a_partial_screen_keeps_the_name_match_and_leaves_the_rest(self) -> None:
        screen = (("0900000001", "Lan"), ("0900000002", "Hoa"), ("0900000003", "Mai"))
        result = build([listing(*screen), profile("Hoa", "@cua.lan")])
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000002", "@cua.lan")])
        self.assertEqual(sorted(phone for phone, _ in result.unopened), ["0900000001", "0900000003"])

    def test_an_extra_pair_joins_by_name_and_leaves_the_row_order_alone(self) -> None:
        screen = [
            Contact("0900000001", "Nhà gỗ"),
            Contact("0900000002", "Quốc Thanh"),
            Contact("0900000003", "Kim Hoa"),
            Contact("0912345678", "Truc", False),
        ]
        result = build(
            [
                FrameRead("list", screen),
                profile("Wood", "@woodhouse"),
                FrameRead("list", screen),
                profile("TV", "@televisionnn"),
                FrameRead("list", screen),
                profile("Vinh", "@shopvinh"),
                FrameRead("list", screen),
                profile("Truc", "@truc31798"),
            ]
        )
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [
                ("0900000001", "@woodhouse"),
                ("0900000002", "@televisionnn"),
                ("0900000003", "@shopvinh"),
                ("0912345678", "@truc31798"),
            ],
        )
        self.assertEqual(result.unopened, [])
        self.assertEqual(result.review, [])

    def test_an_extra_pair_with_a_name_already_in_order_is_ignored(self) -> None:
        screen = [
            Contact("0900000001", "Lan"),
            Contact("0900000002", "Hoa"),
            Contact("0911111111", "Lan", False),
        ]
        result = build([FrameRead("list", screen), profile("Lan", "@lan")])
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000001", "@lan")])
        self.assertEqual(result.unopened, [("0900000002", "Hoa")])
        self.assertEqual(result.review, [])

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

    def test_taps_pair_the_middle_without_shifting_the_ends(self) -> None:
        screen = (
            ("0900000001", "An"),
            ("0900000002", "Binh"),
            ("0900000003", "Chi"),
            ("0900000004", "Dung"),
            ("0900000005", "Em"),
        )
        contacts = [Contact(phone, name) for phone, name in screen]
        result = build(
            [
                FrameRead("list", contacts, tapped="0900000002"),
                profile("Tên khác", "@bbbbxxxx"),
                FrameRead("list", contacts),
                profile("Tên khác", "@ccccxxxx"),
                FrameRead("list", contacts, tapped="0900000004"),
                profile("Tên khác", "@ddddxxxx"),
            ]
        )
        self.assertEqual(
            {(row.phone, row.handle) for row in result.rows},
            {
                ("0900000002", "@bbbbxxxx"),
                ("0900000003", "@ccccxxxx"),
                ("0900000004", "@ddddxxxx"),
            },
        )
        self.assertEqual([phone for phone, _ in result.unopened], ["0900000001", "0900000005"])

    def test_a_gap_between_taps_is_not_shifted(self) -> None:
        screen = (
            ("0900000001", "An"),
            ("0900000002", "Binh"),
            ("0900000003", "Chi"),
            ("0900000004", "Dung"),
        )
        contacts = [Contact(phone, name) for phone, name in screen]
        result = build(
            [
                FrameRead("list", contacts, tapped="0900000001"),
                profile("Tên khác", "@aaaaxxxx"),
                FrameRead("list", contacts),
                profile("Tên khác", "@xxxxgiua"),
                FrameRead("list", contacts, tapped="0900000004"),
                profile("Tên khác", "@ddddxxxx"),
            ]
        )
        self.assertEqual(
            {(row.phone, row.handle) for row in result.rows},
            {("0900000001", "@aaaaxxxx"), ("0900000004", "@ddddxxxx")},
        )
        self.assertIn("0900000002", [phone for phone, _ in result.unopened])
        self.assertIn("0900000003", [phone for phone, _ in result.unopened])

    def test_a_missed_profile_read_still_joins_in_order(self) -> None:
        screen = (("0900000001", "Nhà gỗ"), ("0900000002", "Quốc Thanh"))
        result = build(
            [
                listing(*screen),
                FrameRead("other", full_handle="@woodhouse", full_profile_name="Wood"),
                listing(*screen),
                FrameRead("other", full_handle="@televisionnn", full_profile_name=""),
            ]
        )
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [("0900000001", "@woodhouse"), ("0900000002", "@televisionnn")],
        )
        self.assertEqual(result.unopened, [])

    def test_a_recovered_handle_does_not_replace_one_already_read(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan"), ("0900000002", "Hoa")),
                profile("Lan", "@lan.shop"),
                FrameRead("other", full_handle="@khacxxxx"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000001", "@lan.shop")])
        self.assertEqual([phone for phone, _ in result.unopened], ["0900000002"])

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
        self.assertIn(("0913327358", "@a.hi572"), [(row.phone, row.handle) for row in result.rows])
        self.assertIn(("0913273681", "@huythuc82"), [(row.phone, row.handle) for row in result.rows])
        self.assertNotIn("0915527558", [phone for phone, _ in result.unopened])
        self.assertNotIn("0915527558", [row.phone for row in result.rows])

    def test_rare_lookalike_misread_merges_even_with_a_misread_name(self) -> None:
        frames = [listing(("0913326577", "dy5bpm8kp8fh"), ("0913258209", "mmm")) for _ in range(8)]
        frames += [listing(("0915526577", "Uy IVI"), ("0913330677", "yamate")) for _ in range(2)]
        result = build(frames)
        seen = {phone for phone, _ in result.unopened} | {row.phone for row in result.rows}
        self.assertIn("0913326577", seen)
        self.assertNotIn("0915526577", seen)

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


def _disc(mask: np.ndarray, cx: int, cy: int, radius: int) -> None:
    height, width = mask.shape
    y, x = np.ogrid[:height, :width]
    mask[(y - cy) ** 2 + (x - cx) ** 2 <= radius * radius] = True


def _line_pair(top: int) -> tuple[TextLine, TextLine]:
    return (TextLine("0900000000", 200, top, 500, top + 40), TextLine("Lan", 200, top + 50, 400, top + 90))


def list_layout(phones: list[str], tap_y: float = -1.0, clear: bool = True) -> Layout:
    line = TextLine("x", 0, 0, 10, 10)
    rows = [ListRow(phone, line, clear, 100 + 230 * index) for index, phone in enumerate(phones)]
    return Layout("list", rows=rows, tap_y=tap_y, row_pitch=230.0)


class ProfileHintTests(unittest.TestCase):
    def test_an_unclassified_frame_with_a_left_handle_becomes_a_profile(self) -> None:
        handle = TextLine("@woodhouse", 40, 400, 280, 440, 32)
        layout = Layout(side_handle_line=handle, side_name_line=TextLine("Wood", 40, 340, 200, 380, 32))
        promote_unclassified_profile(layout)
        self.assertEqual(layout.kind, "profile")
        self.assertEqual(layout.handle_hint, "@woodhouse")
        self.assertIs(layout.handle_line, handle)

    def test_a_contact_list_is_not_turned_into_a_profile(self) -> None:
        layout = Layout(
            "list",
            side_handle_line=TextLine("@woodhouse", 40, 400, 280, 440, 32),
        )
        promote_unclassified_profile(layout)
        self.assertEqual(layout.kind, "list")

    def test_a_plain_word_on_the_left_is_not_a_profile(self) -> None:
        layout = Layout(side_handle_line=TextLine("Kem", 40, 400, 120, 440, 32))
        promote_unclassified_profile(layout)
        self.assertEqual(layout.kind, "other")

    def test_a_missed_handle_keeps_the_one_seen_while_scanning(self) -> None:
        from doc_video.video import _fill_missing_profile_handles

        layouts = [
            Layout("list", at=0),
            Layout("profile", handle_hint="@nhago", at=1),
            Layout("profile", handle_hint="@nhago", at=2),
        ]
        done = {
            1: FrameRead("other", at=1),
            2: FrameRead("profile", profile_name="Nha Go", handle="", at=2),
        }
        _fill_missing_profile_handles(layouts, done)
        self.assertEqual(done[2].kind, "profile")
        self.assertEqual(done[2].handle, "@nhago")
        self.assertEqual(done[2].profile_name, "Nha Go")
        self.assertEqual(done[1].handle, "")

    def test_a_careful_handle_is_not_replaced_by_the_scan_hint(self) -> None:
        from doc_video.video import _fill_missing_profile_handles

        layouts = [
            Layout("profile", handle_hint="@khac", at=1),
            Layout("profile", handle_hint="@khac", at=2),
        ]
        done = {1: FrameRead("profile", profile_name="Nha", handle="@nhago", at=1)}
        _fill_missing_profile_handles(layouts, done)
        self.assertEqual(done[1].handle, "@nhago")
        self.assertNotIn(2, done)


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

    def test_back_button_and_avatar_do_not_hide_the_follow_ripple(self) -> None:
        mask = np.zeros((2000, 1000), dtype=bool)
        _disc(mask, 80, 70, 40)
        _disc(mask, 70, 500, 36)
        _disc(mask, 880, 520, 48)
        _disc(mask, 480, 900, 40)
        pairs = [_line_pair(400), _line_pair(630), _line_pair(860)]
        self.assertAlmostEqual(_tap_center(mask, pairs, 230), 520, delta=1)

    def test_one_tap_on_the_name_still_counts_when_the_back_button_is_grey(self) -> None:
        mask = np.zeros((2000, 1000), dtype=bool)
        _disc(mask, 80, 70, 40)
        _disc(mask, 420, 650, 46)
        pairs = [_line_pair(400), _line_pair(630), _line_pair(860)]
        self.assertAlmostEqual(_tap_center(mask, pairs, 230), 650, delta=1)

    def test_a_bitten_follow_circle_is_still_one_tap(self) -> None:
        mask = np.zeros((2000, 1000), dtype=bool)
        _disc(mask, 80, 500, 36)
        _disc(mask, 860, 520, 50)
        mask[470:580, 870:940] = False
        pairs = [_line_pair(400), _line_pair(630), _line_pair(860)]
        self.assertAlmostEqual(_tap_center(mask, pairs, 230), 520, delta=20)

    def test_two_follow_ripples_are_not_a_tap(self) -> None:
        mask = np.zeros((2000, 1000), dtype=bool)
        _disc(mask, 860, 450, 40)
        _disc(mask, 880, 900, 40)
        pairs = [_line_pair(400), _line_pair(630), _line_pair(860)]
        self.assertEqual(_tap_center(mask, pairs, 230), -1)

    def test_a_printed_username_fills_only_the_unopened_phone(self) -> None:
        result = build(
            [
                listing(("0900000001", "Lan"), ("0900000002", "user896273910")),
                profile("Lan", "@lan.shop"),
            ]
        )
        self.assertEqual(
            {(row.phone, row.handle) for row in result.rows},
            {("0900000001", "@lan.shop"), ("0900000002", "@user896273910")},
        )

    def test_a_plain_name_stays_unopened(self) -> None:
        result = build([listing(("0900000001", "teehairsalon"), ("0900000002", "Kem"))])
        self.assertEqual(result.rows, [])
        self.assertEqual([phone for phone, _ in result.unopened], ["0900000001", "0900000002"])

    def test_a_printed_username_does_not_take_an_id_already_paired(self) -> None:
        result = build(
            [
                listing(("0900000001", "user896273910"), ("0900000002", "Lan")),
                profile("Lan", "@user896273910"),
            ]
        )
        self.assertEqual([(row.phone, row.handle) for row in result.rows], [("0900000002", "@user896273910")])
        self.assertIn("0900000001", [phone for phone, _ in result.unopened])

    def test_two_phones_printed_with_the_same_username_stay_unopened(self) -> None:
        result = build([listing(("0900000001", "user111222"), ("0900000002", "user111222"))])
        self.assertEqual(result.rows, [])

    def test_a_full_screen_order_is_not_rewritten_by_a_printed_username(self) -> None:
        screen = (("0900000001", "user111222"), ("0900000002", "Lan"))
        result = build([listing(*screen), profile("Khác", "@mot"), listing(*screen), profile("Khác nữa", "@hai")])
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [("0900000001", "@mot"), ("0900000002", "@hai")],
        )

    def test_the_full_read_covers_rows_the_first_pass_skipped(self) -> None:
        layouts = [list_layout(["0900000001", "0900000002"])]
        full = plan_full_reads(layouts, {0: [0]})
        self.assertEqual(full, {0: [1]})

    def test_the_full_read_covers_profile_frames_the_first_pass_skipped(self) -> None:
        line = TextLine("@a", 0, 0, 10, 10)
        layouts = [list_layout(["0900000001"])]
        layouts += [
            Layout("profile", handle_line=line, handle_hint="@abc", clear=True, name_readable=True) for _ in range(4)
        ]
        layouts += [list_layout(["0900000001"])]
        plan = plan_reads(layouts)
        full = plan_full_reads(layouts, plan)
        planned = {index for index, rows in plan.items() if rows is None}
        extra = {index for index, rows in full.items() if rows is None}
        self.assertEqual(len(planned), 2)
        self.assertTrue(planned.isdisjoint(extra))
        self.assertEqual(len(planned | extra), 4)

    def test_a_tap_does_not_land_on_an_extra_pair(self) -> None:
        line = TextLine("x", 0, 0, 10, 10)
        rows = [
            ListRow("0912345678", line, True, 100, in_order=False),
            ListRow("0900000002", line, True, 330),
        ]
        self.assertEqual(row_at(rows, 120, 230), "")
        self.assertEqual(row_at(rows, 350, 230), "0900000002")


WIDTH = 1000
HEIGHT = 2000


def _right_pair(x: int, y: int, phone: str = "0900000001", name: str = "Lan") -> tuple[TextLine, TextLine]:
    return (
        TextLine(phone, x, y, x + 300, y + 40, 40),
        TextLine(name, x, y + 50, x + 200, y + 90, 40),
    )


class FullNameStreamTests(unittest.TestCase):
    def test_the_same_name_across_the_whole_video_joins_one_pair(self) -> None:
        frames = [
            FrameRead(
                "list",
                [Contact("0900000001", "")],
                full_contacts=[Contact("0900000001", "Lan", False)],
            ),
            FrameRead("profile", full_profile_name="Lan", full_handle="@lan.shop"),
        ]
        result = add_full_name_rows(build(frames), frames)
        self.assertEqual(result.rows, [Row("0900000001", "Lan", "@lan.shop")])
        self.assertEqual(result.unopened, [])

    def test_a_saved_row_keeps_its_phone_and_id(self) -> None:
        screen = (("0900000001", "Lan"), ("0900000002", "Hoa"))
        frames = [
            listing(*screen),
            profile("Khác", "@mot"),
            listing(*screen),
            profile("Khác nữa", "@hai"),
        ]
        frames[0].full_contacts = [Contact("0900000001", "Lan", False)]
        frames[1].full_profile_name = "Lan"
        frames[1].full_handle = "@khac"
        result = add_full_name_rows(build(frames), frames)
        self.assertEqual(
            [(row.phone, row.handle) for row in result.rows],
            [("0900000001", "@mot"), ("0900000002", "@hai")],
        )

    def test_two_phones_with_the_same_full_name_stay_apart(self) -> None:
        result = Result(unopened=[("0900000001", ""), ("0900000002", "")])
        frames = [
            FrameRead(
                full_contacts=[
                    Contact("0900000001", "Lan", False),
                    Contact("0900000002", "Lan", False),
                ]
            ),
            FrameRead(full_profile_name="Lan", full_handle="@lan"),
        ]
        add_full_name_rows(result, frames)
        self.assertEqual(result.rows, [])
        self.assertEqual([phone for phone, _ in result.unopened], ["0900000001", "0900000002"])

    def test_different_full_names_are_not_joined(self) -> None:
        result = Result(unopened=[("0900000001", "Lan")])
        frames = [
            FrameRead(full_contacts=[Contact("0900000001", "Lan", False)]),
            FrameRead(full_profile_name="Hoa", full_handle="@hoa"),
        ]
        add_full_name_rows(result, frames)
        self.assertEqual(result.rows, [])

    def test_a_blank_name_is_filled_without_moving_the_pair(self) -> None:
        result = Result(rows=[Row("0900000001", "", "@lan")])
        frames = [FrameRead(full_contacts=[Contact("0900000001", "Lan", False)])]
        add_full_name_rows(result, frames)
        self.assertEqual(result.rows, [Row("0900000001", "Lan", "@lan")])

    def test_lookalike_ids_of_the_same_name_count_as_one(self) -> None:
        frames = [
            FrameRead(full_contacts=[Contact("0900000001", "Lan", False)]),
            FrameRead(full_profile_name="Lan", full_handle="@lan_shop"),
            FrameRead(full_profile_name="Lan", full_handle="@lan_shop"),
            FrameRead(full_profile_name="Lan", full_handle="@lan__shop"),
        ]
        result = add_full_name_rows(Result(unopened=[("0900000001", "")]), frames)
        self.assertEqual(result.rows, [Row("0900000001", "Lan", "@lan_shop")])


class SidePairTests(unittest.TestCase):
    def test_the_right_column_keeps_the_phone_above_the_name(self) -> None:
        self.assertEqual(len(select_right_pairs([_right_pair(200, 400)], WIDTH, None)), 1)

    def test_the_avatar_column_is_not_the_contact_pair(self) -> None:
        self.assertEqual(select_right_pairs([_right_pair(20, 400)], WIDTH, None), [])

    def test_a_number_hidden_under_the_title_stays_out_of_the_side_pairs(self) -> None:
        title = TextLine("Danh bạ", 200, 10, 500, 80, 40)
        self.assertEqual(select_right_pairs([_right_pair(200, 40)], WIDTH, title), [])
        self.assertEqual(len(select_right_pairs([_right_pair(200, 120)], WIDTH, title)), 1)

    def test_a_follow_button_under_the_phone_is_not_a_name(self) -> None:
        phone = TextLine("0900000001", 200, 400, 500, 440, 40)
        follow = TextLine("Follow", 200, 450, 320, 490, 40)
        self.assertEqual(select_right_pairs([(phone, follow)], WIDTH, None), [])

    def test_the_left_at_id_is_kept_and_one_on_the_right_is_not(self) -> None:
        name = TextLine("Lan", 40, 300, 200, 350, 40)
        handle = TextLine("@lan123", 40, 360, 280, 400, 36)
        right = TextLine("@khac999", 700, 200, 980, 240, 36)
        got_name, got_handle = select_left_profile([right, name, handle], HEIGHT, WIDTH)
        self.assertIsNotNone(got_handle)
        self.assertEqual(got_handle.text if got_handle is not None else "", "@lan123")
        self.assertEqual(got_name.text if got_name is not None else "", "Lan")

    def test_an_at_id_on_the_right_is_not_the_profile_pair(self) -> None:
        right = TextLine("@khac999", 700, 300, 980, 340, 36)
        self.assertEqual(select_left_profile([right], HEIGHT, WIDTH), (None, None))

    def test_a_blank_name_on_the_first_pass_is_filled_without_moving_the_row(self) -> None:
        screen = [Contact("0900000001", ""), Contact("0900000002", "")]
        frames = [
            FrameRead("list", screen, side_contacts=[Contact("0900000001", "Lan", False)]),
            FrameRead("profile", profile_name="", handle="@mot", side_profile_name="Lan", side_handle="@mot"),
            FrameRead("list", screen),
            FrameRead("profile", profile_name="", handle="@hai"),
        ]
        result = add_side_rows(build(frames), frames)
        self.assertEqual(
            [(row.phone, row.name, row.handle) for row in result.rows],
            [("0900000001", "Lan", "@mot"), ("0900000002", "", "@hai")],
        )

    def test_the_same_name_adds_one_row_of_three_cells(self) -> None:
        result = Result(
            unopened=[("0900000009", "Lan")],
            review=[Review("", "", "Lan", "@lan123", "đã mở hồ sơ nhưng không thấy số cùng tên")],
        )
        frames = [
            FrameRead("list", side_contacts=[Contact("0900000009", "Lan", False)]),
            FrameRead("profile", side_profile_name="Lan", side_handle="@lan123"),
        ]
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [Row("0900000009", "Lan", "@lan123")])
        self.assertEqual(result.unopened, [])
        self.assertEqual(result.review, [])

    def test_a_row_already_saved_stays_as_it_is(self) -> None:
        result = Result(rows=[Row("0900000001", "Lan", "@lan123")])
        frames = [
            FrameRead("list", side_contacts=[Contact("0900000001", "Lan", False)]),
            FrameRead("profile", side_profile_name="Lan", side_handle="@lan123"),
        ]
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [Row("0900000001", "Lan", "@lan123")])

    def test_different_names_are_not_joined(self) -> None:
        result = Result(unopened=[("0900000009", "Lan")])
        frames = [
            FrameRead("list", side_contacts=[Contact("0900000009", "Lan", False)]),
            FrameRead("profile", side_profile_name="Hoa", side_handle="@hoa"),
        ]
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened, [("0900000009", "Lan")])

    def test_two_phones_with_one_name_are_not_guessed(self) -> None:
        result = Result(unopened=[("0900000001", "Lan"), ("0900000002", "Lan")])
        frames = [
            FrameRead(
                "list",
                side_contacts=[Contact("0900000001", "Lan", False), Contact("0900000002", "Lan", False)],
            ),
            FrameRead("profile", side_profile_name="Lan", side_handle="@lan"),
        ]
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened, [("0900000001", "Lan"), ("0900000002", "Lan")])

    def test_a_handle_already_saved_with_another_phone_is_not_taken(self) -> None:
        result = Result(rows=[Row("0900000001", "Khac", "@lan")], unopened=[("0900000009", "Lan")])
        frames = [
            FrameRead("list", side_contacts=[Contact("0900000009", "Lan", False)]),
            FrameRead("profile", side_profile_name="Lan", side_handle="@lan"),
        ]
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [Row("0900000001", "Khac", "@lan")])
        self.assertEqual(result.unopened, [("0900000009", "Lan")])

    def test_many_numbers_sharing_a_name_block_the_new_row(self) -> None:
        result = Result(
            review=[Review("0900000001", "Lan", "Lan", "@lan", "nhiều số cùng tên")],
            unopened=[("0900000009", "Lan")],
        )
        frames = [
            FrameRead("list", side_contacts=[Contact("0900000009", "Lan", False)]),
            FrameRead("profile", side_profile_name="Lan", side_handle="@lan"),
        ]
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened, [("0900000009", "Lan")])

    def test_marks_do_not_block_the_side_join(self) -> None:
        result = Result()
        frames = [
            FrameRead("list", side_contacts=[Contact("0900000009", "Trúc", False)]),
            FrameRead("profile", side_profile_name="TRUC", side_handle="@truc"),
        ]
        add_side_rows(result, frames)
        self.assertEqual([(row.phone, row.name, row.handle) for row in result.rows], [("0900000009", "Trúc", "@truc")])

    def test_side_fields_do_not_change_the_old_join(self) -> None:
        frames = [
            FrameRead(
                "list",
                [Contact("0900000001", "Hoa"), Contact("0900000002", "Minh")],
                side_contacts=[Contact("0900000009", "Lan", False)],
            ),
            FrameRead("profile", profile_name="Lan", handle="@lan", side_profile_name="Lan", side_handle="@lan"),
        ]
        result = build(frames)
        self.assertEqual(result.rows, [])
        self.assertEqual([phone for phone, _name in result.unopened], ["0900000001", "0900000002"])
        add_side_rows(result, frames)
        self.assertEqual(result.rows, [Row("0900000009", "Lan", "@lan")])
        self.assertEqual([phone for phone, _name in result.unopened], ["0900000001", "0900000002"])
        self.assertFalse(any(item.handle == "@lan" for item in result.review))

    def test_side_reads_use_only_a_few_frames(self) -> None:
        phone, name = _right_pair(200, 400)
        layouts = [Layout("list", side_pairs=[(phone, name)]) for _ in range(10)]
        self.assertEqual(len(side_frames(layouts, {})), 3)


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
