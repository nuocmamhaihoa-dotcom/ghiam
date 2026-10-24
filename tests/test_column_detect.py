"""Khớp cột theo tên kho; thiếu tên/SĐT/địa chỉ thì lấy cột còn lại."""

from __future__ import annotations

import unittest

from control_plane.column_detect import (
    assign_fields,
    complete_core_mapping,
    header_field,
    looks_like_address,
    looks_like_facebook_uid,
    looks_like_name,
    looks_like_tiktok_id,
    looks_like_vn_phone,
    mapping_spec_chosen,
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
        self.assertFalse(looks_like_facebook_uid("10001"))
        self.assertFalse(looks_like_facebook_uid("100012345"))
        self.assertFalse(looks_like_facebook_uid("09012223331"))

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
    def test_generic_headers_still_take_name_phone_address(self) -> None:
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
        self.assertIsNone(mapping["facebookUid"])
        self.assertIsNone(mapping["tiktokId"])

    def test_unlabeled_uid_column_is_not_guessed(self) -> None:
        headers = ["A", "B"]
        values = [
            ["1000123456789", "1000222333444"],
            ["Nguyễn Lan", "Trần Minh"],
        ]
        mapping = suggest_mapping(headers, values)
        self.assertIsNone(mapping["facebookUid"])
        self.assertEqual(mapping["name"], 1)
        self.assertIsNone(mapping["phone"])

    def test_header_uid_maps_facebook_even_when_cells_are_phones(self) -> None:
        headers = ["uid", "name"]
        values = [
            ["0901222333", "0912345678", "0321111222"],
            ["Lan", "Minh", "Hoa"],
        ]
        mapping = suggest_mapping(headers, values)
        self.assertEqual(mapping["facebookUid"], 0)
        self.assertEqual(mapping["name"], 1)
        self.assertIsNone(mapping["phone"])

    def test_gender_birthday_and_link_columns_are_skipped(self) -> None:
        headers = [
            "Tên khách",
            "Số điện thoại",
            "Địa chỉ",
            "UID Facebook",
            "Giới tính",
            "Ngày sinh",
            "Link Facebook",
        ]
        values = [
            ["Nguyễn Lan"],
            ["0901222333"],
            ["Huế"],
            ["1000123456789"],
            ["female"],
            ["05/07/2024"],
            ["https://fb.com/1000123456789"],
        ]
        mapping, details = assign_fields(headers, values)
        self.assertEqual(mapping["name"], 0)
        self.assertEqual(mapping["phone"], 1)
        self.assertEqual(mapping["address"], 2)
        self.assertEqual(mapping["facebookUid"], 3)
        self.assertIsNone(mapping["tiktokId"])
        self.assertNotIn(4, [item["index"] for item in details.values()])
        self.assertNotIn(5, [item["index"] for item in details.values()])
        self.assertNotIn(6, [item["index"] for item in details.values()])
        self.assertIsNone(header_field("Link Facebook"))
        self.assertIsNone(header_field("Order date"))
        self.assertIsNone(header_field("Ngày sinh"))
        self.assertEqual(header_field("UID Facebook"), "facebookUid")
        self.assertEqual(header_field("Nội dung hàng mua 2"), "orderContent")

    def test_multiple_address_columns_become_separate_lines(self) -> None:
        from control_plane.excel_import import mapping_from_form, rows_from_files

        payload = write_xlsx(
            ["Tên khách", "SĐT", "Số nhà", "Phường", "Tỉnh"],
            [["Nguyễn Lan", "0901222333", "12 Lê Lợi", "Quận 1", "TP.HCM"]],
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
                "phone": {"index": 1, "header": "SĐT"},
                "address": {
                    "columns": [
                        {"index": 2, "header": "Số nhà"},
                        {"index": 3, "header": "Phường"},
                        {"index": 4, "header": "Tỉnh"},
                    ]
                },
            }
        )
        incoming, _reports = rows_from_files([("nhieu-dia-chi.xlsx", payload)], mapping)
        self.assertEqual(incoming[0]["name"], "Nguyễn Lan")
        self.assertEqual(incoming[0]["address"], "12 Lê Lợi\nQuận 1\nTP.HCM")

    def test_multiple_order_content_columns_join_as_lines(self) -> None:
        from control_plane.excel_import import mapping_from_form, rows_from_files

        payload = write_xlsx(
            ["Tên khách", "SĐT", "Hàng mua", "Sản phẩm", "Size"],
            [["Nguyễn Lan", "0901222333", "Nước mắm", "chai 500ml", "size 41"]],
        )
        mapping, details = assign_fields(
            ["Tên khách", "Hàng mua", "Sản phẩm", "Size"],
            [["Nguyễn Lan"], ["Nước mắm"], ["chai 500ml"], ["size 41"]],
        )
        self.assertEqual(mapping["name"], 0)
        self.assertEqual(mapping["orderContent"], 1)
        self.assertEqual(details["orderContent"]["indexes"], [1, 2])
        incoming, _reports = rows_from_files(
            [("hang.xlsx", payload)],
            mapping_from_form(
                {
                    "name": {"index": 0, "header": "Tên khách"},
                    "phone": {"index": 1, "header": "SĐT"},
                    "orderContent": {
                        "columns": [
                            {"index": 2, "header": "Hàng mua"},
                            {"index": 3, "header": "Sản phẩm"},
                        ]
                    },
                }
            ),
        )
        self.assertEqual(incoming[0]["orderContent"], "Nước mắm\nchai 500ml")

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

    def test_unlabeled_core_columns_are_filled_from_values(self) -> None:
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
        self.assertIsNone(mapping["adminAddress"])
        self.assertFalse(details["name"]["fromHeader"])
        self.assertFalse(details["phone"]["fromHeader"])

    def test_receiver_headers_map_name_phone_address(self) -> None:
        self.assertEqual(header_field("Tên người nhận"), "name")
        self.assertEqual(header_field("SĐT người nhận"), "phone")
        self.assertEqual(header_field("Địa chỉ người nhận"), "address")
        self.assertEqual(header_field("Tên KH"), "name")
        self.assertEqual(header_field("SĐT KH"), "phone")
        mapping = suggest_mapping(
            ["Tên người nhận", "SĐT người nhận", "Địa chỉ chuẩn"],
            [[], [], []],
        )
        self.assertEqual(mapping["name"], 0)
        self.assertEqual(mapping["phone"], 1)
        self.assertEqual(mapping["address"], 2)

    def test_data_row_is_not_used_as_header(self) -> None:
        self.assertIsNone(header_field("0901222333"))
        self.assertIsNone(
            header_field("chuẩn57, Bùi Xương Trạch, P. Khương Đình, Q. Thanh Xuân, Tp. Hà Nội")
        )
        self.assertIsNone(header_field("lô 41 đường số 2, KCN An Đồn, Sơn Trà, Đà Nẵng"))
        payload = write_xlsx(
            ["Nguyễn Lan", "0901222333", "12 Lê Lợi, Quận 1, TP.HCM"],
            [["Trần Minh", "0901444555", "Ha Noi"]],
        )
        preview = preview_table("khong-tieu-de.xlsx", payload)
        self.assertEqual(preview["rowCount"], 2)
        self.assertEqual(preview["suggested"]["name"], 0)
        self.assertEqual(preview["suggested"]["phone"], 1)
        self.assertEqual(preview["suggested"]["address"], 2)

    def test_complete_core_mapping_fills_name_and_phone(self) -> None:
        headers = ["Cột A", "Cột B", "Cột C"]
        values = [
            ["Nguyễn Lan", "Trần Minh"],
            ["0901222333", "0901444555"],
            ["12 Lê Lợi, Quận 1, TP.HCM", "lô 41 đường số 2, Sơn Trà, Đà Nẵng"],
        ]
        completed = complete_core_mapping(
            {"address": {"index": 2, "header": "lô 41 đường số 2, Sơn Trà, Đà Nẵng"}},
            headers,
            values,
        )
        self.assertTrue(mapping_spec_chosen(completed["name"]))
        self.assertTrue(mapping_spec_chosen(completed["phone"]))
        self.assertEqual(completed["name"]["index"], 0)
        self.assertEqual(completed["phone"]["index"], 1)
        self.assertEqual(completed["address"]["index"], 2)

    def test_complete_core_mapping_keeps_chosen_name(self) -> None:
        completed = complete_core_mapping(
            {"name": {"index": 2, "header": "Cột C"}, "phone": {}, "address": {}},
            ["A", "B", "Cột C"],
            [["0901222333"], ["Huế"], ["Nguyễn Lan"]],
        )
        self.assertEqual(completed["name"]["index"], 2)

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
