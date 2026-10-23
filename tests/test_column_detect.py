"""Nhận cột theo nội dung: tránh nhầm SĐT, UID, tên, địa chỉ, TikTok."""

from __future__ import annotations

import unittest

from control_plane.column_detect import (
    assign_fields,
    looks_like_address,
    looks_like_facebook_uid,
    looks_like_name,
    looks_like_tiktok_id,
    looks_like_vn_phone,
    suggest_mapping,
)
from control_plane.excel_import import preview_table, write_xlsx
from control_plane.vietnam_places import has_vietnam_place


class CellSignalTests(unittest.TestCase):
    def test_phone_accepts_vn_prefixes_and_country_codes(self) -> None:
        self.assertTrue(looks_like_vn_phone("0901222333"))
        self.assertTrue(looks_like_vn_phone("+84 901 222 333"))
        self.assertTrue(looks_like_vn_phone("84901222333"))
        self.assertTrue(looks_like_vn_phone("084901222333"))
        self.assertTrue(looks_like_vn_phone("0084901222333"))
        self.assertTrue(looks_like_vn_phone("901222333"))
        self.assertFalse(looks_like_vn_phone("1000123456789"))
        self.assertFalse(looks_like_vn_phone("10001"))
        self.assertFalse(looks_like_vn_phone("@lan.hue"))

    def test_facebook_is_digits_but_not_a_phone(self) -> None:
        self.assertTrue(looks_like_facebook_uid("1000123456789"))
        self.assertTrue(looks_like_facebook_uid("1.000123456789e+12"))
        self.assertTrue(looks_like_facebook_uid("https://www.facebook.com/profile.php?id=1000123456789"))
        self.assertFalse(looks_like_facebook_uid("0901222333"))
        self.assertFalse(looks_like_facebook_uid("Nguyễn Lan"))

    def test_tiktok_needs_at_sign(self) -> None:
        self.assertTrue(looks_like_tiktok_id("@lan.hue"))
        self.assertTrue(looks_like_tiktok_id("https://www.tiktok.com/@lan.hue"))
        self.assertFalse(looks_like_tiktok_id("lan.hue"))
        self.assertFalse(looks_like_tiktok_id("Nguyễn Lan"))

    def test_name_is_letters_without_numbers_or_places(self) -> None:
        self.assertTrue(looks_like_name("Nguyễn Lan"))
        self.assertTrue(looks_like_name("Nguyễn Văn Quân"))
        self.assertFalse(looks_like_name("0901222333"))
        self.assertFalse(looks_like_name("12 Lê Lợi"))
        self.assertFalse(looks_like_name("Huế"))
        self.assertFalse(looks_like_name("@lan.hue"))

    def test_address_uses_places_with_and_without_marks(self) -> None:
        self.assertTrue(looks_like_address("Huế"))
        self.assertTrue(looks_like_address("12 Lê Lợi, Quận 1, TP.HCM"))
        self.assertTrue(looks_like_address("Ha Noi"))
        self.assertTrue(looks_like_address("phuong Ben Nghe, quan 1"))
        self.assertTrue(has_vietnam_place("Thành phố Hồ Chí Minh"))
        self.assertTrue(has_vietnam_place("thu duc"))
        self.assertFalse(looks_like_address("Nguyễn Lan"))
        self.assertFalse(looks_like_address("Nguyễn Văn Quân"))


