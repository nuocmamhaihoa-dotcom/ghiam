"""Nhận cột Excel nhiều form và ghép vào kho tổng."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from control_plane.warehouse import (
    apply_mapping,
    detect_table,
    normalize_phone,
    normalize_uid,
    preview_bytes,
    records_from_bytes,
)
from control_plane.warehouse_store import (
    export_csv,
    init_warehouse,
    list_templates,
    save_template,
    search_contacts,
    upsert_records,
)


def xlsx_bytes(rows: list[list[object]], title: str = "DS") -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


class NormalizeTests(unittest.TestCase):
    def test_phone_vn_and_excel_number(self) -> None:
        self.assertEqual(normalize_phone("0901234567"), "0901234567")
        self.assertEqual(normalize_phone("901234567"), "0901234567")
        self.assertEqual(normalize_phone("+84 901 234 567"), "0901234567")
        self.assertEqual(normalize_phone(901234567), "0901234567")

    def test_uid_not_phone(self) -> None:
        self.assertEqual(normalize_uid("100012345678901"), "100012345678901")
        self.assertEqual(normalize_uid("0901234567"), "")
        self.assertEqual(normalize_phone("3000111222333"), "")


class DetectTests(unittest.TestCase):
    def test_vietnamese_headers(self) -> None:
        rows = [
            ["Họ và tên", "SĐT", "Địa chỉ", "UID", "Mã khách"],
            ["Nguyễn Văn A", "0901234567", "12 Đường Láng, Đống Đa, Hà Nội", "100012345678901", "KH001"],
            ["Trần Thị B", "0912345678", "34 Nguyễn Huệ, Q.1, TP.HCM", "100012345678902", "KH002"],
        ]
        sheet = detect_table(rows, "DS")
        self.assertEqual(sheet.mapping["name"], 0)
        self.assertEqual(sheet.mapping["phone"], 1)
        self.assertEqual(sheet.mapping["address"], 2)
        self.assertEqual(sheet.mapping["uid"], 3)
        self.assertEqual(sheet.mapping["id"], 4)
        self.assertEqual(sheet.sample[0]["phone"], "0901234567")
        self.assertEqual(sheet.sample[0]["id"], "KH001")
        self.assertEqual(sheet.method, "header")

    def test_english_headers_and_extra_stt(self) -> None:
        rows = [
            ["No", "Full Name", "Phone Number", "Address", "Facebook UID", "Customer ID"],
            [1, "Le Van C", "0934567890", "5 Pho Hue, Hoan Kiem, Ha Noi", "1000999888777", "C-9"],
            [2, "Pham D", "0945678901", "8 Tran Phu, Ba Dinh, Ha Noi", "1000999888778", "C-10"],
            [3, "Vo E", "0956789012", "2 Le Loi, Q.1, TP.HCM", "1000999888779", "C-11"],
            [4, "Do F", "0967890123", "9 Hang Bac, Hoan Kiem", "1000999888780", "C-12"],
        ]
        sheet = detect_table(rows, "EN")
        self.assertNotIn(0, sheet.mapping.values())
        self.assertEqual(sheet.mapping["name"], 1)
        self.assertEqual(sheet.mapping["phone"], 2)
        self.assertEqual(sheet.mapping["uid"], 4)

    def test_title_row_then_headers(self) -> None:
        rows = [
            ["DANH SÁCH KHÁCH HÀNG THÁNG 10"],
            [],
            ["Tên khách", "Số điện thoại", "Địa chỉ nhà", "UID FB", "Mã"],
            ["Hoàng Lan", "0888123456", "15 Nguyễn Trãi, Thanh Xuân", "2000111222333", "A1"],
        ]
        sheet = detect_table(rows, "T10")
        self.assertEqual(sheet.header_row, 2)
        self.assertEqual(sheet.sample[0]["name"], "Hoàng Lan")
        self.assertEqual(sheet.sample[0]["phone"], "0888123456")

    def test_no_header_uses_cell_shape(self) -> None:
        rows = [
            ["Ngô Văn G", "0977000111", "22 Cầu Giấy, Hà Nội", "3000111222333", "Z99"],
            ["Mai H", "0977000222", "7 Lê Duẩn, Q.1, TP.HCM", "3000111222334", "Z100"],
            ["Bùi I", "0977000333", "3 Phan Đình Phùng, Ba Đình", "3000111222335", "Z101"],
        ]
        sheet = detect_table(rows, "raw")
        self.assertEqual(sheet.method, "shape")
        self.assertEqual(sheet.mapping["phone"], 1)
        self.assertEqual(sheet.mapping["uid"], 3)
        self.assertTrue(sheet.sample[0]["phone"].startswith("0977"))

    def test_semicolon_csv(self) -> None:
        payload = (
            "Ten;SDT;Dia chi;UID;Ma\n"
            "An;0901111222;10 Pho Hue, Ha Noi;4000111222333;M1\n"
        ).encode("utf-8")
        preview = preview_bytes(payload, "khach.csv")
        sheet = preview.sheets[0]
        self.assertEqual(sheet.mapping["name"], 0)
        self.assertEqual(sheet.mapping["phone"], 1)
        self.assertEqual(sheet.sample[0]["name"], "An")

    def test_xlsx_roundtrip_and_template_reuse(self) -> None:
        payload = xlsx_bytes(
            [
                ["Họ tên", "Điện thoại", "Địa chỉ", "UID", "ID"],
                ["Kim J", 981234567, "11 Lý Thường Kiệt, Hoàn Kiếm", "5000111222333", "ID-1"],
            ]
        )
        first = preview_bytes(payload, "a.xlsx")
        self.assertEqual(first.sheets[0].mapping["phone"], 1)
        self.assertEqual(first.sheets[0].sample[0]["phone"], "0981234567")
        fingerprint = first.sheets[0].fingerprint
        templates = {fingerprint: {"name": 0, "phone": 1, "address": 2, "uid": 3, "id": 4}}
        again = preview_bytes(payload, "a.xlsx", templates)
        self.assertTrue(again.sheets[0].reused_template)
        self.assertEqual(again.sheets[0].method, "template")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "kho.db"
        init_warehouse(self.db)

    def test_merge_by_phone_fills_empty_only(self) -> None:
        first = upsert_records(
            self.db,
            [{"name": "Lan", "phone": "0901234567", "address": "", "uid": "", "id": "KH1"}],
            filename="a.xlsx",
            source="form-a",
            mapping={"name": 0, "phone": 1},
            at="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(first["inserted"], 1)
        second = upsert_records(
            self.db,
            [
                {
                    "name": "Lan khác",
                    "phone": "0901234567",
                    "address": "1 Đường Láng, Hà Nội",
                    "uid": "6000111222333",
                    "id": "KH1",
                }
            ],
            filename="b.xlsx",
            source="form-b",
            mapping={"name": 0, "phone": 1},
            at="2026-01-02T00:00:00+00:00",
        )
        self.assertEqual(second["updated"], 1)
        found = search_contacts(self.db, "0901234567", limit=10, after=0)
        row = found["items"][0]
        self.assertEqual(row["name"], "Lan")
        self.assertEqual(row["address"], "1 Đường Láng, Hà Nội")
        self.assertEqual(row["uid"], "6000111222333")

    def test_search_and_export(self) -> None:
        upsert_records(
            self.db,
            [
                {
                    "name": "Minh",
                    "phone": "0911111222",
                    "address": "9 Nguyễn Huệ",
                    "uid": "70001",
                    "id": "X1",
                }
            ],
            filename="c.csv",
            source="tay",
            mapping={"name": 0},
            at="2026-01-03T00:00:00+00:00",
        )
        self.assertEqual(search_contacts(self.db, "Nguyễn Huệ", limit=10, after=0)["count"], 1)
        csv_body = export_csv(self.db, "Minh").decode("utf-8")
        self.assertIn("0911111222", csv_body)
        self.assertIn("Tên", csv_body)

    def test_saved_template_roundtrip(self) -> None:
        save_template(self.db, "abc", ["ten", "sdt"], {"name": 0, "phone": 1}, "2026-01-01T00:00:00+00:00")
        self.assertEqual(list_templates(self.db)["abc"]["phone"], 1)


class ApplyTests(unittest.TestCase):
    def test_records_from_xlsx_use_override_mapping(self) -> None:
        payload = xlsx_bytes(
            [
                ["A", "B", "C"],
                ["Pha", "0922222333", "UID-NO"],
            ]
        )
        rows = records_from_bytes(
            payload, "x.xlsx", "DS", {"name": 0, "phone": 1}, header_row=0, has_header=True
        )
        self.assertEqual(rows[0]["name"], "Pha")
        self.assertEqual(rows[0]["phone"], "0922222333")
        self.assertEqual(apply_mapping([["x", "y"]], {}), [])
