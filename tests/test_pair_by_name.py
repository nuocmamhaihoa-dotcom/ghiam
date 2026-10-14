"""Cùng tên khách thì một hàng: số điện thoại, tên, id. Không dùng cách ghép cũ."""

from __future__ import annotations

import unittest

from control_plane.screen_table import ContactHit, FrameObs, pair_by_same_name, repair_table


def _list(*hits: ContactHit, at: float = -1.0) -> FrameObs:
    return FrameObs("list", tuple(hits), at=at)


def _profile(name: str, username: str, at: float = -1.0) -> FrameObs:
    return FrameObs("profile", (), name, username, at=at)


class SameNamePairTests(unittest.TestCase):
    def test_truc_phone_joins_the_truc_id(self) -> None:
        frames = [
            _list(
                ContactHit("0919313090", "Truc"),
                ContactHit("0908031961", "Thu Vân"),
                ContactHit("0919277810", "Phuong Huynh"),
                at=1.0,
            ),
            _profile("Truc", "@truc31798", at=2.0),
            _profile("Thu Vân", "@thuvan1", at=40.0),
        ]
        table = pair_by_same_name(frames)
        self.assertTrue(table.strict)
        self.assertEqual(
            [(row.phone, row.name, row.username) for row in table.rows],
            [
                ("0919313090", "Truc", "@truc31798"),
                ("0908031961", "Thu Vân", "@thuvan1"),
            ],
        )
        self.assertEqual([(item.phone, item.name) for item in table.unopened], [("0919277810", "Phuong Huynh")])
        self.assertEqual(table.review, [])

    def test_different_name_does_not_take_the_id(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", "Truc"), at=1.0),
                _profile("KimAnh", "@kimanh1", at=2.0),
            ]
        )
        self.assertEqual(table.rows, [])
        self.assertEqual([item.phone for item in table.unopened], ["0919313090"])
        self.assertEqual([(item.profile_name, item.username) for item in table.review], [("KimAnh", "@kimanh1")])

    def test_same_name_pairs_far_apart_in_the_video(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", "Truc"), at=1.0),
                _profile("Truc", "@truc31798", at=500.0),
            ]
        )
        self.assertEqual(
            [(row.phone, row.name, row.username) for row in table.rows],
            [("0919313090", "Truc", "@truc31798")],
        )

    def test_one_id_is_not_copied_onto_every_same_name(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", "Truc"), at=1.0),
                _profile("Truc", "@truc31798", at=2.0),
                _list(ContactHit("0908035416", "Truc"), at=3.0),
            ]
        )
        self.assertEqual(
            [(row.phone, row.username) for row in table.rows],
            [("0919313090", "@truc31798")],
        )
        self.assertEqual([item.phone for item in table.unopened], ["0908035416"])
        self.assertEqual(table.review, [])

    def test_extra_id_stays_without_a_phone(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", "Truc"), at=1.0),
                _profile("Truc", "@truc31798", at=2.0),
                _profile("Truc", "@truc99999", at=4.0),
            ]
        )
        self.assertEqual([row.username for row in table.rows], ["@truc31798"])
        self.assertEqual([item.username for item in table.review], ["@truc99999"])
        self.assertEqual(table.review[0].reason, "đã mở hồ sơ nhưng chưa thấy số")
        self.assertEqual(table.review[0].phone, "")

    def test_dropped_accent_matches_the_marked_name(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", "Truc"), at=1.0),
                _profile("Trúc", "@truc31798", at=2.0),
            ]
        )
        self.assertEqual(
            [(row.phone, row.name, row.username) for row in table.rows],
            [("0919313090", "Truc", "@truc31798")],
        )

    def test_different_marks_stay_two_people(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0908031961", "Thu Vân"), at=1.0),
                _profile("Thu Văn", "@thuvan9", at=2.0),
            ]
        )
        self.assertEqual(table.rows, [])
        self.assertEqual([item.phone for item in table.unopened], ["0908031961"])
        self.assertEqual([item.username for item in table.review], ["@thuvan9"])

    def test_handle_containing_the_name_does_not_pair(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0987654321", "nvchien"), at=1.0),
                _profile("người khác", "@nvchien89", at=2.0),
            ]
        )
        self.assertEqual(table.rows, [])
        repair_table(table)
        self.assertEqual(table.rows, [])
        self.assertEqual([item.phone for item in table.unopened], ["0987654321"])

    def test_ocr_duplicate_handle_is_one_id(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", "Truc"), at=1.0),
                _profile("Truc", "@truc31798", at=2.0),
                _profile("Truc", "@truc31798C3", at=2.4),
            ]
        )
        self.assertEqual([row.username for row in table.rows], ["@truc31798"])
        self.assertEqual(table.review, [])
        self.assertEqual(table.unopened, [])

    def test_phone_without_a_name_is_not_paired(self) -> None:
        table = pair_by_same_name(
            [
                _list(ContactHit("0919313090", ""), at=1.0),
                _profile("Truc", "@truc31798", at=2.0),
            ]
        )
        self.assertEqual(table.rows, [])
        nameless = [item for item in table.review if item.phone == "0919313090"]
        self.assertEqual(len(nameless), 1)
        self.assertEqual(nameless[0].reason, "có số trong danh bạ nhưng chưa đọc được tên")
        self.assertEqual([item.username for item in table.review if item.username], ["@truc31798"])


if __name__ == "__main__":
    unittest.main()