class ColumnAssignTests(unittest.TestCase):
    def test_generic_headers_follow_cell_content(self) -> None:
        headers = ["Cột 1", "Cột 2", "Cột 3", "Cột 4", "Cột 5"]
        values = [
            ["Nguyễn Lan", "Trần Minh", "Lê Hoa"],
            ["0901222333", "084901444555", "84901555666"],
            ["Huế", "Ha Noi", "12 Lê Lợi, Quận 1, TP.HCM"],
            ["1000123456789", "1000987654321", "1.000555666777e+12"],
            ["@lan.hue", "@minh.hn", "https://www.tiktok.com/@hoa.hue"],
        ]
        mapping = suggest_mapping(headers, values)
        self.assertEqual(mapping["name"], 0)
        self.assertEqual(mapping["phone"], 1)
        self.assertEqual(mapping["address"], 2)
        self.assertEqual(mapping["facebookUid"], 3)
        self.assertEqual(mapping["tiktokId"], 4)

    def test_swapped_columns_still_map_by_content(self) -> None:
        headers = ["A", "B"]
        values = [
            ["1000123456789", "1000222333444"],
            ["Nguyễn Lan", "Trần Minh"],
        ]
        mapping = suggest_mapping(headers, values)
        self.assertEqual(mapping["facebookUid"], 0)
        self.assertEqual(mapping["name"], 1)
        self.assertIsNone(mapping["phone"])

    def test_phone_column_is_not_taken_as_facebook(self) -> None:
        headers = ["uid", "name"]
        values = [
            ["0901222333", "0912345678", "0321111222"],
            ["Lan", "Minh", "Hoa"],
        ]
        mapping = suggest_mapping(headers, values)
        self.assertEqual(mapping["phone"], 0)
        self.assertEqual(mapping["name"], 1)
        self.assertIsNone(mapping["facebookUid"])

    def test_multiple_address_columns_become_separate_lines(self) -> None:
        from control_plane.excel_import import mapping_from_form, rows_from_files

        payload = write_xlsx(
            ["Tên khách", "Số nhà", "Phường", "Tỉnh"],
            [["Nguyễn Lan", "12 Lê Lợi", "Quận 1", "TP.HCM"]],
        )
        preview = preview_table("nhieu-dia-chi.xlsx", payload)
        self.assertEqual(preview["suggested"]["name"], 0)
        details_indexes = suggest_mapping(
            preview["headers"],
            [
                ["Nguyễn Lan"],
                ["12 Lê Lợi"],
                ["Quận 1"],
                ["TP.HCM"],
            ],
        )
        self.assertEqual(details_indexes["name"], 0)
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên khách"},
                "address": {
                    "columns": [
                        {"index": 1, "header": "Số nhà"},
                        {"index": 2, "header": "Phường"},
                        {"index": 3, "header": "Tỉnh"},
                    ]
                },
            }
        )
        incoming, _reports = rows_from_files([("nhieu-dia-chi.xlsx", payload)], mapping)
        self.assertEqual(incoming[0]["name"], "Nguyễn Lan")
        self.assertEqual(incoming[0]["address"], "12 Lê Lợi\nQuận 1\nTP.HCM")

    def test_street_and_admin_headers_split_apart(self) -> None:
        headers = ["Tên khách", "Số nhà", "Phường", "Tỉnh"]
        values = [
            ["Nguyễn Lan"],
            ["12 Lê Lợi"],
            ["Quận 1"],
            ["TP.HCM"],
        ]
        mapping, details = assign_fields(headers, values)
        self.assertEqual(mapping["name"], 0)
        self.assertEqual(mapping["address"], 1)
        self.assertEqual(details["address"]["indexes"], [1])
        self.assertEqual(mapping["adminAddress"], 2)
        self.assertEqual(details["adminAddress"]["indexes"], [2, 3])

    def test_unlabeled_address_parts_are_all_kept(self) -> None:
        headers = ["A", "B", "C", "D"]
        values = [
            ["Nguyễn Lan", "Trần Minh"],
            ["0901222333", "0901444555"],
            ["Huế", "Ha Noi"],
            ["Phường Phú Hội", "Cầu Giấy"],
        ]
        mapping, details = assign_fields(headers, values)
        self.assertEqual(mapping["name"], 0)
        self.assertEqual(mapping["phone"], 1)
        self.assertEqual(mapping["address"], 2)
        self.assertEqual(details["address"]["indexes"], [2, 3])

    def test_labeled_tiktok_without_at_still_maps(self) -> None:
        payload = write_xlsx(
            ["Tên khách", "Số điện thoại khách", "Địa chỉ khách", "UID Facebook", "ID TikTok"],
            [["Lan", "0901222333", "Huế", "10001", "lan.hue"]],
        )
        preview = preview_table("danh-sach.xlsx", payload)
        self.assertEqual(preview["suggested"]["name"], 0)
        self.assertEqual(preview["suggested"]["phone"], 1)
        self.assertEqual(preview["suggested"]["address"], 2)
        self.assertEqual(preview["suggested"]["facebookUid"], 3)
        self.assertEqual(preview["suggested"]["tiktokId"], 4)
