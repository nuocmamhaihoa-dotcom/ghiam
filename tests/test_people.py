"""Ghép danh bạ và hồ sơ khi cùng một tên đã được nhìn thấy."""

from __future__ import annotations

import unittest

from control_plane.people import (
    apply_novel,
    clean_username,
    complete_rows,
    complete_sightings,
    fold_sightings,
    name_key,
    sighting_adds,
)


class PeopleMergeTests(unittest.TestCase):
    def test_contact_then_profile_fills_one_row(self) -> None:
        stored = fold_sightings(
            [],
            [{"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"}],
        )
        self.assertEqual(complete_rows(stored), [])
        folded = fold_sightings(
            stored,
            [{"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"}],
        )
        ready = complete_rows(folded)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["name"], "Trần Tùng")
        self.assertEqual(ready[0]["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(ready[0]["username"], "@trn.tng751")

    def test_later_contact_name_does_not_overwrite(self) -> None:
        stored = fold_sightings(
            [],
            [{"kind": "contact", "name": "Bà soi", "contactName": "Chị Soi Xuân Trung"}],
        )
        folded = fold_sightings(
            stored,
            [{"kind": "contact", "name": "Bà soi", "contactName": "Tên khác"}],
        )
        self.assertEqual(folded[0]["contactName"], "Chị Soi Xuân Trung")

    def test_username_prefix_and_invalid_drop(self) -> None:
        self.assertEqual(clean_username("trn.tng751"), "@trn.tng751")
        self.assertEqual(clean_username("@b.soi22"), "@b.soi22")
        self.assertEqual(clean_username("@hoanganh1116"), "@hoanganh1116")
        self.assertEqual(clean_username("Hong@1978"), "")
        self.assertEqual(clean_username("@kol"), "")
        self.assertEqual(clean_username("@khac"), "")
        folded = fold_sightings(
            [],
            [{"kind": "profile", "name": "Hồng", "username": "không hợp lệ"}],
        )
        self.assertEqual(folded[0]["username"], "")
        self.assertEqual(complete_rows(folded), [])

    def test_casefold_matches_without_stripping_accents(self) -> None:
        self.assertEqual(name_key("  Trần   Tùng "), name_key("trần tùng"))
        self.assertNotEqual(name_key("Trần Tùng"), name_key("Tran Tung"))
        stored = fold_sightings(
            [],
            [{"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"}],
        )
        folded = fold_sightings(
            stored,
            [{"kind": "profile", "name": "trần tùng", "username": "b.soi22"}],
        )
        ready = complete_rows(folded)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["name"], "Trần Tùng")
        self.assertEqual(ready[0]["username"], "@b.soi22")

    def test_saved_column_is_not_written_again(self) -> None:
        stored, added = apply_novel(
            [],
            [
                {"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"},
                {"kind": "profile", "name": "Trần Tùng", "username": "@trn.tng751"},
            ],
        )
        self.assertEqual(added, 2)
        again, added_again = apply_novel(
            stored,
            [
                {"kind": "contact", "name": "Trần Tùng", "contactName": "Tên khác"},
                {"kind": "profile", "name": "Trần Tùng", "username": "@khac"},
            ],
        )
        self.assertEqual(added_again, 0)
        self.assertEqual(again[0]["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(again[0]["username"], "@trn.tng751")

    def test_empty_column_can_still_be_filled(self) -> None:
        stored, added = apply_novel(
            [],
            [{"kind": "contact", "name": "Bà soi", "contactName": "Chị Soi Xuân Trung"}],
        )
        self.assertEqual(added, 1)
        self.assertTrue(
            sighting_adds(stored[0], {"kind": "profile", "name": "Bà soi", "username": "@b.soi22"})
        )
        self.assertFalse(
            sighting_adds(stored[0], {"kind": "contact", "name": "Bà soi", "contactName": "Khác"})
        )

    def test_many_complete_rows_are_kept(self) -> None:
        rows = [
            {"name": f"Nguoi {index:02d} Aa", "contactName": f"Danh {index:02d}", "username": f"@n{index:02d}aa"}
            for index in range(50)
        ]
        folded, added = apply_novel([], complete_sightings(rows))
        self.assertEqual(added, 100)
        self.assertEqual(len(complete_rows(folded)), 50)
