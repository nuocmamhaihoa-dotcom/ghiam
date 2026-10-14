"""Ghép phần đọc hỏng: @ chứa tên, chấm che trùng, lệch một số, ảnh dải chữ."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from control_plane.screen_table import (
    COVER_REASON,
    ContactHit,
    FrameObs,
    Review,
    Table,
    Unopened,
    build_table,
    handle_contains_name,
    repair_table,
)
from control_plane.strip_shot import jpeg_strip
from control_plane.video_store import begin_upload, commit_upload, finish, init_db, search_results


def _box():
    from control_plane.screen_read import Box

    return Box(0, 0, 40, 12)


class ReadRepairTests(unittest.TestCase):
    def test_handle_contains_the_contact_name(self) -> None:
        self.assertTrue(handle_contains_name("@elm_yeuyeu", "Elm"))
        self.assertFalse(handle_contains_name("@emery.17", "Elm"))
        self.assertTrue(handle_contains_name("@nguyenphuong_68", "Nguyen Phuong"))
        self.assertFalse(handle_contains_name("@ww", "An"))

    def test_garbled_profile_name_still_pairs_when_the_handle_contains_the_name(self) -> None:
        table = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Elm", True),), at=1.0),
                FrameObs("list", (ContactHit("0900000001", "Khác", True),), at=3.0),
                FrameObs("profile", (), "xxxx", "@elm_yeuyeu", at=8.0),
            ]
        )
        self.assertEqual([(row.phone, row.username) for row in table.rows], [("0982117072", "@elm_yeuyeu")])

    def test_missing_profile_name_keeps_the_handle_on_the_tapped_phone(self) -> None:
        table = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=1.0),
                FrameObs("profile", (), "", "@dangtam.3", at=2.0),
            ]
        )
        self.assertEqual(table.rows[0].phone, "0982117072")
        self.assertEqual(table.rows[0].username, "@dangtam.3")

    def test_repeated_garbage_handles_are_dropped(self) -> None:
        frames = [
            FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=1.0),
        ]
        for index, handle in enumerate(("@nsezses1", "@nsezses2", "@nsezses3")):
            frames.append(FrameObs("profile", (), "nsezses", handle, at=20.0 + index))
        table = build_table(frames)
        self.assertFalse(any("nsezses" in item.username for item in table.review))

    def test_cover_note_is_dropped_when_that_name_already_has_a_phone(self) -> None:
        table = Table(
            rows=[],
            unopened=[Unopened("0982117072", "Hằng")],
            review=[Review("", "Hằng", "", "", COVER_REASON, phone_mask="0989····820")],
        )
        repair_table(table)
        self.assertEqual(table.review, [])

    def test_one_digit_twin_without_a_handle_is_merged_away(self) -> None:
        table = build_table(
            [
                FrameObs("list", (ContactHit("0982117072", "Đặng Thị Tâm", True),), at=1.0),
                FrameObs("profile", (), "Đặng Thị Tâm", "@dangtam.3", at=2.0),
                FrameObs("list", (ContactHit("0982117073", "Dang Thi Tam", False),), at=30.0),
            ]
        )
        phones = [row.phone for row in table.rows] + [item.phone for item in table.unopened]
        self.assertIn("0982117072", phones)
        self.assertNotIn("0982117073", phones)

    def test_one_digit_twins_on_the_same_screen_stay_apart(self) -> None:
        table = build_table(
            [
                FrameObs(
                    "list",
                    (
                        ContactHit("0982117072", "Đặng Thị Tâm", True),
                        ContactHit("0982117073", "Dang Thi Tam", False),
                    ),
                    at=1.0,
                ),
                FrameObs("profile", (), "Đặng Thị Tâm", "@dangtam.3", at=2.0),
            ]
        )
        phones = [row.phone for row in table.rows] + [item.phone for item in table.unopened]
        self.assertIn("0982117073", phones)

    def test_unread_strip_is_stored_on_the_result_row(self) -> None:
        image = Image.new("RGB", (80, 30), "white")
        blob = jpeg_strip(image, _box())
        self.assertTrue(blob.startswith(b"\xff\xd8"))
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "video.db"
            init_db(db)
            created = begin_upload(db, name="a.mp4", size_bytes=10, sha256="shot")
            video_id = int(created["id"])
            path = Path(tmp) / "a.mp4"
            path.write_bytes(b"x")
            commit_upload(db, video_id, str(path))
            finish(
                db,
                video_id,
                Table(
                    review=[
                        Review(
                            "",
                            "Hằng",
                            "",
                            "",
                            COVER_REASON,
                            phone_mask="0989····820",
                            phone_shot=blob,
                        )
                    ]
                ),
            )
            rows = search_results(db, view="all", limit=10)
            self.assertEqual(rows[0]["phone"], "")
            self.assertEqual(rows[0]["phone_mask"], "0989····820")
            self.assertIn("phone", rows[0]["shots"])

    def test_strict_repair_does_not_pair_a_handle_that_contains_the_name(self) -> None:
        table = Table(
            strict=True,
            unopened=[Unopened("0985721500", "nvchien")],
            review=[Review("", "", "OCR lech", "@nvchien89", "đã mở hồ sơ nhưng chưa thấy số")],
        )
        repair_table(table)
        self.assertEqual(table.rows, [])
        self.assertEqual(table.unopened[0].phone, "0985721500")
        self.assertEqual(table.review[0].username, "@nvchien89")

    def test_strict_finish_does_not_join_similar_names(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "video.db"
            init_db(db)
            created = begin_upload(db, name="a.mp4", size_bytes=10, sha256="strict-finish")
            video_id = int(created["id"])
            path = Path(tmp) / "a.mp4"
            path.write_bytes(b"x")
            commit_upload(db, video_id, str(path))
            finish(
                db,
                video_id,
                Table(
                    strict=True,
                    unopened=[Unopened("0982117072", "yHanhnguyen")],
                    review=[Review("", "", "Hanhnguyen", "@hanhnguyenn375", "đã mở hồ sơ nhưng chưa thấy số")],
                ),
            )
            rows = search_results(db, view="all", limit=10)
            by_phone = {item["phone"]: item for item in rows if item["phone"]}
            self.assertEqual(by_phone["0982117072"]["username"], "")
            self.assertTrue(any(item["username"] == "@hanhnguyenn375" and item["phone"] == "" for item in rows))


if __name__ == "__main__":
    unittest.main()
