"""Hướng đọc 2 và 3 chỉ ghép phần còn sót, không đụng hàng đã đủ 3 ô."""

from __future__ import annotations

import unittest
from pathlib import Path

from control_plane.read_fallback import (
    apply_read_fallbacks,
    gaps_remain,
    pair_leftovers_by_time,
    parse_header_text,
    read_headers_for_gaps,
)
from control_plane.screen_table import ContactHit, FrameObs, Review, Row, Table, Unopened


def _list(phone: str, name: str, at: float, selected: bool = True) -> FrameObs:
    return FrameObs("list", (ContactHit(phone, name, selected),), at=at)


def _profile(name: str, username: str, at: float) -> FrameObs:
    return FrameObs("profile", (), name, username, at=at)


class TimePairTests(unittest.TestCase):
    def test_complete_row_stays_when_another_pair_is_filled(self) -> None:
        frames = [
            _list("0982117072", "Đặng Thị Tâm", 1.0),
            _profile("Đặng Thị Tâm", "@dangtam.3", 2.0),
            _list("0332001753", "khactam", 10.0),
            _profile("khactam", "@khactam60", 12.0),
        ]
        table = Table(
            rows=[Row("0982117072", "Đặng Thị Tâm", "@dangtam.3")],
            unopened=[Unopened("0332001753", "khactam")],
            review=[Review("", "", "khactam", "@khactam60", "đã mở hồ sơ nhưng chưa thấy số")],
        )
        result = pair_leftovers_by_time(table, frames)
        self.assertEqual(result.rows[0], Row("0982117072", "Đặng Thị Tâm", "@dangtam.3"))
        self.assertEqual(result.rows[1].phone, "0332001753")
        self.assertEqual(result.rows[1].username, "@khactam60")
        self.assertFalse(result.unopened)
        self.assertFalse(result.review)

    def test_two_profiles_near_one_phone_stay_unpaired(self) -> None:
        frames = [
            _list("0332001753", "khactam", 10.0),
            _profile("khactam", "@khactam60", 11.0),
            _profile("khactam", "@khac.khac", 12.0),
        ]
        table = Table(
            unopened=[Unopened("0332001753", "khactam")],
            review=[
                Review("", "", "khactam", "@khactam60", "đã mở hồ sơ nhưng chưa thấy số"),
                Review("", "", "khactam", "@khac.khac", "đã mở hồ sơ nhưng chưa thấy số"),
            ],
        )
        result = pair_leftovers_by_time(table, frames)
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.unopened), 1)
        self.assertEqual(len(result.review), 2)

    def test_another_profile_between_blocks_the_time_pair(self) -> None:
        frames = [
            _list("0982117072", "Đặng Thị Tâm", 1.0),
            _profile("khactam", "@khactam60", 1.3),
            _profile("Đặng Thị Tâm", "@dangtam.3", 1.6),
        ]
        table = Table(
            unopened=[Unopened("0982117072", "Đặng Thị Tâm")],
            review=[
                Review("", "", "khactam", "@khactam60", "đã mở hồ sơ nhưng chưa thấy số"),
                Review("", "", "Đặng Thị Tâm", "@dangtam.3", "đã mở hồ sơ nhưng chưa thấy số"),
            ],
        )
        result = pair_leftovers_by_time(table, frames)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.unopened[0].phone, "0982117072")
        self.assertEqual({item.username for item in result.review}, {"@khactam60", "@dangtam.3"})

    def test_pair_farther_than_four_seconds_is_left_alone(self) -> None:
        frames = [
            _list("0332001753", "khactam", 1.0),
            _profile("khactam", "@khactam60", 9.5),
        ]
        table = Table(
            unopened=[Unopened("0332001753", "khactam")],
            review=[Review("", "", "khactam", "@khactam60", "đã mở hồ sơ nhưng chưa thấy số")],
        )
        result = pair_leftovers_by_time(table, frames)
        self.assertIs(result, table)

    def test_photo_without_time_is_not_rewritten(self) -> None:
        table = Table(unopened=[Unopened("0373793079", "tinh11")])
        frames = [FrameObs("list", (ContactHit("0373793079", "tinh11"),))]
        self.assertIs(pair_leftovers_by_time(table, frames), table)
        self.assertFalse(gaps_remain(Table(rows=[Row("0982117072", "Đặng", "@dang")])))


class HeaderFallbackTests(unittest.TestCase):
    def test_header_text_keeps_handle_and_phone(self) -> None:
        phone, name, username = parse_header_text("Đặng Thị Tâm\n@dangtam.3\n0982117072")
        self.assertEqual(username, "@dangtam.3")
        self.assertEqual(phone, "0982117072")
        self.assertIn("Đặng", name)
        self.assertEqual(parse_header_text("6 thg 10 2026 chọn\n@ngaychon1"), ("", "", ""))
        self.assertEqual(parse_header_text("Phương Anh\n@0986249011"), ("0986249011", "", ""))
        self.assertEqual(parse_header_text("@phuonganh99\nPhương Anh")[2], "")

    def test_header_read_fills_only_the_nearby_phone(self) -> None:
        frames = [(_profile_path(), _list("0982117072", "Đặng Thị Tâm", 10.0))]
        table = Table(unopened=[Unopened("0982117072", "Đặng Thị Tâm")])

        def reader(_path: Path) -> str:
            return "Đặng Thị Tâm\n@dangtam.3"

        result = read_headers_for_gaps(table, frames, reader=reader)
        self.assertEqual([(row.phone, row.username) for row in result.rows], [("0982117072", "@dangtam.3")])
        self.assertFalse(result.unopened)

    def test_unmatched_header_phone_is_not_added(self) -> None:
        frames = [(_profile_path(), _profile("Khác", "@khac.1", 3.0))]
        table = Table(review=[Review("", "", "Khác", "@khac.1", "đã mở hồ sơ nhưng chưa thấy số")])

        def reader(_path: Path) -> str:
            return "Người lạ\n0900000099"

        result = read_headers_for_gaps(table, frames, reader=reader)
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.review), 1)
        self.assertFalse(result.unopened)

    def test_complete_table_does_not_call_the_header_reader(self) -> None:
        table = Table(rows=[Row("0982117072", "Đặng Thị Tâm", "@dangtam.3")])
        frames = [(Path("x.jpg"), _list("0982117072", "Đặng Thị Tâm", 1.0))]

        def reader(_path: Path) -> str:
            raise AssertionError("không được đọc lại hàng đã đủ")

        self.assertIs(apply_read_fallbacks(table, frames), table)
        self.assertIs(read_headers_for_gaps(table, frames, reader=reader), table)


def _profile_path() -> Path:
    return Path("/tmp/header-fallback-frame.jpg")


class PathExistsTests(unittest.TestCase):
    def test_missing_file_is_skipped(self) -> None:
        frames = [(Path("/tmp/does-not-exist-fallback.jpg"), _list("0982117072", "Đặng", 1.0))]
        table = Table(unopened=[Unopened("0982117072", "Đặng")])
        called = []

        def reader(path: Path) -> str:
            called.append(path)
            return ""

        result = read_headers_for_gaps(table, frames, reader=reader)
        self.assertEqual(called, [])
        self.assertEqual(result.unopened[0].phone, "0982117072")


if __name__ == "__main__":
    unittest.main()
