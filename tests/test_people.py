"""Ghép danh bạ và hồ sơ khi cùng một tên đã được nhìn thấy."""

from __future__ import annotations

import unittest

from control_plane.people import clean_username, complete_rows, fold_sightings, name_key


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
        self.assertEqual(clean_username("Hong@1978"), "")
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
