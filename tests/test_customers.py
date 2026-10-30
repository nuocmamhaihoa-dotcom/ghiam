"""Gộp khách trùng: điền ô trống, thông tin khác thêm dòng trong cùng ô."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from control_plane import db
from control_plane.customers import (
    clean_cod_amount,
    clean_facebook_uid,
    clean_phone,
    clean_tiktok_id,
    extract_phone,
    has_phone,
    is_facebook_id_digits,
    phone_lookup_keys,
    expand_scientific,
    fold_customers,
    ingest_customers,
    combine_phone_group,
    lookup_by_phone_map,
    lookup_by_phones,
        phone_groups_from_text,
        real_lookup_phones,
    matches_query,
    phones_from_text,
    repair_customer_row,
    repair_stored_customers,
    clean_address,
    clear_province_only_addresses,
    dedupe_exact_address,
    dedupe_exact_addresses,
    rewrite_stored_addresses,
    clear_admin_or_short_addresses,
    clear_short_admin_only_addresses,
    prefer_import_address,
    restore_stored_addresses,
    should_restore_address,
    strip_admin_or_short_address,
    strip_province_only_address,
)
from control_plane.address_restore import (
    ImportAddressStore,
    collect_from_customer_db,
    collect_import_addresses,
)
from control_plane.vietnam_places import cell_is_short_admin_only
from control_plane.excel_import import (
    SharedStringStore,
    _combine_sheets,
    _fill_shared_strings,
    _shared_strings,
    iter_mapped_file,
    iter_xlsx_rows,
    mapping_from_form,
    phones_from_files,
    phone_from_cell,
    phone_from_row_cells,
    preview_table,
    rows_from_files,
    rows_from_table,
    write_xlsx,
    write_xlsx_sheets,
    write_xlsx_to_path,
    _usable_mapped_phone,
)
from control_plane.settings import settings


class CustomerCleanTests(unittest.TestCase):
    def test_phone_strips_country_code(self) -> None:
        self.assertEqual(clean_phone("+84 901 234 567"), "0901234567")
        self.assertEqual(clean_phone("0901-234-567"), "0901234567")
        self.assertEqual(clean_phone("0084901234567"), "0901234567")
        self.assertEqual(clean_phone("084901234567"), "0901234567")
        self.assertEqual(clean_phone("84901234567"), "0901234567")
        self.assertEqual(clean_phone("00352548360"), "0352548360")
        self.assertEqual(clean_phone("901234567"), "0901234567")
        self.assertEqual(clean_phone("abc"), "")
        self.assertIn("0901234567", phone_lookup_keys("84901234567"))
        self.assertIn("84901234567", phone_lookup_keys("0901234567"))
        self.assertIn("0901234567", phone_lookup_keys("901234567"))

    def test_extract_phone_keeps_short_and_skips_facebook(self) -> None:
        self.assertEqual(extract_phone("0901222333"), "0901222333")
        self.assertEqual(extract_phone("901222333"), "0901222333")
        self.assertEqual(extract_phone("901222333.0"), "0901222333")
        self.assertEqual(extract_phone("LH: 0901 222 333 gặp chiều"), "0901222333")
        self.assertEqual(extract_phone("09012223331"), "09012223331")
        self.assertEqual(extract_phone("1000123456789"), "")
        self.assertEqual(extract_phone("1.000123456789e+12"), "")
        self.assertFalse(is_facebook_id_digits("0901222333"))
        self.assertFalse(is_facebook_id_digits("901222333"))
        self.assertFalse(is_facebook_id_digits("10001"))
        self.assertFalse(is_facebook_id_digits("09012223331"))
        self.assertTrue(is_facebook_id_digits("1000123456789"))
        self.assertTrue(has_phone({"phone": "09012223331"}))
        self.assertTrue(has_phone({"phone": "901222333"}))
        self.assertFalse(has_phone({"phone": "1000123456789"}))
        self.assertFalse(has_phone({"phone": "1.000123456789e+12"}))

    def test_facebook_uid_from_url_and_scientific(self) -> None:
        self.assertEqual(clean_facebook_uid("https://www.facebook.com/profile.php?id=1000123456789"), "1000123456789")
        self.assertEqual(clean_facebook_uid("1.000123456789e+12"), "1000123456789")
        self.assertEqual(clean_facebook_uid("1000123456789"), "1000123456789")
        self.assertEqual(expand_scientific("1.0001234567890123e+16"), "10001234567890123")
        self.assertEqual(expand_scientific("901222333.0"), "901222333")
        self.assertEqual(expand_scientific("9.01222333E8"), "901222333")
        self.assertEqual(clean_facebook_uid("1.0001234567890123e+16"), "10001234567890123")

    def test_cod_reads_k_and_plain_numbers(self) -> None:
        self.assertEqual(clean_cod_amount("230k"), "230000")
        self.assertEqual(clean_cod_amount("1.5tr"), "1500000")
        self.assertEqual(clean_cod_amount("230.000đ"), "230000")
        self.assertEqual(clean_cod_amount("0901222333"), "")
        self.assertEqual(clean_cod_amount("480000"), "480000")

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
            [
                {
                    "phone": "0901234567",
                    "facebookUid": "1000123456789",
                    "tiktokId": "ban.gach",
                    "adminAddress": "Thừa Thiên Huế",
                    "orderContent": "Giày size 41",
                    "codAmount": "230k",
                }
            ],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(second[0]["name"], "Nguyễn A")
        self.assertEqual(second[0]["phone"], "0901234567")
        self.assertEqual(second[0]["facebookUid"], "1000123456789")
        self.assertEqual(second[0]["tiktokId"], "ban.gach")
        self.assertEqual(second[0]["adminAddress"], "Thừa Thiên Huế")
        self.assertEqual(second[0]["orderContent"], "Giày size 41")
        self.assertEqual(second[0]["codAmount"], "230000")

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
        self.assertEqual(again[0]["name"], "Nguyễn A\nTên khác")
        self.assertEqual(again[0]["address"], "Hà Nội\nSài Gòn")
        self.assertEqual(again[0]["facebookUid"], "1")
        same, same_stats, _removed = fold_customers(
            again,
            [{"name": "Nguyễn A", "phone": "0901234567", "address": "Sài Gòn"}],
            "2026-01-03T00:00:00+00:00",
        )
        self.assertEqual(same_stats["unchanged"], 1)
        self.assertEqual(same_stats["updated"], 0)
        self.assertEqual(same[0]["name"], "Nguyễn A\nTên khác")
        self.assertEqual(same[0]["address"], "Hà Nội\nSài Gòn")
        added, added_stats, _removed = fold_customers(
            same,
            [{"name": "Tên khác", "phone": "0901234567", "address": "Đà Nẵng"}],
            "2026-01-04T00:00:00+00:00",
        )
        self.assertEqual(added_stats["updated"], 1)
        self.assertEqual(added[0]["name"], "Nguyễn A\nTên khác")
        self.assertEqual(added[0]["address"], "Hà Nội\nSài Gòn\nĐà Nẵng")

    def test_later_import_appends_cod_and_fills_blank_fields(self) -> None:
        stored, _stats, _removed = fold_customers(
            [],
            [{"name": "Lan", "phone": "0901555666", "codAmount": "230k", "facebookUid": "100"}],
            "2026-01-01T00:00:00+00:00",
        )
        again, stats, _removed = fold_customers(
            stored,
            [
                {
                    "name": "Lan",
                    "phone": "0901555666",
                    "codAmount": "120000",
                    "facebookUid": "100",
                    "tiktokId": "@ban.gach",
                    "address": "Huế",
                }
            ],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(again[0]["name"], "Lan")
        self.assertEqual(again[0]["codAmount"], "230000\n120000")
        self.assertEqual(again[0]["facebookUid"], "100")
        self.assertEqual(again[0]["tiktokId"], "ban.gach")
        self.assertEqual(again[0]["address"], "Huế")
        same, same_stats, _removed = fold_customers(
            again,
            [{"phone": "0901555666", "codAmount": "230000", "tiktokId": "ban.gach", "address": ""}],
            "2026-01-03T00:00:00+00:00",
        )
        self.assertEqual(same_stats["unchanged"], 1)
        self.assertEqual(same_stats["updated"], 0)
        self.assertEqual(same[0]["codAmount"], "230000\n120000")
        self.assertEqual(same[0]["address"], "Huế")
        self.assertEqual(same[0]["tiktokId"], "ban.gach")

    def test_one_cell_keeps_two_addresses_and_composes_one_address(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {
                    "name": "Lan\nHoa",
                    "phone": "0901222333",
                    "address": "Hà Nội\nSài Gòn",
                    "codAmount": "230k\n120000",
                }
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(stats["created"], 1)
        self.assertEqual(folded[0]["name"], "Lan\nHoa")
        self.assertEqual(folded[0]["address"], "Hà Nội\nSài Gòn")
        self.assertEqual(folded[0]["codAmount"], "230000\n120000")
        parts, part_stats, _removed = fold_customers(
            [],
            [{"name": "Lan", "phone": "0901555666", "address": "12 Lê Lợi\nQuận 1\nTP.HCM"}],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(part_stats["created"], 1)
        self.assertEqual(parts[0]["address"], "12 Lê Lợi, Quận 1, TP.HCM")
        again, again_stats, _removed = fold_customers(
            folded,
            [{"name": "hoa", "phone": "84901222333", "address": "Sài Gòn\nHà Nội", "codAmount": "120000\n230000"}],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(again_stats["unchanged"], 1)
        self.assertEqual(again[0]["name"], "Lan\nHoa")
        self.assertEqual(again[0]["address"], "Hà Nội\nSài Gòn")
        self.assertEqual(again[0]["codAmount"], "230000\n120000")
        added, added_stats, _removed = fold_customers(
            again,
            [{"phone": "0901222333", "address": "12 Lê Lợi\nQuận 1\nTP.HCM"}],
            "2026-01-03T00:00:00+00:00",
        )
        self.assertEqual(added_stats["updated"], 1)
        self.assertEqual(added[0]["address"], "Hà Nội\nSài Gòn\n12 Lê Lợi, Quận 1, TP.HCM")

    def test_blank_address_keeps_stored_and_admin_still_appends(self) -> None:
        stored = [
            {
                "id": "a",
                "name": "Lan",
                "phone": "0901555666",
                "address": "12 Lê Lợi\nQuận 1\nTP.HCM",
                "adminAddress": "Phường 1",
                "orderContent": "Áo",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:00:00+00:00",
            }
        ]
        kept, stats, _removed = fold_customers(
            stored,
            [{"phone": "0901555666", "address": "", "adminAddress": "Quận 1", "orderContent": "Quần"}],
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(kept[0]["name"], "Lan")
        self.assertEqual(kept[0]["address"], "12 Lê Lợi\nQuận 1\nTP.HCM")
        self.assertEqual(kept[0]["adminAddress"], "Phường 1\nQuận 1")
        self.assertEqual(kept[0]["orderContent"], "Áo\nQuần")
        replaced, replaced_stats, _removed = fold_customers(
            kept,
            [{"phone": "0901555666", "address": "P.Bến Nghé, 12 Nguyễn Huệ, TP.HCM"}],
            "2026-01-03T00:00:00+00:00",
        )
        self.assertEqual(replaced_stats["updated"], 1)
        self.assertEqual(
            replaced[0]["address"],
            "12 Lê Lợi\nQuận 1\nTP.HCM\n12 Nguyễn Huệ, P.Bến Nghé, TP.HCM",
        )
        self.assertEqual(replaced[0]["adminAddress"], "Phường 1\nQuận 1")

    def test_absorbed_stored_addresses_are_composed(self) -> None:
        folded, stats, removed = fold_customers(
            [
                {
                    "id": "a",
                    "name": "Lan",
                    "phone": "0901111111",
                    "address": "12 Lê Lợi",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                },
                {
                    "id": "b",
                    "name": "Lan",
                    "phone": "84901111111",
                    "address": "Quận 1\nTP.HCM",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                },
            ],
            [],
            "2026-02-01T00:00:00+00:00",
        )
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["phone"], "0901111111")
        self.assertEqual(folded[0]["address"], "12 Lê Lợi\nQuận 1\nTP.HCM")
        self.assertEqual(removed, ["b"])

    def test_same_phone_84_and_0_in_one_batch_merge(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "Lan", "phone": "0901222333", "address": "Huế"},
                {"name": "Tên khác", "phone": "84901222333", "address": "Đà Nẵng"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["name"], "Lan\nTên khác")
        self.assertEqual(folded[0]["phone"], "0901222333")
        self.assertEqual(folded[0]["address"], "Huế\nĐà Nẵng")

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
        self.assertEqual(folded[0]["name"], "Sơn\nsơn(hg)")
        self.assertEqual(folded[0]["address"], "Đà Nẵng\nHà Nội")

    def test_rows_without_phone_are_skipped(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "Lan", "address": "Huế"},
                {"name": "Lan", "address": "Huế"},
                {"name": "Minh", "phone": "0901444555"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(len(folded), 1)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(folded[0]["phone"], "0901444555")

    def test_facebook_or_tiktok_alone_does_not_merge(self) -> None:
        folded, stats, _removed = fold_customers(
            [],
            [
                {"name": "Trần Tùng", "phone": "0901111111", "facebookUid": "222", "tiktokId": "tung.ban"},
                {"name": "Tùng", "phone": "0901222222", "facebookUid": "222", "tiktokId": "tung.ban"},
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
        lan = next(row for row in _again if row["phone"] == "0901222333")
        minh = next(row for row in _again if row["phone"] == "0901444555")
        self.assertFalse(lan["write"])
        self.assertFalse(minh["write"])
        self.assertEqual(_removed, [])

    def test_empty_rows_are_skipped(self) -> None:
        folded, stats, _removed = fold_customers([], [{"name": "   "}, {}], "2026-01-01T00:00:00+00:00")
        self.assertEqual(folded, [])
        self.assertEqual(stats["skipped"], 2)

    def test_phone_list_lookup_keeps_order_and_misses(self) -> None:
        phones, parsed = phones_from_text(
            "0901222333\n+84 901 222 333\nabc\n0901444555\n0901999888"
        )
        self.assertEqual(phones, ["0901222333", "0901222333", "0901444555", "0901999888"])
        self.assertEqual(parsed["raw"], 5)
        self.assertEqual(parsed["invalid"], 1)
        self.assertEqual(parsed["duplicate"], 1)
        phones = ["0901222333", "0901444555", "0901999888"]
        stored, _stats, _removed = fold_customers(
            [],
            [
                {"name": "Lan", "phone": "0901222333", "address": "Huế", "adminAddress": "Thừa Thiên Huế"},
                {"name": "Minh", "phone": "0901444555", "address": "Đà Nẵng"},
            ],
            "2026-01-01T00:00:00+00:00",
        )
        items, stats = lookup_by_phones(stored, phones)
        self.assertEqual(stats["found"], 2)
        self.assertEqual(stats["missing"], 1)
        self.assertEqual([item["customer"]["name"] for item in items], ["Lan", "Minh"])
        self.assertEqual(items[1]["customer"]["address"], "Đà Nẵng")
        self.assertEqual(items[1]["customer"]["adminAddress"], "")
        items84, stats84 = lookup_by_phones(stored, ["84901222333"])
        self.assertEqual(stats84["found"], 1)
        self.assertEqual(items84[0]["customer"]["name"], "Lan")
        mapped_items, mapped_stats = lookup_by_phone_map(
            {
                "0901222333": [stored[0]],
                "0901444555": [stored[1]],
                "0901999888": [],
            },
            phones,
        )
        self.assertEqual(mapped_stats["found"], 2)
        self.assertEqual([item["customer"]["name"] for item in mapped_items], ["Lan", "Minh"])
        self.assertEqual(mapped_stats["missing"], 1)
        admin_only, admin_stats = lookup_by_phone_map(
            {"0901555666": [{"id": "hoa", "name": "Hoa", "phone": "0901555666", "address": "", "adminAddress": "Hải Châu"}]},
            ["0901555666"],
        )
        self.assertEqual(admin_stats["found"], 1)
        self.assertEqual(admin_stats["missing"], 0)
        self.assertEqual(admin_only[0]["customer"]["adminAddress"], "Hải Châu")
        blank_and_street, partial_stats = lookup_by_phone_map(
            {
                "0901666777": [
                    {"id": "trong", "name": "Trang", "phone": "0901666777", "address": "", "adminAddress": ""},
                    {"id": "hoa", "name": "Hoa", "phone": "0901666777", "address": "15 Hùng Vương", "adminAddress": ""},
                ]
            },
            ["0901666777"],
        )
        self.assertEqual(partial_stats["found"], 1)
        self.assertEqual([item["customer"]["name"] for item in blank_and_street], ["Hoa"])

    def test_one_line_with_two_phones_is_one_customer(self) -> None:
        groups, parsed = phone_groups_from_text(
            "0901000001 - 0901000002\n0901000003\n0901000001\nabc"
        )
        self.assertEqual(
            [real_lookup_phones(group) for group in groups],
            [["0901000001", "0901000002"], ["0901000003"], ["0901000001"], []],
        )
        self.assertEqual(parsed["duplicate"], 1)
        self.assertEqual(parsed["invalid"], 1)
        self.assertEqual(parsed["raw"], 4)
        flat, flat_parsed = phones_from_text("0901000001 - 0901000002\n0901000003")
        self.assertEqual(flat, ["0901000001", "0901000002", "0901000003"])
        self.assertEqual(flat_parsed["unique"], 3)

        def ready(name: str, phone: str, street: str, admin: str) -> dict:
            return {
                "query": phone,
                "found": True,
                "customer": {
                    "id": name,
                    "name": name,
                    "phone": phone,
                    "address": street,
                    "adminAddress": admin,
                },
            }

        different = combine_phone_group(
            ["0901000001", "0901000002"],
            {
                "0901000001": [ready("An", "0901000001", "12 Lê Lợi", "Phường Phú Hội")],
                "0901000002": [ready("Bình", "0901000002", "45 Nguyễn Huệ", "Phường Bến Nghé")],
            },
        )
        self.assertEqual(different["query"], "0901000001\n0901000002")
        self.assertEqual(different["customer"]["address"], "12 Lê Lợi\n45 Nguyễn Huệ")
        self.assertEqual(
            different["resultAddress"],
            "12 Lê Lợi | Phường Phú Hội\n45 Nguyễn Huệ | Phường Bến Nghé",
        )
        same = combine_phone_group(
            ["0901000003", "0901000004"],
            {
                "0901000003": [ready("Chi", "0901000003", "88 Trần Phú", "Quận 1")],
                "0901000004": [ready("Dung", "0901000004", "88 Trần Phú", "Quận 1")],
            },
        )
        self.assertEqual(same["query"], "0901000003\n0901000004")
        self.assertEqual(same["customer"]["address"], "88 Trần Phú\n88 Trần Phú")
        self.assertEqual(same["resultAddress"], "88 Trần Phú | Quận 1\n88 Trần Phú | Quận 1")
        missing = combine_phone_group(["0901000005"], {})
        self.assertEqual(missing["customer"]["address"], ".")
        self.assertEqual(missing["resultAddress"], ".")
        self.assertFalse(missing["found"])
        mixed = combine_phone_group(
            ["0901000006", "0988000111"],
            {"0901000006": [ready("Em", "0901000006", "9 Bà Triệu", "Phường Trần Hưng Đạo")]},
        )
        self.assertEqual(mixed["query"], "0901000006")
        self.assertEqual(mixed["customer"]["address"], "9 Bà Triệu")
        self.assertEqual(mixed["resultAddress"], "9 Bà Triệu | Phường Trần Hưng Đạo")
        self.assertNotIn("0988000111", mixed["query"])
        shared = combine_phone_group(
            ["0901000001", "0901000002", "0901000005"],
            {
                "0901000001": [ready("An", "0901000001", "12 Lê Lợi", "Phường Phú Hội")],
                "0901000002": [ready("Bình", "0901000002", "12 Lê Lợi", "Phường Phú Hội")],
            },
        )
        self.assertEqual(shared["query"], "0901000001\n0901000002")
        self.assertEqual(shared["customer"]["address"], "12 Lê Lợi\n12 Lê Lợi")
        self.assertEqual(shared["customer"]["name"], "An\nBình")
        self.assertEqual(shared["resultAddress"], "12 Lê Lợi | Phường Phú Hội\n12 Lê Lợi | Phường Phú Hội")
        an = ready("An", "0901000003", "88 Trần Phú", "Quận 1")
        an["customer"]["orderContent"] = "Áo"
        an["customer"]["codAmount"] = "100000"
        dung = ready("Dung", "0901000004", "88 Trần Phú", "Phường Bến Nghé")
        dung["customer"]["orderContent"] = "Quần"
        dung["customer"]["codAmount"] = "200000"
        choices = combine_phone_group(
            ["0901000003", "0901000004"],
            {"0901000003": [an], "0901000004": [dung]},
        )
        self.assertEqual(choices["query"], "0901000003\n0901000004")
        self.assertEqual(choices["customer"]["address"], "88 Trần Phú\n88 Trần Phú")
        self.assertEqual(choices["customer"]["adminAddress"], "Quận 1\nPhường Bến Nghé")
        self.assertEqual(choices["customer"]["orderContent"], "Áo\nQuần")
        self.assertEqual(choices["customer"]["codAmount"], "100000\n200000")
        self.assertEqual(
            choices["resultAddress"],
            "88 Trần Phú | Quận 1\n88 Trần Phú | Phường Bến Nghé",
        )
        first = ready("An", "0901000001", "12 Lê Lợi", "Phường Phú Hội")
        second = ready("Bình", "0901000001", "45 Nguyễn Huệ", "Phường Bến Nghé")
        second["customer"]["id"] = "b"
        several = combine_phone_group(["0901000001"], {"0901000001": [first, second]})
        self.assertEqual(several["query"], "0901000001\n0901000001")
        self.assertEqual(several["customer"]["address"], "12 Lê Lợi\n45 Nguyễn Huệ")
        self.assertEqual(several["customer"]["name"], "An\nBình")
        self.assertEqual(
            several["resultAddress"],
            "12 Lê Lợi | Phường Phú Hội\n45 Nguyễn Huệ | Phường Bến Nghé",
        )
        street_only = combine_phone_group(
            ["0901000008"],
            {"0901000008": [ready("Hoa", "0901000008", "15 Hùng Vương", "")]},
        )
        self.assertTrue(street_only["found"])
        self.assertEqual(street_only["customer"]["address"], "15 Hùng Vương")
        self.assertEqual(street_only["customer"]["adminAddress"], "")
        self.assertEqual(street_only["resultAddress"], "15 Hùng Vương")
        admin_only = combine_phone_group(
            ["0901000009"],
            {"0901000009": [ready("Khanh", "0901000009", "", "Quận Hải Châu")]},
        )
        self.assertTrue(admin_only["found"])
        self.assertEqual(admin_only["customer"]["address"], "Quận Hải Châu")
        self.assertEqual(admin_only["customer"]["adminAddress"], "Quận Hải Châu")
        self.assertEqual(admin_only["resultAddress"], "Quận Hải Châu")
        blank = ready("Trang", "0901000010", "", "")
        kept = ready("Hoa", "0901000010", "15 Hùng Vương", "")
        kept["customer"]["id"] = "hoa"
        partial = combine_phone_group(["0901000010"], {"0901000010": [blank, kept]})
        self.assertTrue(partial["found"])
        self.assertEqual(partial["customer"]["address"], "15 Hùng Vương")
        self.assertEqual(partial["resultAddress"], "15 Hùng Vương")
        pasted, pasted_parsed = phone_groups_from_text("0901000008\n\nabc\n0901000005")
        self.assertEqual(pasted_parsed["raw"], 4)
        self.assertEqual(len(pasted), 4)
        blank_row = combine_phone_group(pasted[1], {})
        self.assertEqual(blank_row["query"], "")
        self.assertEqual(blank_row["customer"]["address"], ".")
        self.assertFalse(blank_row["found"])
        junk_row = combine_phone_group(pasted[2], {})
        self.assertEqual(junk_row["query"], "abc")
        self.assertEqual(junk_row["resultAddress"], ".")
        both_missing = combine_phone_group(["0901000005", "0988000111"], {})
        self.assertEqual(both_missing["query"], "0901000005 - 0988000111")
        self.assertEqual(both_missing["customer"]["address"], ".")
        wide = ready("An", "0901000001", "12 Lê Lợi\nTầng 2", "Phường Phú Hội")
        other = ready("Bình", "0901000002", "45 Nguyễn Huệ", "Phường Bến Nghé")
        aligned = combine_phone_group(
            ["0901000001", "0901000002"],
            {"0901000001": [wide], "0901000002": [other]},
        )
        self.assertEqual(aligned["customer"]["address"], "12 Lê Lợi | Tầng 2\n45 Nguyễn Huệ")
        self.assertEqual(aligned["customer"]["name"], "An\nBình")
        self.assertEqual(aligned["query"], "0901000001\n0901000002")
        alone = combine_phone_group(["0901000001"], {"0901000001": [wide]})
        self.assertEqual(alone["customer"]["address"], "12 Lê Lợi\nTầng 2")
        edges, edge_parsed = phone_groups_from_text("\n0901000008\n\n")
        self.assertEqual(edge_parsed["raw"], 3)
        self.assertEqual(edge_parsed["invalid"], 2)
        self.assertEqual(len(edges), 3)
        self.assertEqual(real_lookup_phones(edges[0]), [])
        self.assertEqual(real_lookup_phones(edges[1]), ["0901000008"])
        self.assertEqual(real_lookup_phones(edges[2]), [])

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
        self.assertFalse(preview["truncated"])
        self.assertEqual(preview["suggested"]["name"], 0)
        self.assertEqual(preview["suggested"]["phone"], 1)
        self.assertEqual(preview["suggested"]["address"], 2)
        self.assertEqual(preview["suggested"]["facebookUid"], 3)
        self.assertEqual(preview["suggested"]["tiktokId"], 4)

    def test_export_keeps_each_line_in_the_same_cell(self) -> None:
        payload = write_xlsx(
            ["Tên khách", "SĐT", "Địa chỉ", "Giá tiền COD"],
            [["Lan\nHoa", "0901222333", "Hà Nội\nSài Gòn", "230000\n120000"]],
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            styles = archive.read("xl/styles.xml").decode("utf-8")
        self.assertIn("&#10;", sheet)
        self.assertIn('s="1"', sheet)
        self.assertIn('wrapText="1"', styles)
        self.assertIn('customHeight="1"', sheet)
        rows = list(iter_xlsx_rows(payload))
        self.assertEqual(rows[1][0], "Lan\nHoa")
        self.assertEqual(rows[1][2], "Hà Nội\nSài Gòn")
        self.assertEqual(rows[1][3], "230000\n120000")

    def test_shared_strings_stream_skips_phonetic_runs(self) -> None:
        payload = (
            b'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
            b'spreadsheetml/2006/main"><si><t>Lan</t></si><si><r><t>0901</t></r>'
            b"<rPh><t>x</t></rPh></si></sst>"
        )
        self.assertEqual(_shared_strings(payload), ["Lan", "0901"])

    def test_phones_from_files_reads_phone_column_first(self) -> None:
        payload = write_xlsx(
            ["Tên khách", "SĐT", "Ghi chú"],
            [["Lan", "0901222333", "gọi lại"], ["Minh", "0901444555", ""]],
        )
        phones, reports = phones_from_files([("so.xlsx", payload)])
        self.assertEqual(phones, ["0901222333", "0901444555"])
        self.assertEqual(reports[0]["phones"], 2)

    def test_preview_counts_all_rows_without_storing_them(self) -> None:
        payload = write_xlsx(
            ["Tên khách", "SĐT"],
            [[f"K{index}", f"0901{index:06d}"] for index in range(300)],
        )
        preview = preview_table("lon.xlsx", payload)
        self.assertEqual(preview["rowCount"], 300)
        self.assertTrue(preview["truncated"])
        self.assertLessEqual(len(preview["samples"]), 8)
        self.assertLessEqual(len(preview["detectRows"]), 200)

    def test_preview_from_disk_path_matches_bytes(self) -> None:
        payload = write_xlsx(
            ["Tên khách", "SĐT", "Địa chỉ khách"],
            [[f"K{index}", f"0901{index:06d}", f"Huế {index}"] for index in range(80)],
        )
        from_bytes = preview_table("dia.xlsx", payload)
        with tempfile.TemporaryDirectory(prefix="khach-path-") as folder:
            path = Path(folder) / "dia.xlsx"
            path.write_bytes(payload)
            from_path = preview_table("dia.xlsx", path)
        self.assertEqual(from_path["rowCount"], 80)
        self.assertEqual(from_path["rowCount"], from_bytes["rowCount"])
        self.assertEqual(from_path["headers"], from_bytes["headers"])
        self.assertEqual(from_path["samples"], from_bytes["samples"])
        self.assertEqual(from_path["suggested"], from_bytes["suggested"])

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

    def test_bad_file_in_batch_does_not_block_others(self) -> None:
        good = write_xlsx(["Tên khách", "SĐT"], [["Lan", "0901222333"]])
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên khách"},
                "phone": {"index": 1, "header": "SĐT"},
            }
        )
        incoming, reports = rows_from_files(
            [("ok.xlsx", good), ("hong.xlsx", b"not-excel"), ("ok2.xlsx", good)],
            mapping,
        )
        self.assertEqual(len(incoming), 2)
        self.assertEqual(reports[1]["rows"], 0)
        self.assertIn("hỏng", reports[1]["error"])
        self.assertEqual(reports[0]["rows"], 1)
        self.assertEqual(reports[2]["rows"], 1)

    def test_accented_and_plain_headers_match(self) -> None:
        payload = write_xlsx(
            ["Ten khach", "So dien thoai"],
            [["Lan", "0901222333"]],
        )
        mapping = mapping_from_form(
            {
                "name": {"index": None, "header": "Tên khách"},
                "phone": {"index": None, "header": "Số điện thoại"},
            }
        )
        incoming, reports = rows_from_files([("so.xlsx", payload)], mapping)
        self.assertEqual(reports[0]["rows"], 1)
        self.assertEqual(incoming[0]["name"], "Lan")
        self.assertEqual(incoming[0]["phone"], "0901222333")

    def test_later_sheet_keeps_new_warehouse_columns(self) -> None:
        combined = _combine_sheets(
            [
                [["Tên khách", "SĐT"], ["Lan", "0901222333"]],
                [["Tên khách", "UID Facebook"], ["Lan", "1000123456789"]],
            ]
        )
        self.assertEqual(combined[0], ["Tên khách", "SĐT", "UID Facebook"])
        self.assertEqual(combined[1], ["Lan", "0901222333", ""])
        self.assertEqual(combined[2], ["Lan", "", "1000123456789"])

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


class CustomerRepairTests(unittest.TestCase):
    def test_facebook_dump_moves_name_uid_and_place(self) -> None:
        row = {
            "name": "femelelle",
            "phone": "0972863591",
            "address": (
                "1000045227421200\n"
                "84972863591\n"
                "Quackky Nkanan\n"
                "femelelle\n"
                "05/07/2024\n"
                "Tràng Bom\n"
                "https://fb.com/1000045227421200"
            ),
            "adminAddress": "",
            "orderContent": "",
            "codAmount": "",
            "facebookUid": "1000045227421200",
            "tiktokId": "",
        }
        fixed, changed = repair_customer_row(row)
        self.assertTrue(changed)
        self.assertEqual(fixed["name"], "Quackky Nkanan")
        self.assertEqual(fixed["phone"], "0972863591")
        self.assertEqual(fixed["address"], "Tràng Bom")
        self.assertEqual(fixed["facebookUid"], "1000045227421200")

    def test_clean_row_is_left_alone(self) -> None:
        row = {
            "name": "Nguyễn Ngân",
            "phone": "0903449598",
            "address": "Huế",
            "facebookUid": "1000183715327222",
        }
        fixed, changed = repair_customer_row(row)
        self.assertFalse(changed)
        self.assertEqual(fixed["name"], "Nguyễn Ngân")
        self.assertEqual(fixed["address"], "Huế")

    def test_repair_stored_customers_writes_only_dirty_rows(self) -> None:
        import tempfile
        from pathlib import Path

        from control_plane import db

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            db.replace_customers(
                path,
                [
                    {
                        "id": "dump1",
                        "name": "female",
                        "phone": "0985515140",
                        "address": (
                            "100007416894544\n"
                            "84985515140\n"
                            "Thienoy Nguyen\n"
                            "female\n"
                            "Tuy Hòa\n"
                            "https://fb.com/100007416894544"
                        ),
                        "adminAddress": "",
                        "orderContent": "",
                        "codAmount": "",
                        "facebookUid": "100007416894544",
                        "tiktokId": "",
                        "createdAt": "2026-01-01T00:00:00+00:00",
                        "updatedAt": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "id": "ok1",
                        "name": "Nguyễn Ngân",
                        "phone": "0903449598",
                        "address": "Huế",
                        "adminAddress": "",
                        "orderContent": "",
                        "codAmount": "",
                        "facebookUid": "1000183715327222",
                        "tiktokId": "",
                        "createdAt": "2026-01-01T00:00:00+00:00",
                        "updatedAt": "2026-01-01T00:00:00+00:00",
                    },
                ],
                [],
            )
            self.assertEqual(repair_stored_customers(path), 1)
            rows = {row["id"]: row for row in db.list_customers(path)}
            self.assertEqual(rows["dump1"]["name"], "Thienoy Nguyen")
            self.assertEqual(rows["dump1"]["address"], "Tuy Hòa")
            self.assertEqual(rows["dump1"]["facebookUid"], "100007416894544")
            self.assertEqual(rows["ok1"]["name"], "Nguyễn Ngân")
            self.assertEqual(rows["ok1"]["address"], "Huế")


class IngestSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="khach-ingest-")
        self.db_path = Path(self.folder.name) / "server.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_ingest_keeps_every_unique_phone(self) -> None:
        now = "2026-01-01T00:00:00+00:00"
        incoming = ({"name": f"K{index}", "phone": f"0904{index:07d}"} for index in range(4000))
        stats, _preview = ingest_customers(self.db_path, incoming, now, flush_every=80)
        self.assertEqual(stats["incoming"], 4000)
        self.assertEqual(stats["created"], 4000)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["total"], 4000)
        later = (
            {"name": "", "phone": f"0904{index:07d}", "address": f"Huế {index}"}
            for index in range(4000)
        )
        filled, _preview = ingest_customers(self.db_path, later, "2026-01-02T00:00:00+00:00", flush_every=80)
        self.assertEqual(filled["created"], 0)
        self.assertEqual(filled["updated"], 4000)
        self.assertEqual(filled["total"], 4000)
        conn = db.connect(self.db_path)
        try:
            sample = db.customers_by_phone(conn, "09040000012")
        finally:
            conn.close()
        self.assertEqual(len(sample), 1)
        self.assertEqual(sample[0]["name"], "K12")
        self.assertEqual(sample[0]["address"], "Huế 12")

    def test_rewrite_sorts_fragment_addresses_and_keeps_every_customer(self) -> None:
        created = "2026-01-01T00:00:00+00:00"
        later = "2026-06-01T00:00:00+00:00"
        db.replace_customers(
            self.db_path,
            [
                {
                    "id": "a",
                    "name": "Lan",
                    "phone": "0901111111",
                    "address": "12 Lê Lợi\nQuận 1\nTP.HCM",
                    "createdAt": created,
                    "updatedAt": created,
                },
                {
                    "id": "b",
                    "name": "Minh",
                    "phone": "0902222222",
                    "address": "Đà Nẵng",
                    "createdAt": created,
                    "updatedAt": created,
                },
                {
                    "id": "c",
                    "name": "Hoa",
                    "phone": "0903333333",
                    "address": "",
                    "createdAt": created,
                    "updatedAt": created,
                },
            ],
            [],
        )
        result = rewrite_stored_addresses(self.db_path, later, batch=1)
        self.assertEqual(result, {"seen": 3, "changed": 1, "unchanged": 2})
        self.assertEqual(db.customer_count(self.db_path), 3)
        rows = {row["id"]: row for row in db.list_customers(self.db_path)}
        self.assertEqual(rows["a"]["address"], "12 Lê Lợi, Quận 1, TP.HCM")
        self.assertEqual(rows["a"]["name"], "Lan")
        self.assertEqual(rows["a"]["phone"], "0901111111")
        self.assertEqual(rows["a"]["updatedAt"], later)
        self.assertEqual(rows["b"]["address"], "Đà Nẵng")
        self.assertEqual(rows["b"]["updatedAt"], created)
        self.assertEqual(rows["c"]["address"], "")
        self.assertEqual(rows["c"]["updatedAt"], created)

    def test_ingest_one_roundtrip_for_thousands_of_phones(self) -> None:
        now = "2026-01-01T00:00:00+00:00"
        incoming = ({"name": f"K{index}", "phone": f"0906{index:07d}"} for index in range(2200))
        stats, _preview = ingest_customers(self.db_path, incoming, now, flush_every=2200)
        self.assertEqual(stats["created"], 2200)
        self.assertEqual(stats["total"], 2200)
        found = db.customers_for_phones(
            self.db_path,
            ["09060000003", "849060000004", "09069999999"],
        )
        self.assertEqual(len(found["09060000003"]), 1)
        self.assertEqual(found["09060000003"][0]["name"], "K3")
        self.assertEqual(len(found["849060000004"]), 1)
        self.assertEqual(found["849060000004"][0]["name"], "K4")
        self.assertEqual(found["09069999999"], [])

    def test_ingest_merges_same_phone_and_skips_no_phone(self) -> None:
        now = "2026-01-01T00:00:00+00:00"
        incoming = iter(
            [
                {"name": "Lan", "phone": "0901111111", "address": ""},
                {"name": "", "phone": "0901111111", "address": "Huế"},
                {"name": "Không số A", "phone": ""},
                {"name": "Không số B", "phone": ""},
                {"name": "Không số A", "phone": ""},
            ]
        )
        stats, _preview = ingest_customers(self.db_path, incoming, now, flush_every=50)
        self.assertEqual(stats["incoming"], 5)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["skipped"], 3)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(db.delete_customers_without_phone(self.db_path), 0)
        conn = db.connect(self.db_path)
        try:
            lan = db.customers_by_phone(conn, "0901111111")
            empty = conn.execute("SELECT COUNT(*) AS n FROM customers WHERE phone = ''").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(len(lan), 1)
        self.assertEqual(lan[0]["name"], "Lan")
        self.assertEqual(lan[0]["address"], "Huế")
        self.assertEqual(int(empty), 0)

    def test_delete_customers_without_phone_keeps_numbered_rows(self) -> None:
        db.replace_customers(
            self.db_path,
            [
                {
                    "id": "p1",
                    "name": "Lan",
                    "phone": "0901111111",
                    "address": "Huế",
                    "facebookUid": "",
                    "tiktokId": "",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                },
                {
                    "id": "e1",
                    "name": "Không số",
                    "phone": "",
                    "address": "Huế",
                    "facebookUid": "",
                    "tiktokId": "",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                },
            ],
            [],
        )
        self.assertEqual(db.delete_customers_without_phone(self.db_path), 1)
        self.assertEqual(db.customer_count(self.db_path), 1)
        stats = db.warehouse_stats(self.db_path)
        self.assertEqual(stats["emptyPhone"], 0)
        self.assertEqual(stats["uniquePhones"], 1)

    def test_saved_total_tracks_inserts_updates_and_deletes(self) -> None:
        before = db.customer_count(self.db_path)
        db.set_customer_total(self.db_path, 10)
        now = "2026-01-01T00:00:00+00:00"
        row = {
            "id": "n1",
            "name": "Lan",
            "phone": "0901999888",
            "address": "Huế",
            "createdAt": now,
            "updatedAt": now,
        }
        db.replace_customers(self.db_path, [row], [])
        self.assertEqual(db.cached_customer_total(self.db_path), 11)
        db.replace_customers(self.db_path, [dict(row, name="Lan 2")], [])
        self.assertEqual(db.cached_customer_total(self.db_path), 11)
        db.replace_customers(self.db_path, [], ["n1"])
        self.assertEqual(db.cached_customer_total(self.db_path), 10)
        self.assertEqual(db.customer_count(self.db_path), before)

    def test_ingest_merges_84_and_00_prefix_to_same_row(self) -> None:
        now = "2026-01-01T00:00:00+00:00"
        first, _preview = ingest_customers(
            self.db_path,
            iter([{"name": "Lan", "phone": "0901222333", "address": "Huế"}]),
            now,
        )
        self.assertEqual(first["created"], 1)
        later, _preview = ingest_customers(
            self.db_path,
            iter(
                [
                    {"name": "Tên khác", "phone": "84901222333", "address": "Đà Nẵng", "codAmount": "120k"},
                    {"name": "", "phone": "00352548360", "address": "Nha Trang"},
                    {"name": "Minh", "phone": "0352548360", "address": "Quy Nhơn"},
                ]
            ),
            "2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(later["created"], 1)
        self.assertEqual(later["updated"], 2)
        self.assertEqual(later["total"], 2)
        conn = db.connect(self.db_path)
        try:
            lan = db.customers_by_phone(conn, "84901222333")
            minh = db.customers_by_phone(conn, "00352548360")
        finally:
            conn.close()
        self.assertEqual(len(lan), 1)
        self.assertEqual(lan[0]["name"], "Lan\nTên khác")
        self.assertEqual(lan[0]["address"], "Huế\nĐà Nẵng")
        self.assertEqual(lan[0]["codAmount"], "120000")
        self.assertEqual(len(minh), 1)
        self.assertEqual(minh[0]["name"], "Minh")
        self.assertEqual(minh[0]["phone"], "0352548360")
        self.assertEqual(minh[0]["address"], "Nha Trang\nQuy Nhơn")

    def test_ingest_same_batch_84_and_0_merge(self) -> None:
        now = "2026-01-01T00:00:00+00:00"
        stats, _preview = ingest_customers(
            self.db_path,
            iter(
                [
                    {"name": "Lan", "phone": "0901222333", "address": "Huế"},
                    {"name": "Tên khác", "phone": "84901222333", "address": "Đà Nẵng"},
                    {"name": "Minh", "phone": "0352548360", "address": "Nha Trang"},
                    {"name": "", "phone": "00352548360", "address": "Quy Nhơn"},
                    {"name": "UID", "phone": "1000123456789", "address": "Bỏ"},
                ]
            ),
            now,
        )
        self.assertEqual(stats["created"], 2)
        self.assertEqual(stats["total"], 2)
        self.assertGreaterEqual(stats["skipped"], 1)
        conn = db.connect(self.db_path)
        try:
            lan = db.customers_by_phone(conn, "84901222333")
            minh = db.customers_by_phone(conn, "0352548360")
        finally:
            conn.close()
        self.assertEqual(len(lan), 1)
        self.assertEqual(lan[0]["name"], "Lan\nTên khác")
        self.assertEqual(lan[0]["address"], "Huế\nĐà Nẵng")
        self.assertEqual(len(minh), 1)
        self.assertEqual(minh[0]["name"], "Minh")
        self.assertEqual(minh[0]["address"], "Nha Trang\nQuy Nhơn")


class PhoneOnRowTests(unittest.TestCase):
    def test_phone_from_row_cells_skips_facebook_uid(self) -> None:
        self.assertEqual(
            phone_from_row_cells(["Lan", "Nữ", "100007416894544", "0901222333", "Huế"]),
            "0901222333",
        )
        self.assertEqual(
            phone_from_row_cells(["Lan", "Nữ", "100007416894544", "Huế"]),
            "",
        )
        self.assertEqual(
            phone_from_row_cells(["Lan", "84901444555", "100007416894544"], skip_indexes={1}),
            "0901444555",
        )

    def test_rows_from_table_takes_phone_on_row_when_mapped_column_is_wrong(self) -> None:
        rows = [
            ["Tên", "Giới tính", "UID Facebook", "Ghi chú", "Địa chỉ"],
            ["Lan", "Nữ", "100007416894544", "0901111111", "Huế"],
            ["Minh", "Nam", "100007416894545", "0902222222", "Đà Nẵng"],
            ["Không số", "Nữ", "100007416894546", "", "Nha Trang"],
        ]
        mapping = {
            "name": {"index": 0, "header": "Tên"},
            "phone": {"index": 1, "header": "Giới tính"},
            "facebookUid": {"index": 2, "header": "UID Facebook"},
            "address": {"index": 4, "header": "Địa chỉ"},
        }
        extracted = rows_from_table(rows, mapping)
        self.assertEqual([row["phone"] for row in extracted], ["0901111111", "0902222222"])
        self.assertEqual(extracted[0]["name"], "Lan")
        self.assertEqual(extracted[0]["facebookUid"], "100007416894544")
        self.assertEqual(extracted[0]["address"], "Huế")

    def test_phone_from_cell_reads_text_and_skips_facebook(self) -> None:
        self.assertEqual(phone_from_cell("LH: 0901.222.333 gặp chiều"), "0901222333")
        self.assertEqual(phone_from_cell("100007416894544"), "")
        self.assertEqual(phone_from_cell("84901444555"), "0901444555")
        self.assertEqual(_usable_mapped_phone("1000123456789"), "")
        self.assertEqual(_usable_mapped_phone("1.000123456789e+12"), "")
        self.assertEqual(_usable_mapped_phone("0901222333"), "0901222333")


class CompleteRowImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="khach-complete-")
        self.db_path = Path(self.folder.name) / "server.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _phones(self) -> set[str]:
        conn = db.connect(self.db_path)
        try:
            rows = conn.execute("SELECT phone FROM customers").fetchall()
        finally:
            conn.close()
        return {str(row["phone"]) for row in rows}

    def test_every_row_with_phone_becomes_one_customer(self) -> None:
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên"},
                "phone": {"index": 1, "header": "SĐT"},
                "address": {"index": 2, "header": "Địa chỉ"},
            }
        )
        payload = write_xlsx(
            ["Tên", "SĐT", "Địa chỉ"],
            [
                ["Lan", "0901111111", "Huế"],
                ["", "", ""],
                ["Minh", "+84 902 222 222", "Đà Nẵng"],
                ["Hoa", "84903333333", "Nha Trang"],
                ["Không số", "", "Chỉ địa chỉ"],
                ["An", "0901.444.444", "Quy Nhơn"],
                ["Lan trùng", "0901111111", "Huế 2"],
            ],
        )
        incoming, reports = rows_from_files([("du-kien.xlsx", payload)], mapping)
        self.assertEqual(reports[0]["rows"], 5)
        self.assertEqual([row["phone"] for row in incoming], [
            "0901111111",
            "0902222222",
            "0903333333",
            "0901444444",
            "0901111111",
        ])
        stats, _preview = ingest_customers(self.db_path, iter(incoming), "2026-01-01T00:00:00+00:00")
        self.assertEqual(stats["incoming"], 5)
        self.assertEqual(stats["created"] + stats["updated"] + stats["unchanged"], 5)
        self.assertEqual(stats["created"], 4)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(self._phones(), {"0901111111", "0902222222", "0903333333", "0901444444"})

    def test_second_sheet_keeps_first_data_row_and_own_headers(self) -> None:
        payload = write_xlsx_sheets(
            [
                (
                    "Mot",
                    [
                        ["Tên", "SĐT", "Địa chỉ"],
                        ["Lan", "0901111111", "Huế"],
                    ],
                ),
                (
                    "Hai",
                    [
                        ["Họ tên", "Số điện thoại", "Địa chỉ nhận"],
                        ["Minh", "0902222222", "Đà Nẵng"],
                        ["Hoa", "0903333333", "Nha Trang"],
                    ],
                ),
                (
                    "Ba",
                    [
                        ["An", "0904444444", "Quy Nhơn"],
                        ["Bình", "0905555555", "Hà Nội"],
                    ],
                ),
            ]
        )
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên"},
                "phone": {"index": 1, "header": "SĐT"},
                "address": {"index": 2, "header": "Địa chỉ"},
            }
        )
        counts = {"fileRows": 0, "unmapped": 0, "phonesOnFile": 0}
        _width, mapped = iter_mapped_file("nhieu-sheet.xlsx", payload, mapping, None, counts)
        incoming = list(mapped)
        self.assertEqual(counts["phonesOnFile"], 5)
        self.assertEqual({row["phone"] for row in incoming}, {
            "0901111111",
            "0902222222",
            "0903333333",
            "0904444444",
            "0905555555",
        })
        stats, _preview = ingest_customers(self.db_path, iter(incoming), "2026-01-01T00:00:00+00:00")
        self.assertEqual(stats["created"], 5)
        self.assertEqual(self._phones(), {
            "0901111111",
            "0902222222",
            "0903333333",
            "0904444444",
            "0905555555",
        })

    def test_messy_and_excel_number_phones_still_import(self) -> None:
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên"},
                "phone": {"index": 1, "header": "Giới tính"},
                "address": {"index": 2, "header": "Địa chỉ"},
            }
        )
        rows = [
            ["Tên", "Giới tính", "Ghi chú", "Địa chỉ"],
            ["Lan", "Nữ", "LH: 0901.666.666 gặp chiều", "Huế"],
            ["Minh", "Nam", "9.01222777E8", "Đà Nẵng"],
            ["Hoa", "Nữ", "0901222888.0", "Nha Trang"],
            ["Uid", "Nam", "100007416894544", "Hà Nội"],
        ]
        extracted = rows_from_table(rows, mapping)
        self.assertEqual([row["phone"] for row in extracted], ["0901666666", "0901222777", "0901222888"])
        stats, _preview = ingest_customers(self.db_path, iter(extracted), "2026-01-01T00:00:00+00:00")
        self.assertEqual(stats["created"], 3)
        self.assertEqual(self._phones(), {"0901666666", "0901222777", "0901222888"})

    def test_csv_semicolon_keeps_every_phone_row(self) -> None:
        text = "Tên;SĐT;Địa chỉ\nLan;0901111111;Huế\nMinh;0902222222;Đà Nẵng\n"
        mapping = mapping_from_form(
            {
                "name": {"index": 0, "header": "Tên"},
                "phone": {"index": 1, "header": "SĐT"},
                "address": {"index": 2, "header": "Địa chỉ"},
            }
        )
        incoming, reports = rows_from_files([("danh-ba.csv", text.encode("utf-8"))], mapping)
        self.assertEqual(reports[0]["rows"], 2)
        self.assertEqual({row["phone"] for row in incoming}, {"0901111111", "0902222222"})

    def test_write_xlsx_to_path_splits_sheets_and_streams(self) -> None:
        dest = Path(self.folder.name) / "kho.xlsx"
        rows = [[f"K{index}", f"0901{index:06d}", "Huế"] for index in range(5)]
        write_xlsx_to_path(["Tên", "SĐT", "Địa chỉ"], rows, dest, sheet_rows=2)
        self.assertTrue(dest.exists())
        self.assertGreater(dest.stat().st_size, 200)
        with zipfile.ZipFile(dest) as archive:
            sheets = [name for name in archive.namelist() if name.startswith("xl/worksheets/")]
            self.assertEqual(len(sheets), 3)
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("Khach", workbook)
            self.assertIn("Khach 2", workbook)
            self.assertIn("Khach 3", workbook)
            first = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("K0", first)
            self.assertIn("K1", first)
            self.assertNotIn("K2", first)

    def test_headerless_second_sheet_is_not_dropped_by_combine(self) -> None:
        combined = _combine_sheets(
            [
                [["Tên khách", "SĐT"], ["Lan", "0901111111"]],
                [["Minh", "0902222222"], ["Hoa", "0903333333"]],
            ]
        )
        self.assertEqual(combined[0], ["Tên khách", "SĐT"])
        self.assertIn(["Minh", "0902222222"], combined)
        self.assertIn(["Hoa", "0903333333"], combined)
        self.assertIn(["Lan", "0901111111"], combined)


class SharedStringSpillTests(unittest.TestCase):
    def test_store_spills_to_disk_and_reads_back(self) -> None:
        previous = settings.data_dir
        with tempfile.TemporaryDirectory(prefix="khach-sst-") as folder:
            settings.data_dir = Path(folder)
            settings.ensure_dirs()
            store = SharedStringStore()
            try:
                store.prepare_disk()
                store.append("Lan")
                store.append("0901222333")
                store.append("Huế")
                store.finalize()
                self.assertTrue(store._spilled)
                self.assertEqual(store[0], "Lan")
                self.assertEqual(store[1], "0901222333")
                self.assertEqual(store[2], "Huế")
                leftover = list(settings.tmp_dir().glob("khach-sst-*"))
                self.assertEqual(len(leftover), 1)
            finally:
                store.close()
                settings.data_dir = previous
            self.assertEqual(list(Path(folder, "tmp").glob("khach-sst-*")), [])

    def test_fill_spills_when_unique_count_is_high(self) -> None:
        import control_plane.excel_import as excel_import

        previous_spill = excel_import.SHARED_SPILL
        previous_dir = settings.data_dir
        payload = (
            b'<?xml version="1.0"?><sst uniqueCount="8" xmlns="http://schemas.openxmlformats.org/'
            b'spreadsheetml/2006/main">'
            b"<si><t>A0</t></si><si><t>A1</t></si><si><t>A2</t></si><si><t>A3</t></si>"
            b"</sst>"
        )
        with tempfile.TemporaryDirectory(prefix="khach-sst2-") as folder:
            settings.data_dir = Path(folder)
            settings.ensure_dirs()
            excel_import.SHARED_SPILL = 3
            store = SharedStringStore()
            try:
                _fill_shared_strings(io.BytesIO(payload), store)
                self.assertTrue(store._spilled)
                self.assertEqual(store[0], "A0")
                self.assertEqual(store[3], "A3")
            finally:
                store.close()
                excel_import.SHARED_SPILL = previous_spill
                settings.data_dir = previous_dir

    def test_iter_xlsx_rows_reads_spilled_shared_strings(self) -> None:
        import control_plane.excel_import as excel_import

        previous_spill = excel_import.SHARED_SPILL
        previous_dir = settings.data_dir
        strings = ["Tên khách", "SĐT", "Lan", "0901222333", "Minh", "0901444555"]
        sst = (
            '<?xml version="1.0"?><sst uniqueCount="6" xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main">'
            + "".join(f"<si><t>{item}</t></si>" for item in strings)
            + "</sst>"
        )
        sheet = (
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
            '<row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3" t="s"><v>5</v></c></row>'
            "</sheetData></worksheet>"
        )
        workbook = """<?xml version="1.0"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Khach" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
        workbook_rels = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
        content_types = """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""
        rels = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
            archive.writestr("xl/sharedStrings.xml", sst)
        with tempfile.TemporaryDirectory(prefix="khach-sst3-") as folder:
            settings.data_dir = Path(folder)
            settings.ensure_dirs()
            excel_import.SHARED_SPILL = 3
            try:
                rows = list(iter_xlsx_rows(buffer.getvalue()))
            finally:
                excel_import.SHARED_SPILL = previous_spill
                settings.data_dir = previous_dir
        self.assertEqual(rows[0], ["Tên khách", "SĐT"])
        self.assertEqual(rows[1], ["Lan", "0901222333"])
        self.assertEqual(rows[2], ["Minh", "0901444555"])
        self.assertEqual(list(Path(folder, "tmp").glob("khach-sst-*")), [])


class ProvinceOnlyAddressTests(unittest.TestCase):
    def test_bare_province_and_city_lines_are_removed(self) -> None:
        self.assertEqual(strip_province_only_address("Hà Nội"), "")
        self.assertEqual(strip_province_only_address("Thành phố Hồ Chí Minh"), "")
        self.assertEqual(strip_province_only_address("Tỉnh Thanh Hóa"), "")
        self.assertEqual(strip_province_only_address("Tỉnh An GiangTỉnh An Giang"), "")
        self.assertEqual(strip_province_only_address("Hanoi"), "")
        self.assertEqual(strip_province_only_address("Ho Chi Minh City"), "")
        self.assertEqual(strip_province_only_address("Tỉnh, Sóc Trăng"), "")
        self.assertEqual(strip_province_only_address("thành phố, Bắc Ninh"), "")
        self.assertEqual(strip_province_only_address("Lạng Sơn"), "")
        self.assertEqual(strip_province_only_address("Hải Dương"), "")
        self.assertEqual(strip_province_only_address("xã An Bình\nHà Nội"), "xã An Bình")
        self.assertEqual(strip_province_only_address("Nha Trang"), "")
        self.assertEqual(strip_province_only_address("Vinh, Tỉnh Nghệ An"), "")
        self.assertEqual(strip_province_only_address("Tân An, Long An"), "")
        self.assertEqual(strip_province_only_address("Hà Nội\nHuế"), "")

    def test_street_ward_and_district_addresses_stay(self) -> None:
        street = "12 Lê Lợi"
        self.assertEqual(strip_province_only_address(street), street)
        mixed = "12 Lê Lợi, Phường Phú Hội, Hà Nội"
        self.assertEqual(strip_province_only_address(mixed), mixed)
        self.assertEqual(strip_province_only_address("Phường 1"), "Phường 1")
        self.assertEqual(strip_province_only_address("Ấp Bình Dương"), "Ấp Bình Dương")
        self.assertEqual(strip_province_only_address("Ba Đình"), "Ba Đình")
        self.assertEqual(strip_province_only_address("VALUE"), "VALUE")
        self.assertEqual(strip_province_only_address("."), ".")
        self.assertEqual(strip_province_only_address("Làng Hoa"), "Làng Hoa")
        kept = "12 Lê Lợi\nHà Nội"
        self.assertEqual(strip_province_only_address(kept), "12 Lê Lợi")

    def test_clear_updates_only_the_address_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-tinh-") as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            now = "2026-01-01T00:00:00+00:00"
            db.replace_customers(
                path,
                [
                    {
                        "id": "tinh",
                        "name": "Lan",
                        "phone": "0901222333",
                        "address": "Hà Nội",
                        "adminAddress": "Thừa Thiên Huế",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "duong",
                        "name": "Minh",
                        "phone": "0901444555",
                        "address": "12 Lê Lợi\nHà Nội",
                        "adminAddress": "Đà Nẵng",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                ],
                [],
            )
            stats = clear_province_only_addresses(path, batch=10)
            self.assertEqual(stats["cleared"], 1)
            self.assertEqual(stats["trimmed"], 1)
            with db.session(path) as conn:
                rows = {
                    row["id"]: row
                    for row in conn.execute("SELECT id, address, admin_address, name FROM customers")
                }
            self.assertEqual(rows["tinh"]["address"], "")
            self.assertEqual(rows["tinh"]["admin_address"], "Thừa Thiên Huế")
            self.assertEqual(rows["tinh"]["name"], "Lan")
            self.assertEqual(rows["duong"]["address"], "12 Lê Lợi")


class AdminOrShortAddressTests(unittest.TestCase):
    def test_admin_only_and_short_lines_are_removed(self) -> None:
        self.assertEqual(strip_admin_or_short_address("Hà Nội"), "")
        self.assertEqual(strip_admin_or_short_address("Thành phố Hồ Chí Minh"), "")
        self.assertEqual(strip_admin_or_short_address("Phường Phú Hội"), "")
        self.assertEqual(strip_admin_or_short_address("Phường 1"), "")
        self.assertEqual(strip_admin_or_short_address("Quận 1"), "")
        self.assertEqual(strip_admin_or_short_address("Xã An Bình, Huyện Long Hồ, Tỉnh Vĩnh Long"), "")
        self.assertEqual(strip_admin_or_short_address("Ba Đình"), "")
        self.assertEqual(strip_admin_or_short_address("Hải Dương"), "")
        self.assertEqual(strip_admin_or_short_address("Lạng Sơn"), "")
        self.assertEqual(strip_admin_or_short_address("VALUE"), "")
        self.assertEqual(strip_admin_or_short_address("."), "")
        self.assertEqual(strip_admin_or_short_address("xã An Bình\nHà Nội"), "")

    def test_house_street_and_hamlet_stay(self) -> None:
        self.assertEqual(strip_admin_or_short_address("12 Lê Lợi"), "12 Lê Lợi")
        self.assertEqual(strip_admin_or_short_address("Đường Lê Lợi"), "Đường Lê Lợi")
        self.assertEqual(strip_admin_or_short_address("Thôn Trung"), "Thôn Trung")
        self.assertEqual(strip_admin_or_short_address("Ấp Bình Dương"), "Ấp Bình Dương")
        self.assertEqual(strip_admin_or_short_address("Làng Hoa"), "Làng Hoa")
        self.assertEqual(strip_admin_or_short_address("12/5 Lê Lợi, Phường 1"), "12/5 Lê Lợi")
        self.assertEqual(
            strip_admin_or_short_address("12 Lê Lợi, Phường Phú Hội, Hà Nội"),
            "12 Lê Lợi",
        )
        self.assertEqual(strip_admin_or_short_address("xã An Bình\n12 Lê Lợi"), "12 Lê Lợi")
        self.assertEqual(strip_admin_or_short_address("VALUE\n12 Lê Lợi"), "12 Lê Lợi")
        long_street = "Số 15 đường Nguyễn Huệ, phường Bến Nghé, quận 1"
        self.assertEqual(strip_admin_or_short_address(long_street), "Số 15 đường Nguyễn Huệ")
        self.assertEqual(strip_admin_or_short_address(long_street), strip_admin_or_short_address("Số 15 đường Nguyễn Huệ"))

    def test_clear_updates_only_the_address_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-hanh-chinh-") as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            now = "2026-01-01T00:00:00+00:00"
            db.replace_customers(
                path,
                [
                    {
                        "id": "hanh",
                        "name": "Lan",
                        "phone": "0901222333",
                        "address": "Phường Phú Hội, Quận 1",
                        "adminAddress": "Thừa Thiên Huế",
                        "orderContent": "Nước mắm",
                        "codAmount": "150000",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "duong",
                        "name": "Minh",
                        "phone": "0901444555",
                        "address": "12 Lê Lợi, Phường 1",
                        "adminAddress": "Đà Nẵng",
                        "orderContent": "Áo",
                        "codAmount": "90000",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "ngan",
                        "name": "Hoa",
                        "phone": "0901666777",
                        "address": "VALUE",
                        "adminAddress": "Huế",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                ],
                [],
            )
            stats = clear_admin_or_short_addresses(path, batch=10)
            self.assertEqual(stats["cleared"], 2)
            self.assertEqual(stats["trimmed"], 1)
            again = clear_admin_or_short_addresses(path, batch=10)
            self.assertEqual(again["changed"], 0)
            with db.session(path) as conn:
                rows = {
                    row["id"]: row
                    for row in conn.execute(
                        "SELECT id, name, phone, address, admin_address, order_content, cod_amount, updated_at FROM customers"
                    )
                }
            self.assertEqual(rows["hanh"]["address"], "")
            self.assertEqual(rows["hanh"]["admin_address"], "Thừa Thiên Huế")
            self.assertEqual(rows["hanh"]["name"], "Lan")
            self.assertEqual(rows["hanh"]["phone"], "0901222333")
            self.assertEqual(rows["hanh"]["order_content"], "Nước mắm")
            self.assertEqual(rows["hanh"]["cod_amount"], "150000")
            self.assertEqual(rows["hanh"]["updated_at"], now)
            self.assertEqual(rows["duong"]["address"], "12 Lê Lợi")
            self.assertEqual(rows["duong"]["admin_address"], "Đà Nẵng")
            self.assertEqual(rows["duong"]["updated_at"], now)
            self.assertEqual(rows["ngan"]["address"], "")
            self.assertEqual(rows["ngan"]["admin_address"], "Huế")
            self.assertEqual(rows["ngan"]["name"], "Hoa")
            self.assertEqual(rows["duong"]["admin_address"], "Đà Nẵng")


class ShortAdminOnlyAddressTests(unittest.TestCase):
    def test_only_short_admin_cells_are_removed(self) -> None:
        self.assertTrue(cell_is_short_admin_only("Hà Nội"))
        self.assertTrue(cell_is_short_admin_only("Phường 1"))
        self.assertTrue(cell_is_short_admin_only("Quận 1"))
        self.assertFalse(cell_is_short_admin_only("Thành phố Hồ Chí Minh"))
        self.assertFalse(cell_is_short_admin_only("12 Lê Lợi"))
        self.assertFalse(cell_is_short_admin_only("12 Lê Lợi, Phường 1, Hà Nội"))
        self.assertFalse(cell_is_short_admin_only("VALUE"))
        self.assertFalse(cell_is_short_admin_only("."))

    def test_restore_only_when_incoming_contains_current(self) -> None:
        full = "12 Lê Lợi, Phường Phú Hội, Hà Nội"
        self.assertTrue(should_restore_address("12 Lê Lợi", full))
        self.assertTrue(should_restore_address("", full))
        self.assertFalse(should_restore_address(full, "12 Lê Lợi"))
        self.assertFalse(should_restore_address("12 Lê Lợi", "Hà Nội"))
        self.assertFalse(should_restore_address("", "Hà Nội"))
        self.assertEqual(prefer_import_address("12 Lê Lợi", full), full)
        self.assertEqual(prefer_import_address(full, "12 Lê Lợi"), full)

    def test_clear_and_restore_keep_other_columns(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-khoi-phuc-") as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            now = "2026-01-01T00:00:00+00:00"
            full = "12 Lê Lợi, Phường Phú Hội, Hà Nội"
            db.replace_customers(
                path,
                [
                    {
                        "id": "cat",
                        "name": "Lan",
                        "phone": "0901222333",
                        "address": "12 Lê Lợi",
                        "adminAddress": "Thừa Thiên Huế",
                        "orderContent": "Nước mắm",
                        "codAmount": "150000",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "trong",
                        "name": "Minh",
                        "phone": "0901444555",
                        "address": "",
                        "adminAddress": "Đà Nẵng",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "ngan",
                        "name": "Hoa",
                        "phone": "0901666777",
                        "address": "Hà Nội",
                        "adminAddress": "Huế",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "dai",
                        "name": "Tú",
                        "phone": "0901888999",
                        "address": full,
                        "adminAddress": "Huế",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                ],
                [],
            )
            sources = {
                db.phone_key("0901222333"): full,
                db.phone_key("0901444555"): "Số 8 ngõ 4 đường Lê Lợi, phường Bến Nghé",
                db.phone_key("0901666777"): "Hà Nội",
            }
            stats = restore_stored_addresses(path, sources, batch=10)
            self.assertEqual(stats["expanded"], 1)
            self.assertEqual(stats["filled"], 1)
            cleared = clear_short_admin_only_addresses(path, batch=10)
            self.assertEqual(cleared["cleared"], 1)
            with db.session(path) as conn:
                rows = {
                    row["id"]: row
                    for row in conn.execute(
                        "SELECT id, name, phone, address, admin_address, order_content, cod_amount, updated_at FROM customers"
                    )
                }
            self.assertEqual(rows["cat"]["address"], full)
            self.assertEqual(rows["cat"]["admin_address"], "Thừa Thiên Huế")
            self.assertEqual(rows["cat"]["name"], "Lan")
            self.assertEqual(rows["cat"]["phone"], "0901222333")
            self.assertEqual(rows["cat"]["order_content"], "Nước mắm")
            self.assertEqual(rows["cat"]["cod_amount"], "150000")
            self.assertEqual(rows["cat"]["updated_at"], now)
            self.assertEqual(rows["trong"]["address"], "Số 8 ngõ 4 đường Lê Lợi, phường Bến Nghé")
            self.assertEqual(rows["trong"]["admin_address"], "Đà Nẵng")
            self.assertEqual(rows["ngan"]["address"], "")
            self.assertEqual(rows["ngan"]["admin_address"], "Huế")
            self.assertEqual(rows["dai"]["address"], full)

    def test_collect_from_import_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-import-src-") as folder:
            root = Path(folder)
            batch = root / "imports" / "20261025T000000"
            batch.mkdir(parents=True)
            (batch / "khach.csv").write_text(
                "Tên,Số điện thoại,Địa chỉ\n"
                "Lan,0901222333,\"12 Lê Lợi, Phường 1, Hà Nội\"\n"
                "Minh,0901444555,Hà Nội\n",
                encoding="utf-8",
            )
            store = ImportAddressStore(root / "tmp" / "src.db")
            try:
                stats = collect_import_addresses(root, store)
                self.assertEqual(stats["files"], 1)
                self.assertGreaterEqual(stats["phones"], 1)
                self.assertEqual(
                    store.get(db.phone_key("0901222333")),
                    "12 Lê Lợi, Phường 1, Hà Nội",
                )
                self.assertEqual(store.get(db.phone_key("0901444555")), "")
            finally:
                store.close()

    def test_collect_from_old_customer_db_keeps_longer_address(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-bak-src-") as folder:
            root = Path(folder)
            source = root / "old.db"
            db.init_db(source)
            now = "2026-01-01T00:00:00+00:00"
            full = "12 Lê Lợi, Phường Phú Hội, Hà Nội"
            db.replace_customers(
                source,
                [
                    {
                        "id": "mot",
                        "name": "Lan",
                        "phone": "0901222333",
                        "address": full,
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "hai",
                        "name": "Minh",
                        "phone": "0901444555",
                        "address": "Hà Nội",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                ],
                [],
            )
            store = ImportAddressStore(root / "src.db")
            try:
                store.put(db.phone_key("0901222333"), "12 Lê Lợi")
                store.commit()
                stats = collect_from_customer_db(source, store)
                self.assertGreaterEqual(stats["kept"], 1)
                self.assertEqual(store.get(db.phone_key("0901222333")), full)
                self.assertEqual(store.get(db.phone_key("0901444555")), "")
            finally:
                store.close()


class ExactRepeatAddressTests(unittest.TestCase):
    def test_exact_copies_collapse_to_one(self) -> None:
        self.assertEqual(dedupe_exact_address("Tỉnh, Sóc TrăngTỉnh, Sóc Trăng"), "Tỉnh, Sóc Trăng")
        self.assertEqual(dedupe_exact_address("Nước ngoàiNước ngoài"), "Nước ngoài")
        self.assertEqual(dedupe_exact_address("Nước ngoài Nước ngoài"), "Nước ngoài")
        self.assertEqual(dedupe_exact_address("Nước ngoài, Nước ngoài"), "Nước ngoài")
        self.assertEqual(dedupe_exact_address("Nước ngoàiNước ngoàiNước ngoài"), "Nước ngoài")
        self.assertEqual(dedupe_exact_address("Nước ngoài, Nước ngoài, Nước ngoài"), "Nước ngoài")
        self.assertEqual(dedupe_exact_address("12 Lê Lợi\n12 Lê Lợi"), "12 Lê Lợi")
        self.assertEqual(dedupe_exact_address("12 Lê Lợi\n12 Lê Lợi\n12 Lê Lợi"), "12 Lê Lợi")
        doubled = "Đặc khu Thổ Châu, An Giang"
        self.assertEqual(dedupe_exact_address(doubled + doubled), doubled)
        self.assertEqual(dedupe_exact_address("ForeignerForeigner"), "Foreigner")
        self.assertEqual(dedupe_exact_address("Tỉnh Lạng SơnTỉnh Lạng Sơn"), "Tỉnh Lạng Sơn")
        self.assertEqual(clean_address("Nước ngoàiNước ngoài"), "Nước ngoài")

    def test_partial_repeats_and_different_case_stay(self) -> None:
        kept = [
            "12 Lê Lợi, Phường 1",
            "Phường 1, Phường 2",
            "12 Lê Lợi\n34 Trần Phú",
            "Số 10",
            "11",
            "Số 11, ngõ 11",
            "Tân An, Long An",
            "xã An Bình, huyện An Bình",
            "VALUE",
            ".",
            "Nước ngoàiNước Ngoài",
            "1111111111111111",
            "AAAAAAA AAAAAAA",
        ]
        for text in kept:
            self.assertEqual(dedupe_exact_address(text), text)
            self.assertEqual(clean_address(text), text)

    def test_dedupe_updates_only_the_address_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-lap-") as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            now = "2026-01-01T00:00:00+00:00"
            db.replace_customers(
                path,
                [
                    {
                        "id": "lap",
                        "name": "Lan",
                        "phone": "0901222333",
                        "address": "Nước ngoàiNước ngoài",
                        "adminAddress": "Thừa Thiên Huế",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                    {
                        "id": "thuong",
                        "name": "Minh",
                        "phone": "0901444555",
                        "address": "Phường 1, Phường 2",
                        "adminAddress": "Đà Nẵng",
                        "createdAt": now,
                        "updatedAt": now,
                    },
                ],
                [],
            )
            stats = dedupe_exact_addresses(path, batch=10)
            self.assertEqual(stats["changed"], 1)
            with db.session(path) as conn:
                rows = {
                    row["id"]: row
                    for row in conn.execute(
                        "SELECT id, address, admin_address, name, updated_at FROM customers"
                    )
                }
            self.assertEqual(rows["lap"]["address"], "Nước ngoài")
            self.assertEqual(rows["lap"]["admin_address"], "Thừa Thiên Huế")
            self.assertEqual(rows["lap"]["name"], "Lan")
            self.assertEqual(rows["lap"]["updated_at"], now)
            self.assertEqual(rows["thuong"]["address"], "Phường 1, Phường 2")
            self.assertEqual(rows["thuong"]["admin_address"], "Đà Nẵng")
            self.assertEqual(rows["thuong"]["updated_at"], now)


if __name__ == "__main__":
    unittest.main()
