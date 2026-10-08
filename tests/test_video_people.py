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
    name_key,
    joined_name,
    names_close,
    normalize_phone,
    pair_close_names,
    phone_in_text,
)
from control_plane.video_scan import keep_stable, voting_frames, write_table


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
        self.assertTrue(names_close("Lò Thị Việt", "Lo Thi e Viet nt"))
        self.assertTrue(names_close("sỹ hoa hồng trắng", "hoa hồng trắng"))
        self.assertTrue(names_close("Phuong Le44036", "Phuong Le4405ó"))
        self.assertFalse(names_close("tân", "Tuấn"))
        self.assertFalse(names_close("liên", "lien"))
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
    def test_keeps_settled_frames_and_skips_motion(self) -> None:
        still_a = np.zeros((8, 8), dtype=np.float32)
        still_b = np.full((8, 8), 40, dtype=np.float32)
        motion = np.full((8, 8), 20, dtype=np.float32)
        frames = [still_a, still_a, motion, still_b, still_b]
        self.assertEqual(keep_stable(frames), [0, 3])

    def test_small_screen_change_is_kept(self) -> None:
        still = np.zeros((8, 8), dtype=np.float32)
        other = np.full((8, 8), 2, dtype=np.float32)
        frames = [still, still, other, other]
        self.assertEqual(keep_stable(frames), [0, 2])

    def test_stable_scene_keeps_a_second_frame_for_a_vote(self) -> None:
        still_a = np.zeros((8, 8), dtype=np.float32)
        still_b = np.full((8, 8), 40, dtype=np.float32)
        motion = np.full((8, 8), 20, dtype=np.float32)
        frames = [still_a, still_a, motion, still_b, still_b]
        self.assertEqual(voting_frames(frames), [0, 1, 3, 4])


if __name__ == "__main__":
    unittest.main()
