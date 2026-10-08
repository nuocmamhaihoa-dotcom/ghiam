"""Ghép bảng 3 cột từ danh bạ và hồ sơ, kể cả khi video có lần bấm mở hồ sơ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from control_plane.screen_read import _choose_name
from control_plane.screen_table import (
    ContactHit,
    FrameObs,
    build_table,
    choose_phone,
    clean_username,
    handle_matches_name,
    name_key,
    joined_name,
    names_close,
    normalize_phone,
    pair_close_names,
    phone_in_text,
    same_person_name,
)
from control_plane.video_scan import _Planner, write_table


def frame_list(*hits: ContactHit) -> FrameObs:
    return FrameObs("list", tuple(hits))


def frame_profile(name: str, username: str) -> FrameObs:
    return FrameObs("profile", (), name, username)


class MergeTests(unittest.TestCase):
    def test_name_key_keeps_vietnamese_marks(self) -> None:
        self.assertEqual(name_key("  Đặng   Thị Tâm "), name_key("đặng thị tâm"))
        self.assertNotEqual(name_key("Đặng Thị Tâm"), name_key("Dang Thi Tam"))
        self.assertNotEqual(name_key("khactam"), name_key("khac tam"))

    def test_phone_and_username_filters(self) -> None:
        self.assertEqual(normalize_phone("0982117072"), "0982117072")
        self.assertEqual(normalize_phone("+84 982 117 072"), "0982117072")
        self.assertEqual(normalize_phone("0982117O72"), "0982117072")
        self.assertEqual(normalize_phone("08251500"), "")
        self.assertEqual(normalize_phone("0123456789"), "")
        self.assertEqual(normalize_phone("0801234567"), "")
        self.assertEqual(normalize_phone("0711234567"), "")
        self.assertEqual(normalize_phone("0861234567"), "0861234567")
        self.assertEqual(choose_phone("0982117072", "0982117075", "0982117072"), "0982117072")
        self.assertEqual(choose_phone("0982117075", "0982117072", "0982117072"), "0982117072")
        self.assertEqual(phone_in_text("0982117072 Đặng"), "0982117072")
        self.assertEqual(phone_in_text("316 242 0"), "")
        self.assertEqual(clean_username("@dangtam.3"), "@dangtam.3")
        self.assertEqual(clean_username("dangtam.3"), "")
        self.assertEqual(clean_username("@khactam60"), "@khactam60")

    def test_method_two_pairs_exact_names_and_leaves_the_rest(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0982117072", "Đặng Thị Tâm"),
                    ContactHit("0332001753", "khactam"),
                    ContactHit("0373793079", "tinh11"),
                    ContactHit("0369610446", "Thông báo hệ thống"),
                ),
                frame_profile("Đặng Thị Tâm", "@dangtam.3"),
                frame_profile("khactam", "@khactam60"),
            ]
        )
        ready = {(row.phone, row.name, row.username) for row in table.rows}
        self.assertEqual(
            ready,
            {
                ("0982117072", "Đặng Thị Tâm", "@dangtam.3"),
                ("0332001753", "khactam", "@khactam60"),
            },
        )
        self.assertEqual([item.phone for item in table.unopened], ["0373793079"])
        self.assertFalse(table.review)

    def test_accent_loss_pairs_a_long_name(self) -> None:
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm")),
                frame_profile("Dang Thi Tam", "@dangtam.3"),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].name, "Đặng Thị Tâm")
        self.assertEqual(table.rows[0].username, "@dangtam.3")
        self.assertFalse(table.review)

    def test_duplicate_name_is_not_auto_merged(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0900000001", "Photo"),
                    ContactHit("0900000002", "Photo"),
                ),
                frame_profile("Photo", "@photo.1"),
            ]
        )
        self.assertEqual(table.rows, [])
        reasons = {item.reason for item in table.review}
        self.assertIn("trùng tên, không tự ghép", reasons)
        self.assertFalse(any(item.username == "@photo.1" and item.phone for item in table.review if item.reason != "trùng tên, không tự ghép"))

    def test_same_frame_window_pairs_by_matching_name(self) -> None:
        table = build_table(
            [
                FrameObs(
                    "list",
                    (
                        ContactHit("0982117072", "Đặng Thị Tâm"),
                        ContactHit("0332001753", "khactam"),
                    ),
                    at=1.0,
                ),
                FrameObs("profile", (), "Dang Thi Tam", "@dangtam.3", at=1.4),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].username, "@dangtam.3")
        self.assertEqual(table.unopened[0].phone, "0332001753")

    def test_same_frame_window_pairs_by_username_handle(self) -> None:
        table = build_table(
            [
                FrameObs(
                    "list",
                    (
                        ContactHit("0985721500", "nvchien"),
                        ContactHit("0332001753", "khactam"),
                    ),
                    at=2.0,
                ),
                FrameObs("profile", (), "OCR lech", "@nvchien89", at=2.3),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0985721500")
        self.assertEqual(table.rows[0].username, "@nvchien89")
        self.assertEqual(table.unopened[0].phone, "0332001753")

    def test_tap_then_profile_pairs_when_names_match(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0982117072", "Đặng Thị Tâm", selected=True),
                    ContactHit("0332001753", "khactam"),
                ),
                frame_profile("Đặng Thị Tâm", "@dangtam.3"),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].username, "@dangtam.3")
        self.assertEqual(table.unopened[0].phone, "0332001753")

    def test_tap_pairs_even_when_the_profile_name_differs(self) -> None:
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm", selected=True)),
                frame_profile("khactam", "@khactam60"),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].name, "Đặng Thị Tâm")
        self.assertEqual(table.rows[0].username, "@khactam60")
        self.assertFalse(table.review)
        self.assertEqual(table.unopened, [])

    def test_missing_profile_name_still_makes_complete_row(self) -> None:
        """OCR không đọc tên trên trang hồ sơ nhưng đã có số + tên danh bạ + @ → hàng đủ."""
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm", selected=True)),
                frame_profile("", "@dangtam.3"),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].name, "Đặng Thị Tâm")
        self.assertEqual(table.rows[0].username, "@dangtam.3")
        self.assertFalse(table.review)
        self.assertFalse(table.unopened)

    def test_second_profile_without_returning_to_list_is_not_the_same_tap(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0982117072", "Đặng Thị Tâm", selected=True),
                    ContactHit("0332001753", "khactam"),
                ),
                frame_profile("Đặng Thị Tâm", "@dangtam.3"),
                frame_profile("khactam", "@khactam60"),
            ]
        )
        ready = {row.phone: row.username for row in table.rows}
        self.assertEqual(ready["0982117072"], "@dangtam.3")
        self.assertEqual(ready["0332001753"], "@khactam60")
        self.assertFalse(table.review)

    def test_selection_clears_after_two_lists_where_the_row_is_visible_but_not_selected(self) -> None:
        plain = frame_list(ContactHit("0982117072", "Đặng Thị Tâm"))
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm", selected=True)),
                plain,
                plain,
                frame_profile("Người khác", "@khac.1"),
            ]
        )
        self.assertEqual(table.rows, [])
        self.assertEqual(table.unopened[0].phone, "0982117072")
        self.assertEqual(table.review[0].reason, "đã mở hồ sơ nhưng chưa thấy số")

    def test_one_unselected_frame_still_keeps_the_tap(self) -> None:
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm", selected=True)),
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm")),
                frame_profile("Đặng Thị Tâm", "@dangtam.3"),
            ]
        )
        self.assertEqual(table.rows[0].username, "@dangtam.3")

    def test_shared_name_tap_keeps_the_tapped_phone(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0900000001", "Photo", selected=True),
                    ContactHit("0900000002", "Photo"),
                ),
                frame_profile("Photo", "@photo.1"),
            ]
        )
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0].phone, "0900000001")
        self.assertEqual(table.rows[0].username, "@photo.1")
        self.assertEqual([item.phone for item in table.unopened], ["0900000002"])
        self.assertFalse(table.review)

    def test_diacritic_vote_wins_a_tie(self) -> None:
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "Dang Thi Tam")),
                frame_list(ContactHit("0982117072", "Đặng Thị Tâm")),
                frame_profile("Đặng Thị Tâm", "@dangtam.3"),
            ]
        )
        self.assertEqual(table.rows[0].name, "Đặng Thị Tâm")

    def test_csv_files(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0982117072", "Đặng Thị Tâm", selected=True),
                    ContactHit("0332001753", "khactam"),
                ),
                frame_profile("Người khác", "@khac.1"),
            ]
        )
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "bang.csv"
            written = write_table(table, output)
            text = output.read_text(encoding="utf-8-sig")
            self.assertIn("Số điện thoại,Tên,Username", text)
            self.assertIn("0982117072", text)
            self.assertIn("@khac.1", text)
            self.assertFalse(output.with_name("bang.can-xem.csv").exists())
            unopened = output.with_name("bang.chua-mo.csv").read_text(encoding="utf-8-sig")
            self.assertIn("0332001753", unopened)
            self.assertIn("khactam", unopened)
            self.assertEqual(len(written), 2)


class CloseNameTests(unittest.TestCase):
    def test_close_names_skip_short_words_and_different_people(self) -> None:
        self.assertTrue(names_close("Đặng Thị Tâm", "Dang Thi Tam"))
        self.assertTrue(names_close("Hanhnguyen", "ÿHanhnguyen"))
        self.assertTrue(names_close("nvchien", "nvchien"))
        self.assertTrue(same_person_name("Đặng Thị Tâm", "Dang Thi Tam"))
        self.assertTrue(handle_matches_name("@nvchien89", "nvchien"))
        self.assertTrue(names_close("Lò Thị Việt", "Lo Thi e Viet nt"))
        self.assertTrue(names_close("sỹ hoa hồng trắng", "hoa hồng trắng"))
        self.assertTrue(names_close("Phuong Le44036", "Phuong Le4405ó"))
        self.assertFalse(names_close("tân", "Tuấn"))
        self.assertFalse(names_close("liên", "lien"))
        self.assertFalse(handle_matches_name("@anh.1", "Anh"))
        self.assertFalse(names_close("Lý Mai Trang", "hoa hồng trắng"))
        self.assertFalse(names_close("Nhat anh", "Thái Thành"))
        self.assertFalse(names_close("KIÊN NGUYỄN TẤN", "KIEN NGUYEN"))
        self.assertFalse(names_close("Lo Thi Mười", "Lo Thi e Viet nt"))
        self.assertEqual(joined_name("ÿHanhnguyen", "Hanhnguyen"), "Hanhnguyen")
        self.assertEqual(joined_name("Phuong Le44036", "Phuong Le4405ó"), "Phuong Le44036")
        self.assertEqual(joined_name("Dang Thi Tam", "Đặng Thị Tâm"), "Đặng Thị Tâm")

    def test_two_phones_with_one_username_are_not_paired(self) -> None:
        pairs = pair_close_names(
            [("a", "Lý Mai Trang"), ("b", "Lý Mai Trang")],
            [("u", "Ly Mai Trang")],
        )
        self.assertEqual(pairs, [])

    def test_one_close_pair_is_kept(self) -> None:
        table = build_table(
            [
                frame_list(ContactHit("0982117072", "ÿHanhnguyen")),
                frame_profile("Hanhnguyen", "@hanhnguyenn375"),
            ]
        )
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].username, "@hanhnguyenn375")
        self.assertFalse(table.review)
        self.assertFalse(table.unopened)


class ReadGuardTests(unittest.TestCase):
    def test_one_bad_frame_does_not_create_a_second_number(self) -> None:
        frames = [frame_list(ContactHit("0982117072", "Đặng Thị Tâm")) for _ in range(4)]
        frames.append(frame_list(ContactHit("0982117075", "Đặng Thị Tâm")))
        frames.append(frame_profile("Đặng Thị Tâm", "@dangtam.3"))
        table = build_table(frames)
        phones = [row.phone for row in table.rows]
        phones += [item.phone for item in table.unopened]
        phones += [item.phone for item in table.review]
        self.assertIn("0982117072", phones)
        self.assertNotIn("0982117075", phones)

    def test_two_real_numbers_one_digit_apart_both_stay(self) -> None:
        frame = frame_list(
            ContactHit("0865299758", "Lý Mai Trang"),
            ContactHit("0865299738", "Lý Mai Trang"),
        )
        table = build_table([frame, frame, frame])
        phones = [item.phone for item in table.unopened + table.review]
        phones += [row.phone for row in table.rows]
        self.assertIn("0865299758", phones)
        self.assertIn("0865299738", phones)

    def test_single_letter_name_is_dropped(self) -> None:
        table = build_table([frame_list(ContactHit("0982117072", "K"))])
        self.assertEqual(table.rows, [])
        self.assertEqual(table.unopened, [])
        self.assertEqual(table.review, [])


class NameChoiceTests(unittest.TestCase):
    def test_keeps_digits_and_marks_from_the_clearer_read(self) -> None:
        self.assertEqual(_choose_name("tinh", "tinh11"), "tinh11")
        self.assertEqual(_choose_name("Dang Thi Tam", "Đặng Thị Tâm"), "Đặng Thị Tâm")
        self.assertEqual(_choose_name("Nguyễn Tuyết Mai", "Nguyễn Tuyết Mai S"), "Nguyễn Tuyết Mai")
        self.assertEqual(_choose_name("Trọng Nguyễn 67AG", "Trọng Nguyễn ó7AG"), "Trọng Nguyễn 67AG")


class SampleFrameTests(unittest.TestCase):
    def test_four_photos_frames_make_two_rows(self) -> None:
        assets = [
            Path("/home/ubuntu/.cursor/projects/workspace/assets/4803D6B0-4508-4BB8-97C4-11374CA8B605_L0_001.jpg"),
            Path("/home/ubuntu/.cursor/projects/workspace/assets/0BC22282-AAAA-43C4-9EB5-C8944EC4E733_L0_001.jpg"),
            Path("/home/ubuntu/.cursor/projects/workspace/assets/CDEB2FAB-DBE1-4CC7-8BF9-F5838961A78A_L0_001.jpg"),
            Path("/home/ubuntu/.cursor/projects/workspace/assets/9D4BE407-DBE3-442B-B4CB-9D13F5C4F61F_L0_001.jpg"),
        ]
        if not all(path.exists() for path in assets):
            self.skipTest("không có ảnh mẫu trên máy này")
        from control_plane.video_scan import scan_paths

        table, frames = scan_paths(assets)
        ready = {(row.phone, row.name, row.username) for row in table.rows}
        self.assertEqual(
            ready,
            {
                ("0982117072", "Đặng Thị Tâm", "@dangtam.3"),
                ("0332001753", "khactam", "@khactam60"),
            },
        )
        self.assertFalse(table.review)
        unopened = {item.phone: item.name for item in table.unopened}
        self.assertEqual(unopened["0373793079"], "tinh11")
        self.assertEqual(unopened["0918046839"], "Trọng Nguyễn 67AG")
        self.assertNotIn("0369610446", unopened)
        self.assertNotIn("0918046859", unopened)
        selected = [hit for hit in frames[0][1].contacts if hit.selected]
        self.assertEqual([hit.phone for hit in selected], ["0982117072"])


class StableFrameTests(unittest.TestCase):
    def test_small_screen_change_is_read_again(self) -> None:
        planner = _Planner()
        for thumb in [np.zeros((8, 8), dtype=np.float32)] * 8 + [np.full((8, 8), 2, dtype=np.float32)] * 8:
            planner.push(thumb)
        self.assertEqual(planner.finish().reads, [0, 8])

    def test_planner_reads_each_still_scene_once_and_probes_before_each_change(self) -> None:
        planner = _Planner()
        still_a = np.zeros((8, 8), dtype=np.float32)
        still_b = np.full((8, 8), 60, dtype=np.float32)
        motion = [np.full((8, 8), 20, dtype=np.float32), np.full((8, 8), 40, dtype=np.float32)]
        thumbs = [still_a] * 10 + motion + [still_b] * 10
        for thumb in thumbs:
            planner.push(thumb)
        plan = planner.finish()
        self.assertEqual(plan.reads, [0, 12])
        self.assertEqual(plan.taps, [[5, 6, 7, 8, 9]])
        self.assertEqual(plan.count, 22)

    def test_planner_keeps_a_short_pause_as_a_probe_even_without_a_still_read(self) -> None:
        planner = _Planner()
        thumbs = [np.full((8, 8), float(10 * (index % 7)), dtype=np.float32) for index in range(14)]
        thumbs += [np.full((8, 8), 200, dtype=np.float32)] * 3
        thumbs += [np.full((8, 8), float(10 * (index % 7)), dtype=np.float32) for index in range(14)]
        for thumb in thumbs:
            planner.push(thumb)
        plan = planner.finish()
        self.assertTrue(any(window[-1] == 16 for window in plan.taps))
        # Mở hồ sơ ~0.1–0.2s rồi thoát: ít nhất một khung đoạn đứng yên vào hàng đọc OCR.
        self.assertTrue(any(number in plan.reads for number in (14, 15, 16)))

    def test_brief_profile_pause_is_kept_when_leaving_quickly(self) -> None:
        """Danh bạ đứng yên → hồ sơ ~0.1s (2 khung) → về danh bạ: vẫn giữ khung hồ sơ."""
        planner = _Planner()
        list_thumb = np.zeros((8, 8), dtype=np.float32)
        profile_thumb = np.full((8, 8), 180, dtype=np.float32)
        for thumb in [list_thumb] * 10 + [profile_thumb] * 2 + [list_thumb] * 10:
            planner.push(thumb)
        plan = planner.finish()
        self.assertIn(0, plan.reads)
        self.assertTrue(any(10 <= number <= 11 for number in plan.reads))

    def test_single_frame_profile_flash_is_still_kept(self) -> None:
        """Chỉ ló hồ sơ 1 khung (~0.07s) rồi thoát: vẫn đưa khung đó vào hàng đọc."""
        planner = _Planner()
        list_thumb = np.zeros((8, 8), dtype=np.float32)
        profile_thumb = np.full((8, 8), 200, dtype=np.float32)
        for thumb in [list_thumb] * 8 + [profile_thumb] + [list_thumb] * 8:
            planner.push(thumb)
        plan = planner.finish()
        self.assertIn(8, plan.reads)


class TapFrameTests(unittest.TestCase):
    def test_tap_then_profile_pairs_by_the_tap(self) -> None:
        table = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm"), ContactHit("0332001753", "khactam")), at=1.0),
                FrameObs("tap", (ContactHit("0332001753", "khactam", True),), at=2.0),
                FrameObs("profile", (), "Người lạ", "@nguoila", at=3.0),
            ]
        )
        self.assertEqual([(row.phone, row.username) for row in table.rows], [("0332001753", "@nguoila")])

    def test_tap_followed_by_more_list_is_not_used(self) -> None:
        table = build_table(
            [
                FrameObs("tap", (ContactHit("0332001753", "khactam", True),), at=2.0),
                FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm"),), at=2.5),
                FrameObs("profile", (), "Người lạ", "@nguoila", at=3.0),
            ]
        )
        self.assertEqual(table.rows, [])

    def test_profile_long_after_the_tap_is_not_paired(self) -> None:
        table = build_table(
            [
                FrameObs("tap", (ContactHit("0332001753", "khactam", True),), at=2.0),
                FrameObs("profile", (), "Người lạ", "@nguoila", at=9.0),
            ]
        )
        self.assertEqual(table.rows, [])

    def test_profile_pairs_with_the_only_matching_row_on_screen(self) -> None:
        # Hai số cùng tên nhưng cách xa (>2 chữ số): không phải OCR lệch, giữ riêng.
        table = build_table(
            [
                frame_list(ContactHit("0900000001", "Photo"), ContactHit("0900000005", "Lan")),
                frame_list(ContactHit("0912345678", "Photo"), ContactHit("0900000003", "Mai Anh")),
                frame_profile("Photo", "@photo.2"),
            ]
        )
        self.assertIn(("0912345678", "@photo.2"), [(row.phone, row.username) for row in table.rows])
        self.assertNotIn("0900000001", [row.phone for row in table.rows])

    def test_two_matching_rows_on_screen_stay_apart(self) -> None:
        table = build_table(
            [
                frame_list(ContactHit("0900000001", "Photo"), ContactHit("0900000002", "Photo")),
                frame_profile("Photo", "@photo.2"),
            ]
        )
        self.assertEqual(table.rows, [])

    def test_misread_digit_merges_only_when_never_on_screen_together(self) -> None:
        frames = [frame_list(ContactHit("0982117072", "Đặng Thị Tâm")) for _ in range(2)]
        frames.append(frame_list(ContactHit("0982117075", "Đặng Thị Tâm")))
        table = build_table(frames)
        self.assertEqual([item.phone for item in table.unopened], ["0982117072"])

    def test_misread_two_digits_merge_when_never_together(self) -> None:
        frames = [
            frame_list(ContactHit("0788101657", "Đồng Nội Hương")),
            frame_list(ContactHit("0988161657", "Đồng Nội Hương")),
            frame_list(ContactHit("0788161057", "Đồng Nội Hương")),
        ]
        table = build_table(frames)
        self.assertEqual(len(table.unopened), 1)
        self.assertEqual(table.unopened[0].name, "Đồng Nội Hương")
        self.assertIn(table.unopened[0].phone, {"0788101657", "0988161657", "0788161057"})

    def test_near_phones_on_same_frame_stay_separate(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0915005586", "f_ Tranhungchef"),
                    ContactHit("0915003586", "f_ Tranhungchef"),
                ),
                frame_list(
                    ContactHit("0915005586", "f_ Tranhungchef"),
                    ContactHit("0915003586", "f_ Tranhungchef"),
                ),
            ]
        )
        phones = sorted(item.phone for item in table.unopened)
        self.assertEqual(phones, ["0915003586", "0915005586"])

    def test_far_same_name_on_same_frame_stay_separate(self) -> None:
        table = build_table(
            [
                frame_list(
                    ContactHit("0936502563", "Lâm Tường"),
                    ContactHit("0556802562", "Lâm Tường"),
                    ContactHit("0766502863", "Lâm Tường"),
                )
            ]
        )
        phones = sorted(item.phone for item in table.unopened)
        self.assertEqual(phones, ["0556802562", "0766502863", "0936502563"])


class TapSpotTests(unittest.TestCase):
    def _list_image(self, dot_row: int | None, grey_row: int | None = None, dot_x: int = 620) -> tuple:
        from PIL import Image, ImageDraw

        from control_plane.screen_read import _column_anchor, _list_buttons, _pink_boxes, _row_strip

        image = Image.new("RGB", (900, 1600), "white")
        draw = ImageDraw.Draw(image, "RGBA")
        for index in range(6):
            top = 200 + index * 170
            if grey_row == index:
                draw.rectangle((0, top - 50, 900, top + 120), fill=(240, 240, 240, 255))
            draw.rounded_rectangle((640, top, 820, top + 64), radius=30, fill=(234, 64, 86, 255))
        if dot_row is not None:
            cy = 200 + dot_row * 170 + 32
            draw.ellipse((dot_x - 42, cy - 42, dot_x + 42, cy + 42), fill=(0, 0, 0, 189))
        listed = _list_buttons(_pink_boxes(image), image.width)
        anchor_x, anchor_w = _column_anchor(listed)
        strips = [_row_strip(image, button, anchor_x, anchor_w) for button in listed]
        return image, listed, strips, anchor_x, anchor_w

    def test_touch_dot_marks_the_row(self) -> None:
        from control_plane.screen_read import _tapped_rows

        image, listed, strips, anchor_x, anchor_w = self._list_image(dot_row=2)
        self.assertEqual(len(listed), 6)
        self.assertEqual(_tapped_rows(image, listed, strips, anchor_x, anchor_w), {2})

    def test_grey_row_without_dot(self) -> None:
        from control_plane.screen_read import _tapped_rows

        image, listed, strips, anchor_x, anchor_w = self._list_image(dot_row=None, grey_row=4)
        self.assertEqual(_tapped_rows(image, listed, strips, anchor_x, anchor_w), {4})

    def test_dot_over_the_avatar_column_is_ignored(self) -> None:
        from control_plane.screen_read import _tapped_rows

        image, listed, strips, anchor_x, anchor_w = self._list_image(dot_row=1, dot_x=60)
        self.assertEqual(_tapped_rows(image, listed, strips, anchor_x, anchor_w), set())

    def test_dot_and_grey_on_different_rows_is_ignored(self) -> None:
        from control_plane.screen_read import _tapped_rows

        image, listed, strips, anchor_x, anchor_w = self._list_image(dot_row=1, grey_row=3)
        self.assertEqual(_tapped_rows(image, listed, strips, anchor_x, anchor_w), set())

    def test_dot_hiding_the_follow_button_still_marks_the_row(self) -> None:
        from control_plane.screen_read import _tapped_rows

        image, listed, strips, anchor_x, anchor_w = self._list_image(dot_row=2, dot_x=730)
        self.assertEqual(len(listed), 6)
        self.assertEqual(_tapped_rows(image, listed, strips, anchor_x, anchor_w), {2})


class VideoFailureTests(unittest.TestCase):
    def test_broken_frame_is_skipped_not_fatal(self) -> None:
        from control_plane.video_scan import read_frame_at, read_tap_at

        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "f_000001.jpg"
            broken.write_bytes(b"not an image")
            self.assertEqual(read_frame_at(str(broken), 1.5).kind, "unknown")
            self.assertEqual(read_frame_at(str(broken), 1.5).at, 1.5)
            self.assertEqual(read_tap_at([str(broken)], 2.0).kind, "unknown")

    def test_noisy_ffmpeg_does_not_hang_the_planner(self) -> None:
        import os
        import stat
        import threading

        from control_plane import video_scan

        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "ffmpeg"
            frame = video_scan.THUMB_W * video_scan.THUMB_H
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stderr.write('lỗi giải mã\\n' * 40000)\n"
                "sys.stderr.flush()\n"
                f"sys.stdout.buffer.write(bytes({frame}) * 12)\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            video = Path(folder) / "clip.mp4"
            video.write_bytes(b"x")
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = folder + os.pathsep + old_path
            found: list[object] = []
            try:
                worker = threading.Thread(target=lambda: found.append(video_scan.plan_video(video)), daemon=True)
                worker.start()
                worker.join(timeout=30)
            finally:
                os.environ["PATH"] = old_path
            self.assertFalse(worker.is_alive(), "plan_video bị treo khi ffmpeg in nhiều lỗi")
            self.assertEqual(found[0].count, 12)


if __name__ == "__main__":
    unittest.main()
