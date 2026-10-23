"""Gộp khách trùng: điền ô trống, giữ ô đã có."""

from __future__ import annotations

import unittest

from control_plane.customers import (
    clean_facebook_uid,
    clean_phone,
    clean_tiktok_id,
    fold_customers,
    lookup_by_phones,
    matches_query,
    phones_from_text,
)
from control_plane.excel_import import (
    mapping_from_form,
    preview_table,
    rows_from_files,
    write_xlsx,
)


class CustomerCleanTests(unittest.TestCase):
    def test_phone_strips_country_code(self) -> None:
        self.assertEqual(clean_phone("+84 901 234 567"), "0901234567")
        self.assertEqual(clean_phone("0901-234-567"), "0901234567")
        self.assertEqual(clean_phone("0084901234567"), "0901234567")
        self.assertEqual(clean_phone("084901234567"), "0901234567")
        self.assertEqual(clean_phone("84901234567"), "0901234567")
        self.assertEqual(clean_phone("abc"), "")

    def test_facebook_uid_from_url_and_scientific(self) -> None:
        self.assertEqual(clean_facebook_uid("https://www.facebook.com/profile.php?id=1000123456789"), "1000123456789")
        self.assertEqual(clean_facebook_uid("1.000123456789e+12"), "1000123456789")
        self.assertEqual(clean_facebook_uid("1000123456789"), "1000123456789")

    def test_tiktok_id_from_url(self) -> None:
        self.assertEqual(clean_tiktok_id("https://www.tiktok.com/@ban.gach"), "ban.gach")
        self.assertEqual(clean_tiktok_id("@ban.gach"), "ban.gach")


