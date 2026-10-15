"""Nhận cột Excel nhiều form và ghép vào kho tổng."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from control_plane.warehouse import (
    apply_mapping,
    classify_cell,
    column_value,
    detect_table,
    normalize_id,
    normalize_phone,
    normalize_uid,
    preview_bytes,
    record_from_row,
    records_from_bytes,
    _looks_address,
    _looks_name,
)
from control_plane.warehouse_store import (
    export_csv,
    export_csv_rows,
    find_match,
    init_warehouse,
    list_templates,
    preview_matches,
    save_template,
    search_contacts,
    restore_snapshot,
    snapshot_to_file,
    upsert_records,
    warehouse_stats,
)
from control_plane.db import session


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
        self.assertEqual(normalize_phone("0084901234567"), "0901234567")
        self.assertEqual(normalize_phone("840901234567"), "0901234567")
        self.assertEqual(normalize_phone(901234567), "0901234567")
        self.assertEqual(normalize_phone("01691234567"), "0391234567")
        self.assertEqual(normalize_phone("841691234567"), "0391234567")
        self.assertEqual(normalize_phone("008401691234567"), "0391234567")
        self.assertEqual(normalize_phone("01234567890"), "0834567890")
        self.assertEqual(normalize_phone("01281234567"), "0781234567")
        self.assertEqual(normalize_phone("01861234567"), "0561234567")
        self.assertEqual(normalize_phone("01991234567"), "0591234567")
        self.assertEqual(normalize_uid("01691234567"), "")

    def test_uid_not_phone(self) -> None:
        self.assertEqual(normalize_uid("100012345678901"), "100012345678901")
        self.assertEqual(normalize_uid("0901234567"), "")
        self.assertEqual(normalize_phone("3000111222333"), "")

    def test_uid_not_mobile_prefix_or_house_number(self) -> None:
        self.assertEqual(normalize_uid("1234"), "")
        self.assertEqual(normalize_uid("12"), "")
        self.assertEqual(normalize_uid("10001"), "10001")
        self.assertEqual(normalize_uid("090123456789012"), "")
        self.assertEqual(normalize_uid("037123456789"), "")
        self.assertEqual(normalize_uid("84901234567"), "")
        self.assertEqual(normalize_uid("901234567"), "")
        self.assertEqual(normalize_phone("901234567"), "0901234567")
        self.assertEqual(classify_cell("12 Đường Láng, Đống Đa, Hà Nội")[0][0], "address")
        self.assertNotIn("uid", {kind for kind, _score in classify_cell("12 Đường Láng, Đống Đa, Hà Nội")})
        self.assertEqual(classify_cell("10001")[0][0], "uid")
        self.assertEqual(classify_cell("0901234567")[0][0], "phone")

    def test_address_locality_vs_person_name(self) -> None:
        self.assertTrue(_looks_address("Hà Nội"))
        self.assertTrue(_looks_address("Ha Noi"))
        self.assertTrue(_looks_address("Vinh"))
        self.assertTrue(_looks_address("Huyện Đông Anh"))
        self.assertTrue(_looks_address("Xa Tan Phong"))
        self.assertTrue(_looks_address("Quận 1"))
        self.assertTrue(
            _looks_address("12 Đường Láng, Phường Thành Công, Quận Đống Đa, Hà Nội")
        )
        long_place = "so mot nha sau hem ba nha sau hem bon ngo nam pho sau phuong bay"
        self.assertGreater(len(long_place.split()), 8)
        self.assertGreater(len(long_place), 28)
        self.assertTrue(_looks_address(long_place))
        self.assertTrue(_looks_name("An Giang Uyen Lan"))
        self.assertFalse(_looks_address("An Giang Uyen Lan"))
        self.assertTrue(_looks_name("Le Van Quan"))
        self.assertFalse(_looks_address("Le Van Quan"))
        self.assertEqual(classify_cell("Hà Nội")[0][0], "address")
        self.assertEqual(classify_cell("An Tam Quan Em")[0][0], "name")
        self.assertFalse(_looks_name("Hà Nội"))

    def test_facebook_uid_and_tiktok_handle(self) -> None:
        self.assertEqual(
            normalize_uid("https://www.facebook.com/profile.php?id=100012345678901"),
            "100012345678901",
        )
        self.assertEqual(normalize_uid("https://facebook.com/100012345678901"), "100012345678901")
        self.assertEqual(normalize_id("@HoangLan"), "@hoanglan")
        self.assertEqual(normalize_id("https://www.tiktok.com/@TranThiB?lang=vi"), "@tranthib")
        self.assertEqual(normalize_id("KH001"), "")
        self.assertEqual(normalize_id("0901234567"), "")
        self.assertEqual(classify_cell("@hoanglan")[0][0], "id")
        self.assertEqual(classify_cell("0901234567")[0][0], "phone")
        self.assertEqual(classify_cell("100012345678901")[0][0], "uid")


class DetectTests(unittest.TestCase):
    def test_vietnamese_headers(self) -> None:
        rows = [
            ["Họ và tên", "SĐT", "Địa chỉ", "UID", "TikTok"],
            ["Nguyễn Văn A", "0901234567", "12 Đường Láng, Đống Đa, Hà Nội", "100012345678901", "@khach001"],
            ["Trần Thị B", "0912345678", "34 Nguyễn Huệ, Q.1, TP.HCM", "100012345678902", "@khach002"],
        ]
        sheet = detect_table(rows, "DS")
        self.assertEqual(sheet.mapping["name"], 0)
        self.assertEqual(sheet.mapping["phone"], 1)
        self.assertEqual(sheet.mapping["address"], 2)
        self.assertEqual(sheet.mapping["uid"], 3)
        self.assertEqual(sheet.mapping["id"], 4)
        self.assertEqual(sheet.sample[0]["phone"], "0901234567")
        self.assertEqual(sheet.sample[0]["id"], "@khach001")
        self.assertEqual(sheet.method, "header")

    def test_english_headers_and_extra_stt(self) -> None:
        rows = [
            ["No", "Full Name", "Phone Number", "Address", "Facebook UID", "TikTok"],
            [1, "Le Van C", "0934567890", "5 Pho Hue, Hoan Kiem, Ha Noi", "1000999888777", "@levanc"],
            [2, "Pham D", "0945678901", "8 Tran Phu, Ba Dinh, Ha Noi", "1000999888778", "@phamd"],
            [3, "Vo E", "0956789012", "2 Le Loi, Q.1, TP.HCM", "1000999888779", "@voe"],
            [4, "Do F", "0967890123", "9 Hang Bac, Hoan Kiem", "1000999888780", "@dof"],
        ]
        sheet = detect_table(rows, "EN")
        self.assertNotIn(0, sheet.mapping.values())
        self.assertEqual(sheet.mapping["name"], 1)
        self.assertEqual(sheet.mapping["phone"], 2)
        self.assertEqual(sheet.mapping["uid"], 4)
        self.assertEqual(sheet.mapping["id"], 5)
        self.assertEqual(sheet.sample[0]["id"], "@levanc")

    def test_title_row_then_headers(self) -> None:
        rows = [
            ["DANH SÁCH KHÁCH HÀNG THÁNG 10"],
            [],
            ["Tên khách", "Số điện thoại", "Địa chỉ nhà", "UID FB", "ID TikTok"],
            ["Hoàng Lan", "0888123456", "15 Nguyễn Trãi, Thanh Xuân", "2000111222333", "@HoangLan"],
        ]
        sheet = detect_table(rows, "T10")
        self.assertEqual(sheet.header_row, 2)
        self.assertEqual(sheet.sample[0]["name"], "Hoàng Lan")
        self.assertEqual(sheet.sample[0]["phone"], "0888123456")
        self.assertEqual(sheet.sample[0]["id"], "@hoanglan")

    def test_no_header_uses_cell_shape(self) -> None:
        rows = [
            ["Ngô Văn G", "0977000111", "22 Cầu Giấy, Hà Nội", "3000111222333", "@ngovang"],
            ["Mai H", "0977000222", "7 Lê Duẩn, Q.1, TP.HCM", "3000111222334", "@maih"],
            ["Bùi I", "0977000333", "3 Phan Đình Phùng, Ba Đình", "3000111222335", "@buii"],
        ]
        sheet = detect_table(rows, "raw")
        self.assertEqual(sheet.method, "shape")
        self.assertEqual(sheet.mapping["phone"], 1)
        self.assertEqual(sheet.mapping["uid"], 3)
        self.assertTrue(sheet.sample[0]["phone"].startswith("0977"))
        self.assertEqual(sheet.mapping["id"], 4)
        self.assertEqual(sheet.sample[0]["id"], "@ngovang")

    def test_shuffled_no_header_keeps_horizontal_row(self) -> None:
        rows = [
            ["@nguyenvannam", "12 Đường Láng, Đống Đa, Hà Nội", "0901234567", "Nguyễn Văn Nam", "100012345678901"],
            ["@tranthihoa", "7 Lê Duẩn, Quận 1, TP.HCM", "0912345678", "Trần Thị Hoa", "100012345678902"],
        ]
        sheet = detect_table(rows, "lech")
        self.assertEqual(sheet.sample[0]["name"], "Nguyễn Văn Nam")
        self.assertEqual(sheet.sample[0]["phone"], "0901234567")
        self.assertEqual(sheet.sample[0]["address"], "12 Đường Láng, Đống Đa, Hà Nội")
        self.assertEqual(sheet.sample[0]["uid"], "100012345678901")
        self.assertEqual(sheet.sample[0]["id"], "@nguyenvannam")
        self.assertEqual(sheet.sample[1]["name"], "Trần Thị Hoa")
        self.assertEqual(sheet.sample[1]["phone"], "0912345678")

    def test_semicolon_csv(self) -> None:
        payload = (
            "Ten;SDT;Dia chi;UID;TikTok\n"
            "An;0901111222;10 Pho Hue, Ha Noi;4000111222333;@an\n"
        ).encode("utf-8")
        preview = preview_bytes(payload, "khach.csv")
        sheet = preview.sheets[0]
        self.assertEqual(sheet.mapping["name"], 0)
        self.assertEqual(sheet.mapping["phone"], 1)
        self.assertEqual(sheet.sample[0]["name"], "An")
        self.assertEqual(sheet.sample[0]["id"], "@an")

    def test_xlsx_roundtrip_and_template_reuse(self) -> None:
        payload = xlsx_bytes(
            [
                ["Họ tên", "Điện thoại", "Địa chỉ", "UID", "ID"],
                ["Kim J", 981234567, "11 Lý Thường Kiệt, Hoàn Kiếm", "5000111222333", "@kimj"],
            ]
        )
        first = preview_bytes(payload, "a.xlsx")
        self.assertEqual(first.sheets[0].mapping["phone"], 1)
        self.assertEqual(first.sheets[0].sample[0]["phone"], "0981234567")
        self.assertEqual(first.sheets[0].sample[0]["id"], "@kimj")
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
        self.assertEqual(row["nameExtra"], "Lan khác")
        self.assertEqual(row["address"], "1 Đường Láng, Hà Nội")
        self.assertEqual(row["uid"], "6000111222333")
        stats = warehouse_stats(self.db)
        self.assertEqual(stats["contacts"], 1)
        self.assertEqual(stats["phones"], 1)
        self.assertEqual(stats["uids"], 1)
        self.assertEqual(stats["batches"], 2)

    def test_unique_name_and_address_fill_empty(self) -> None:
        upsert_records(
            self.db,
            [
                {
                    "name": "Hoàng Thị Lan",
                    "phone": "0908888111",
                    "address": "",
                    "uid": "",
                    "id": "",
                },
                {
                    "name": "Phạm Quốc Bảo",
                    "phone": "",
                    "address": "88 Nguyễn Trãi, Thanh Xuân, Hà Nội",
                    "uid": "",
                    "id": "",
                },
            ],
            filename="goc.xlsx",
            source="goc",
            mapping={"name": 0},
            at="2026-02-01T00:00:00+00:00",
        )
        result = upsert_records(
            self.db,
            [
                {
                    "name": "Hoàng Thị Lan",
                    "phone": "",
                    "address": "19 Lý Thường Kiệt, Hoàn Kiếm",
                    "uid": "9000111222333",
                    "id": "HL-1",
                },
                {
                    "name": "",
                    "phone": "0912222333",
                    "address": "88 Nguyễn Trãi, Thanh Xuân, Hà Nội",
                    "uid": "",
                    "id": "PQB",
                },
            ],
            filename="bo-sung.xlsx",
            source="bs",
            mapping={"name": 0},
            at="2026-02-02T00:00:00+00:00",
        )
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["inserted"], 0)
        lan = search_contacts(self.db, "Hoàng Thị Lan", limit=10, after=0)["items"][0]
        self.assertEqual(lan["phone"], "0908888111")
        self.assertEqual(lan["address"], "19 Lý Thường Kiệt, Hoàn Kiếm")
        self.assertEqual(lan["uid"], "9000111222333")
        bao = search_contacts(self.db, "0912222333", limit=10, after=0)["items"][0]
        self.assertEqual(bao["name"], "Phạm Quốc Bảo")
        self.assertEqual(bao["externalId"], "PQB")

    def test_same_name_adds_phone_and_cross_match_is_kept(self) -> None:
        seeded = upsert_records(
            self.db,
            [
                {"name": "Nguyễn Văn A", "phone": "0901111001", "address": "", "uid": "", "id": "@a1"},
                {"name": "Nguyễn Văn A", "phone": "0901111002", "address": "", "uid": "", "id": "@a2"},
                {"name": "Trần B", "phone": "", "address": "", "uid": "111122223333444", "id": ""},
                {"name": "Lê C", "phone": "0903333001", "address": "", "uid": "", "id": ""},
            ],
            filename="hai-nguoi.xlsx",
            source="goc",
            mapping={"name": 0},
            at="2026-03-01T00:00:00+00:00",
        )
        self.assertEqual(seeded["inserted"], 3)
        self.assertEqual(seeded["updated"], 1)
        result = upsert_records(
            self.db,
            [
                {
                    "name": "Nguyễn Văn A",
                    "phone": "0909999000",
                    "address": "1 Phố Huế, Hà Nội",
                    "uid": "",
                    "id": "",
                },
                {
                    "name": "",
                    "phone": "0903333001",
                    "address": "",
                    "uid": "111122223333444",
                    "id": "",
                },
            ],
            filename="trung.xlsx",
            source="trung",
            mapping={"name": 0},
            at="2026-03-02T00:00:00+00:00",
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(result["skipped"], 0)
        row = search_contacts(self.db, "0909999000", limit=10, after=0)["items"][0]
        self.assertEqual(row["phone"], "0901111001")
        self.assertIn("0901111002", row["phoneExtra"])
        self.assertIn("0909999000", row["phoneExtra"])
        self.assertEqual(row["address"], "1 Phố Huế, Hà Nội")
        self.assertEqual(search_contacts(self.db, "0903333001", limit=10, after=0)["items"][0]["name"], "Lê C")
        self.assertEqual(search_contacts(self.db, "111122223333444", limit=10, after=0)["items"][0]["name"], "Trần B")
        with session(self.db) as conn:
            conflict = find_match(
                conn,
                {
                    "name": "",
                    "phone": "0903333001",
                    "phone_key": "0903333001",
                    "uid": "111122223333444",
                    "id": "",
                    "address": "",
                },
            )
            self.assertEqual(conflict, (None, "conflict"))

    def test_same_person_keeps_old_and_adds_new_values(self) -> None:
        upsert_records(
            self.db,
            [
                {
                    "name": "Hoàng Lan",
                    "phone": "0901234567",
                    "address": "12 Đường Láng, Hà Nội",
                    "uid": "",
                    "id": "@lan",
                }
            ],
            filename="lan-1.xlsx",
            source="1",
            mapping={"name": 0},
            at="2026-05-01T00:00:00+00:00",
        )
        result = upsert_records(
            self.db,
            [
                {
                    "name": "Lan",
                    "phone": "",
                    "address": "12 Đường Láng, Hà Nội",
                    "uid": "100012345678901",
                    "id": "@lan2",
                }
            ],
            filename="lan-2.xlsx",
            source="2",
            mapping={"name": 0},
            at="2026-05-02T00:00:00+00:00",
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["inserted"], 0)
        row = search_contacts(self.db, "100012345678901", limit=10, after=0)["items"][0]
        self.assertEqual(row["name"], "Hoàng Lan")
        self.assertEqual(row["nameExtra"], "Lan")
        self.assertEqual(row["phone"], "0901234567")
        self.assertEqual(row["address"], "12 Đường Láng, Hà Nội")
        self.assertEqual(row["addressExtra"], "")
        self.assertEqual(row["uid"], "100012345678901")
        self.assertEqual(row["externalId"], "@lan")
        self.assertEqual(row["idExtra"], "@lan2")
        again = upsert_records(
            self.db,
            [
                {
                    "name": "Hoàng Lan",
                    "phone": "0919999000",
                    "address": "7 Lê Duẩn, Quận 1, TP.HCM",
                    "uid": "100012345678901",
                    "id": "@lan",
                }
            ],
            filename="lan-3.xlsx",
            source="3",
            mapping={"name": 0},
            at="2026-05-03T00:00:00+00:00",
        )
        self.assertEqual(again["updated"], 1)
        row = search_contacts(self.db, "0919999000", limit=10, after=0)["items"][0]
        self.assertEqual(row["phone"], "0901234567")
        self.assertEqual(row["phoneExtra"], "0919999000")
        self.assertEqual(row["address"], "12 Đường Láng, Hà Nội")
        self.assertEqual(row["addressExtra"], "7 Lê Duẩn, Quận 1, TP.HCM")
        self.assertEqual(search_contacts(self.db, "", limit=10, after=0)["count"], 1)

    def test_snapshot_restore_keeps_every_field(self) -> None:
        import sqlite3

        upsert_records(
            self.db,
            [{"name": "Minh An", "phone": "0907777666", "address": "12 Đường Láng, Hà Nội", "uid": "555566667777888", "id": "@minhan"}],
            filename="minh.xlsx",
            source="minh",
            mapping={"name": 0},
            at="2026-06-01T00:00:00+00:00",
        )
        upsert_records(
            self.db,
            [{"name": "Minh Khác", "phone": "0918888777", "address": "", "uid": "", "id": ""}],
            filename="minh-2.xlsx",
            source="minh",
            mapping={"name": 0},
            at="2026-06-02T00:00:00+00:00",
        )
        stats = warehouse_stats(self.db)
        self.assertEqual(stats["storage"], "one")
        self.assertNotIn("copies", stats)
        self.assertFalse((self.db.parent / f"{self.db.name}.copies").exists())
        snap = self.tmp / "kho-tong.db"
        snapshot_to_file(self.db, snap)
        restored = self.tmp / "restored.db"
        shutil_copy = snap.read_bytes()
        incoming = self.tmp / "incoming.db"
        incoming.write_bytes(shutil_copy)
        restore_snapshot(restored, incoming)
        found = search_contacts(restored, "0907777666", limit=10, after=0)
        self.assertEqual(found["items"][0]["name"], "Minh An")
        self.assertEqual(found["items"][0]["phone"], "0907777666")
        self.assertEqual(found["items"][0]["address"], "12 Đường Láng, Hà Nội")
        self.assertEqual(found["items"][0]["uid"], "555566667777888")
        self.assertEqual(found["items"][0]["externalId"], "@minhan")
        self.assertEqual(search_contacts(restored, "", limit=10, after=0)["count"], 2)
        self.assertFalse(incoming.exists())
        with sqlite3.connect(self.db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(warehouse_contacts)")}
        self.assertNotIn("name_key", columns)
        self.assertNotIn("phone_key", columns)

    def test_legacy_schema_migrates_without_losing_fields(self) -> None:
        import sqlite3

        legacy = self.tmp / "legacy.db"
        conn = sqlite3.connect(legacy)
        conn.executescript(
            """
            CREATE TABLE warehouse_contacts (
              row_id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL DEFAULT '',
              name_key TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              phone_key TEXT NOT NULL DEFAULT '',
              address TEXT NOT NULL DEFAULT '',
              address_key TEXT NOT NULL DEFAULT '',
              uid TEXT NOT NULL DEFAULT '',
              external_id TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              batch_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              name_extra TEXT NOT NULL DEFAULT '',
              phone_extra TEXT NOT NULL DEFAULT '',
              address_extra TEXT NOT NULL DEFAULT '',
              uid_extra TEXT NOT NULL DEFAULT '',
              id_extra TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE warehouse_changes (
              change_id INTEGER PRIMARY KEY AUTOINCREMENT,
              op TEXT NOT NULL,
              at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            INSERT INTO warehouse_contacts(
              name, name_key, phone, phone_key, address, address_key, uid, external_id,
              source, created_at, updated_at, phone_extra
            ) VALUES(
              'Minh An', 'minh an', '0907777666', '0907777666', '12 Đường Láng, Hà Nội',
              '12 duong lang ha noi', '555566667777888', '@minhan', 'minh',
              '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00', '0918888777'
            );
            """
        )
        conn.commit()
        conn.close()
        copies = legacy.parent / f"{legacy.name}.copies"
        copies.mkdir()
        (copies / "journal.jsonl").write_text("x\n", encoding="utf-8")
        init_warehouse(legacy)
        self.assertFalse(copies.exists())
        found = search_contacts(legacy, "0918888777", limit=10, after=0)
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["items"][0]["name"], "Minh An")
        self.assertEqual(found["items"][0]["phone"], "0907777666")
        self.assertEqual(found["items"][0]["phoneExtra"], "0918888777")
        self.assertEqual(found["items"][0]["address"], "12 Đường Láng, Hà Nội")
        self.assertEqual(found["items"][0]["uid"], "555566667777888")
        self.assertEqual(found["items"][0]["externalId"], "@minhan")

    def test_same_file_duplicate_phone_fills(self) -> None:
        result = upsert_records(
            self.db,
            [
                {"name": "Lan", "phone": "0901234567", "address": "", "uid": "", "id": ""},
                {"name": "", "phone": "0901234567", "address": "1 Đường Láng, Hà Nội", "uid": "", "id": ""},
            ],
            filename="mot-file.xlsx",
            source="mot",
            mapping={"name": 0, "phone": 1},
            at="2026-04-01T00:00:00+00:00",
        )
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped"], 0)
        found = search_contacts(self.db, "0901234567", limit=10, after=0)
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["items"][0]["name"], "Lan")
        self.assertEqual(found["items"][0]["address"], "1 Đường Láng, Hà Nội")

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
        chunks = list(export_csv_rows(self.db, "0911111222"))
        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn("0911111222", b"".join(chunks).decode("utf-8"))
        stats = warehouse_stats(self.db)
        self.assertEqual(stats["contacts"], 1)
        self.assertEqual(stats["phones"], 1)

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
        self.assertEqual(apply_mapping([["", ""]], {}), [])

    def test_shuffled_cells_stay_one_row(self) -> None:
        row = ["@NguyenVanNam", "0901234567", "100012345678901", "Nguyễn Văn Nam", "12 Đường Láng, Đống Đa, Hà Nội"]
        kinds = {kind for kind, _score in classify_cell(row[1])}
        self.assertIn("phone", kinds)
        record = record_from_row(row)
        self.assertEqual(record["name"], "Nguyễn Văn Nam")
        self.assertEqual(record["phone"], "0901234567")
        self.assertEqual(record["address"], "12 Đường Láng, Đống Đa, Hà Nội")
        self.assertEqual(record["uid"], "100012345678901")
        self.assertEqual(record["id"], "@nguyenvannam")
        mapped_wrong = record_from_row(row, {"name": 0, "phone": 1, "uid": 2})
        self.assertEqual(mapped_wrong["id"], "@nguyenvannam")
        self.assertEqual(mapped_wrong["phone"], "0901234567")
        self.assertEqual(mapped_wrong["name"], "Nguyễn Văn Nam")
        self.assertEqual(mapped_wrong["uid"], "100012345678901")
        self.assertEqual(mapped_wrong["address"], "12 Đường Láng, Đống Đa, Hà Nội")

    def test_rows_do_not_borrow_cells(self) -> None:
        records = apply_mapping(
            [
                ["@an", "0901111222", "Nguyễn Văn An", "1000111222333", "12 Đường Láng, Đống Đa, Hà Nội"],
                ["Trần Thị Bình", "34 Nguyễn Huệ, Q.1, TP.HCM", "0912222333", "@binh", "1000111222444"],
            ],
            {},
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["name"], "Nguyễn Văn An")
        self.assertEqual(records[0]["phone"], "0901111222")
        self.assertEqual(records[0]["id"], "@an")
        self.assertEqual(records[0]["uid"], "1000111222333")
        self.assertEqual(records[1]["name"], "Trần Thị Bình")
        self.assertEqual(records[1]["phone"], "0912222333")
        self.assertEqual(records[1]["id"], "@binh")
        self.assertEqual(records[1]["uid"], "1000111222444")
        self.assertNotEqual(records[0]["phone"], records[1]["phone"])
        self.assertEqual(
            record_from_row(["KH001"]),
            {"name": "", "phone": "", "address": "", "uid": "", "id": ""},
        )

    def test_confirmed_columns_do_not_borrow(self) -> None:
        row = ["@NguyenVanNam", "0901234567", "100012345678901", "Nguyễn Văn Nam", "12 Đường Láng, Đống Đa, Hà Nội"]
        chosen = record_from_row(
            row,
            {"name": 3, "phone": 1, "address": 4, "uid": 2, "id": 0},
            strict=True,
        )
        self.assertEqual(chosen["name"], "Nguyễn Văn Nam")
        self.assertEqual(chosen["phone"], "0901234567")
        self.assertEqual(chosen["id"], "@nguyenvannam")
        self.assertEqual(chosen["uid"], "100012345678901")
        only_phone = record_from_row(row, {"phone": 1}, strict=True)
        self.assertEqual(only_phone["phone"], "0901234567")
        self.assertEqual(only_phone["name"], "")
        self.assertEqual(only_phone["uid"], "")
        self.assertEqual(column_value("id", "hoanglan"), "@hoanglan")
        self.assertEqual(column_value("phone", "Nguyễn Văn Nam"), "")

    def test_preview_keeps_raw_grid(self) -> None:
        payload = xlsx_bytes(
            [
                ["Tên khách", "SĐT", "Địa chỉ khách"],
                ["Lan", "0901234567", "12 Đường Láng, Hà Nội"],
            ]
        )
        sheet = preview_bytes(payload, "khach.xlsx").sheets[0]
        self.assertEqual(sheet.grid[0][0], "Lan")
        self.assertEqual(sheet.mapping["phone"], 1)