class CustomerMergeTests(unittest.TestCase):
    def test_later_file_fills_missing_fields(self) -> None:
        first, stats, _removed = fold_customers(
            [],
            [{"name": "Nguyễn A", "phone": "0901234567"}],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(stats["created"], 1)
        second, stats, _removed = fold_customers(
            first,
            [{"phone": "0901234567", "facebookUid": "1000123456789", "tiktokId": "ban.gach"}],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(second[0]["name"], "Nguyễn A")
        self.assertEqual(second[0]["phone"], "0901234567")
        self.assertEqual(second[0]["facebookUid"], "1000123456789")
        self.assertEqual(second[0]["tiktokId"], "ban.gach")

    def test_existing_values_are_kept(self) -> None:
        stored, _stats, _removed = fold_customers(
            [],
            [{"name": "Nguyễn A", "phone": "0901234567", "address": "Hà Nội"}],
            "2026-01-01T00:00:00+00:00",
        )
        again, stats, _removed = fold_customers(
            stored,
            [{"name": "Tên khác", "phone": "0901234567", "address": "Sài Gòn", "facebookUid": "1"}],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(again[0]["name"], "Nguyễn A")
        self.assertEqual(again[0]["address"], "Hà Nội")
        self.assertEqual(again[0]["facebookUid"], "1")

    def test_same_name_different_phones_stay_apart(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "sơn(hg)", "phone": "0338174222", "address": "Đà Nẵng"},
                {"name": "sơn(hg)", "phone": "0918028684", "address": "Hà Nội"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(len(folded), 2)
        self.assertEqual(stats["created"], 2)
        self.assertEqual({row["phone"] for row in folded}, {"0338174222", "0918028684"})

    def test_same_phone_merges_even_when_names_differ(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "Sơn", "phone": "0338174222", "address": "Đà Nẵng"},
                {"name": "sơn(hg)", "phone": "0338174222", "address": "Hà Nội"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(len(folded), 1)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(folded[0]["phone"], "0338174222")
        self.assertEqual(folded[0]["name"], "Sơn")

    def test_same_name_and_address_without_phone_stay_apart(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "Lan", "address": "Huế"},
                {"name": "Lan", "address": "Huế"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(len(folded), 2)
        self.assertEqual(stats["created"], 2)

    def test_facebook_or_tiktok_alone_does_not_merge(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "Trần Tùng", "facebookUid": "222", "tiktokId": "tung.ban"},
                {"name": "Tùng", "facebookUid": "222", "tiktokId": "tung.ban"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(len(folded), 2)
        self.assertEqual(stats["created"], 2)

    def test_unchanged_counts_only_rows_in_this_import(self) -> None:
        first, _stats, _removed = fold_customers(
            [],
            [
                {"name": "Lan", "phone": "0901222333"},
                {"name": "Minh", "phone": "0901444555"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        _again, stats, _removed = fold_customers(
            first,
            [{"name": "Lan", "phone": "0901222333"}],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(stats["unchanged"], 1)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(stats["total"], 2)

    def test_empty_rows_are_skipped(self) -> None:
        folded, stats, _removed = fold_customers([], [{"name": "   "}, {}], "2026-01-01T00:00:00+00:00")
        self.assertEqual(folded, [])
        self.assertEqual(stats["skipped"], 2)

    def test_phone_list_lookup_keeps_order_and_misses(self) -> None:
        phones, parsed = phones_from_text(
            "0901222333\n+84 901 222 333\nabc\n0901444555\n0901999888"
        )
        self.assertEqual(phones, ["0901222333", "0901444555", "0901999888"])
        self.assertEqual(parsed["invalid"], 1)
        self.assertEqual(parsed["duplicate"], 1)
        stored, _stats, _removed = fold_customers(
            [],
            [
                {"name": "Lan", "phone": "0901222333", "address": "Huế"},
                {"name": "Minh", "phone": "0901444555"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        items, stats = lookup_by_phones(stored, phones)
        self.assertEqual(stats["found"], 2)
        self.assertEqual(stats["missing"], 1)
        self.assertEqual(items[0]["customer"]["name"], "Lan")
        self.assertEqual(items[1]["customer"]["name"], "Minh")
        self.assertFalse(items[2]["found"])
        self.assertEqual(items[2]["query"], "0901999888")

    def test_search_matches_any_field(self) -> None:
        row = {"name": "Nguyễn A", "phone": "0901234567", "address": "Hà Nội", "facebookUid": "1", "tiktokId": "ban"}
        self.assertTrue(matches_query(row, "hà nội"))
        self.assertTrue(matches_query(row, "0901"))
        self.assertFalse(matches_query(row, "xyz"))


class ExcelMappingTests(unittest.TestCase):
    def test_preview_suggests_vietnamese_headers(self) -> None:
        payload = write_xlsx(
            ["Tên khách", "Số điện thoại khách", "Địa chỉ khách", "UID Facebook", "ID TikTok"],
            [["Lan", "0901222333", "Huế", "10001", "lan.hue"]],
        )
        preview = preview_table("danh-sach.xlsx", payload)
        self.assertEqual(preview["rowCount"], 1)
        self.assertEqual(preview["suggested"]["name"], 0)
        self.assertEqual(preview["suggested"]["phone"], 1)
        self.assertEqual(preview["suggested"]["address"], 2)
        self.assertEqual(preview["suggested"]["facebookUid"], 3)
        self.assertEqual(preview["suggested"]["tiktokId"], 4)

    def test_same_mapping_applies_to_every_file_in_batch(self) -> None:
        first = write_xlsx(
            ["Tên khách", "SĐT", "UID Facebook"],
            [["Minh", "0901000001", "111"]],
        )
        second = write_xlsx(
            ["Tên khách", "SĐT", "UID Facebook"],
            [["Minh", "0901000001", ""], ["Hoa", "0901000002", "222"]],
        )
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên khách"},
                "phone": {"index": 1, "header": "SĐT"},
                "facebookUid": {"index": 2, "header": "UID Facebook"},
            }
        )
        incoming, reports = rows_from_files(
            [("mot.xlsx", first), ("hai.xlsx", second)],
            mapping,
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(sum(item["rows"] for item in reports), 3)
        folded, stats, _removed = fold_customers([], incoming, "2026-01-01T00:00:00+00:00")
        self.assertEqual(stats["created"], 2)
        minh = next(row for row in folded if row["name"] == "Minh")
        self.assertEqual(minh["facebookUid"], "111")
        self.assertEqual(minh["phone"], "0901000001")

    def test_complementary_files_map_extra_headers_without_stealing(self) -> None:
        first = write_xlsx(
            ["Tên khách", "Số điện thoại khách", "Địa chỉ khách"],
            [["Lan", "0901222333", "Huế"]],
        )
        second = write_xlsx(
            ["Tên khách", "Số điện thoại khách", "UID Facebook", "ID TikTok"],
            [["Lan", "0901222333", "10001", "lan.hue"]],
        )
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên khách"},
                "phone": {"index": 1, "header": "Số điện thoại khách"},
                "address": {"index": 2, "header": "Địa chỉ khách"},
                "facebookUid": {"index": None, "header": "UID Facebook"},
                "tiktokId": {"index": None, "header": "ID TikTok"},
            }
        )
        incoming, _reports = rows_from_files([("a.xlsx", first), ("b.xlsx", second)], mapping)
        folded, stats, _removed = fold_customers([], incoming, "2026-01-01T00:00:00+00:00")
        self.assertEqual(stats["created"], 1)
        self.assertEqual(folded[0]["name"], "Lan")
        self.assertEqual(folded[0]["address"], "Huế")
        self.assertEqual(folded[0]["facebookUid"], "10001")
        self.assertEqual(folded[0]["tiktokId"], "lan.hue")

    def test_missing_header_falls_back_to_same_column_index(self) -> None:
        first = write_xlsx(["Tên khách", "SĐT"], [["An", "0901999888"]])
        second = write_xlsx(["Name", "Phone"], [["An", "0901999888"], ["Bình", "0901777666"]])
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên khách"},
                "phone": {"index": 1, "header": "SĐT"},
            }
        )
        incoming, _reports = rows_from_files([("a.xlsx", first), ("b.xlsx", second)], mapping)
        folded, stats, _removed = fold_customers([], incoming, "2026-01-01T00:00:00+00:00")
        self.assertEqual(stats["created"], 2)
        self.assertEqual({row["name"] for row in folded}, {"An", "Bình"})


if __name__ == "__main__":
    unittest.main()
