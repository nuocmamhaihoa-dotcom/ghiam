"""Bộ đọc xlsx nhanh, tách nhiều số trong một ô, khóa SĐT, CSV mã hoá cũ, đếm dòng xem trước."""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path

from control_plane import db, xlsx_fast
from control_plane.column_detect import looks_like_name
from control_plane.customers import (
    clean_address,
    extract_phone,
    ingest_customers,
    phones_from_text,
    phones_in_text,
    split_address_lines,
)
from control_plane.excel_import import (
    SharedStringStore,
    _fill_shared_strings,
    _iter_sheet_rows,
    count_data_rows,
    iter_csv_rows,
    iter_mapped_file,
    mapping_for_file,
    phones_from_files,
    preview_table,
)
from control_plane.xlsx_fast import LazySharedStrings, iter_sheet_rows

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_xlsx(sheets: list[str], sst: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        nodes = "".join(
            f'<sheet name="S{index}" sheetId="{index}" r:id="rId{index}"/>' for index in range(1, len(sheets) + 1)
        )
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>{nodes}</sheets></workbook>')
        rels = "".join(
            f'<Relationship Id="rId{index}" Type="{REL}/worksheet" Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        for index, xml in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
        if sst is not None:
            archive.writestr("xl/sharedStrings.xml", sst)
    return buffer.getvalue()


def sheet(rows: str, prefix: str = "") -> str:
    p = f"{prefix}:" if prefix else ""
    ns = f' xmlns:{prefix}="{NS}"' if prefix else f' xmlns="{NS}"'
    return f'<?xml version="1.0" encoding="UTF-8"?>\r<{p}worksheet{ns}><{p}sheetData>{rows}</{p}sheetData></{p}worksheet>'


def sst(items: str, prefix: str = "") -> str:
    p = f"{prefix}:" if prefix else ""
    ns = f' xmlns:{prefix}="{NS}"' if prefix else f' xmlns="{NS}"'
    return f'<?xml version="1.0" encoding="UTF-8"?><{p}sst{ns}>{items}</{p}sst>'


def old_rows(payload: bytes, index: int = 1) -> list[list[str]]:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    store = SharedStringStore()
    try:
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as handle:
                _fill_shared_strings(handle, store)
        with archive.open(f"xl/worksheets/sheet{index}.xml") as handle:
            return list(_iter_sheet_rows(handle, store))
    finally:
        store.close()
        archive.close()


def new_rows(payload: bytes, index: int = 1) -> list[list[str]]:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    shared = LazySharedStrings(archive, tempfile.gettempdir())
    try:
        with archive.open(f"xl/worksheets/sheet{index}.xml") as handle:
            return list(iter_sheet_rows(handle, shared))
    finally:
        shared.close()
        archive.close()


MIXED_ROWS = (
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
    '<row r="2"><c r="A2" s="1" t="s"><v>2</v></c><c r="C2"><v>901222333</v></c><c r="D2" t="n"><v>1.00E+15</v></c></row>'
    '<row r="3" spans="1:4"><c r="A3" t="str"><f>A1&amp;B1</f><v>Lan &amp; Minh</v></c>'
    '<c r="B3" t="b"><v>1</v></c><c r="C3" t="e"><v>#N/A</v></c></row>'
    '<row r="4"/>'
    '<row r="5"><c r="B5" t="inlineStr"><is><t xml:space="preserve">  Huế  </t></is></c><c r="A5" t="s"><v>3</v></c></row>'
    '<row r="6"><c t="s"><v>0</v></c><c><v>12</v></c></row>'
    '<row r="7"><c r="A7" t="s" s="2"><v>4</v></c><c r="B7" s="3"/></row>'
    '<row r="8"><c r="AA8"><v>5.0</v></c></row>'
    '<row r="9"><c r="A9" s="1"/><c r="B9" s="1"/></row>'
)
MIXED_SST = (
    "<si><t>Tên</t></si><si><t>SĐT</t></si>"
    '<si><r><rPr><b/></rPr><t>Nguyễn</t></r><r><t xml:space="preserve"> Văn A</t></r><rPh sb="0" eb="1"><t>X</t></rPh></si>'
    "<si><t>0901&amp;222 &#233;</t></si><si/>"
)


class FastSheetParityTests(unittest.TestCase):
    def test_mixed_cells_match_elementtree(self) -> None:
        payload = make_xlsx([sheet(MIXED_ROWS)], sst(MIXED_SST))
        self.assertEqual(new_rows(payload), old_rows(payload))
        rows = new_rows(payload)
        self.assertEqual(rows[0], ["Tên", "SĐT"])
        self.assertEqual(rows[1], ["Nguyễn Văn A", "", "901222333", "1000000000000000"])
        self.assertEqual(rows[2], ["Lan & Minh", "1", "#N/A"])
        self.assertEqual(rows[3], [])
        self.assertEqual(rows[4], ["0901&222 é", "Huế"])
        self.assertEqual(rows[5], ["Tên", "12"])
        self.assertEqual(len(rows[7]), 27)
        self.assertEqual(rows[7][26], "5")
        self.assertEqual(rows[8], ["", ""])

    def test_namespace_prefix_reads_same_rows(self) -> None:
        prefixed_rows = MIXED_ROWS.replace("<row", "<x:row").replace("</row>", "</x:row>")
        prefixed_rows = prefixed_rows.replace("<c ", "<x:c ").replace("<c>", "<x:c>").replace("</c>", "</x:c>")
        prefixed_rows = prefixed_rows.replace("<v>", "<x:v>").replace("</v>", "</x:v>")
        prefixed_rows = prefixed_rows.replace("<f>", "<x:f>").replace("</f>", "</x:f>")
        prefixed_rows = prefixed_rows.replace("<is>", "<x:is>").replace("</is>", "</x:is>")
        prefixed_rows = prefixed_rows.replace("<t ", "<x:t ").replace("</t>", "</x:t>")
        prefixed_sst = MIXED_SST.replace("<si", "<x:si").replace("</si>", "</x:si>").replace("<t", "<x:t")
        prefixed_sst = prefixed_sst.replace("</t>", "</x:t>").replace("<r>", "<x:r>").replace("</r>", "</x:r>")
        prefixed_sst = prefixed_sst.replace("<rPr>", "<x:rPr>").replace("</rPr>", "</x:rPr>").replace("<b/>", "<x:b/>")
        prefixed_sst = prefixed_sst.replace("<rPh", "<x:rPh").replace("</rPh>", "</x:rPh>")
        plain = make_xlsx([sheet(MIXED_ROWS)], sst(MIXED_SST))
        prefixed = make_xlsx([sheet(prefixed_rows, "x")], sst(prefixed_sst, "x"))
        self.assertEqual(new_rows(prefixed), new_rows(plain))

    def test_tiny_chunks_keep_every_row(self) -> None:
        strings = "".join(f"<si><t>Khách {index}</t></si>" for index in range(600))
        rows = "".join(
            f'<row r="{index + 1}"><c r="A{index + 1}" t="s"><v>{index}</v></c><c r="B{index + 1}"><v>90{index:07d}</v></c></row>'
            + ('<row r="9999" ht="21"/>' if index % 50 == 0 else "")
            for index in range(600)
        )
        payload = make_xlsx([sheet(rows)], sst(strings))
        previous = xlsx_fast.READ_CHUNK
        xlsx_fast.READ_CHUNK = 97
        try:
            fast = new_rows(payload)
        finally:
            xlsx_fast.READ_CHUNK = previous
        self.assertEqual(fast, old_rows(payload))
        self.assertEqual(len([row for row in fast if row]), 600)

    def test_excel_line_break_escape_is_decoded(self) -> None:
        payload = make_xlsx(
            [sheet('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Huế_x000D_\nNam</t></is></c></row>')],
            sst("<si><t>12 Lê Lợi_x000D_\nQuận 1 _x005F_x0041_</t></si>"),
        )
        self.assertEqual(new_rows(payload), [["12 Lê Lợi Quận 1 _x0041_", "Huế Nam"]])

    def test_shared_strings_load_only_what_is_needed(self) -> None:
        strings = "".join(f"<si><t>Chuỗi {index}</t></si>" for index in range(5000))
        payload = make_xlsx([sheet("")], sst(strings))
        archive = zipfile.ZipFile(io.BytesIO(payload))
        previous = xlsx_fast.READ_CHUNK
        xlsx_fast.READ_CHUNK = 1024
        shared = LazySharedStrings(archive, tempfile.gettempdir())
        try:
            self.assertEqual(shared[0], "Chuỗi 0")
            self.assertFalse(shared._done)
            self.assertEqual(shared[4999], "Chuỗi 4999")
            self.assertEqual(shared[123], "Chuỗi 123")
            self.assertEqual(len(shared), 5000)
            with self.assertRaises(IndexError):
                shared[5000]
        finally:
            xlsx_fast.READ_CHUNK = previous
            shared.close()
            archive.close()


class PhoneTextTests(unittest.TestCase):
    def test_two_phones_in_one_cell(self) -> None:
        self.assertEqual(phones_in_text("0901222333 0912345678"), ["0901222333", "0912345678"])
        self.assertEqual(phones_in_text("0901 222 333 - 0912.345.678"), ["0901222333", "0912345678"])
        self.assertEqual(phones_in_text("SĐT: 0901222333, 84987654321"), ["0901222333", "0987654321"])
        self.assertEqual(phones_in_text("(+84) 901-222-333"), ["0901222333"])
        self.assertEqual(phones_in_text("(028) 3822 1234"), ["02838221234"])
        self.assertEqual(phones_in_text("01689123456"), ["01689123456"])

    def test_numbers_that_are_not_phones(self) -> None:
        self.assertEqual(phones_in_text("12/10/2026"), [])
        self.assertEqual(phones_in_text("COD 1.200.000 và 12.000.000"), [])
        self.assertEqual(phones_in_text("100012345678901"), [])
        self.assertEqual(phones_in_text("Mã 3456789012"), [])

    def test_extract_phone_prefers_real_number(self) -> None:
        self.assertEqual(extract_phone("0901222333 - 0912345678"), "0901222333")
        self.assertEqual(extract_phone("0901222333 12"), "0901222333")
        self.assertEqual(extract_phone("12345678"), "12345678")
        self.assertEqual(extract_phone("+1 202 555 0123"), "+12025550123")
        self.assertEqual(extract_phone("100012345678901"), "")
        self.assertEqual(extract_phone("9.01222333E8"), "0901222333")

    def test_pasted_rows_skip_junk_columns(self) -> None:
        phones, stats = phones_from_text(
            "Lan\t0901222333\t12000000\n0912345678 / 0987654321\n84901222333\n12345678\nkhông có số"
        )
        self.assertEqual(phones, ["0901222333", "0912345678", "0987654321", "12345678"])
        self.assertEqual(stats["duplicate"], 1)
        self.assertEqual(stats["invalid"], 1)


class PhoneKeyTests(unittest.TestCase):
    def test_same_number_same_key(self) -> None:
        forms = ["0901222333", "84901222333", "+84 901 222 333", "084901222333", "0084901222333", "901222333", "00901222333", "840901222333"]
        self.assertEqual({db.phone_key(form) for form in forms}, {"901222333"})
        self.assertEqual(db.phone_key("01689123456"), db.phone_key("1689123456"))
        self.assertEqual(db.phone_key("02838221234"), db.phone_key("842838221234"))
        self.assertNotEqual(db.phone_key("12345678"), db.phone_key("012345678"))

    def test_key_lookup_matches_variant_lookup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-key-") as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            stored = [
                ("a", "Lan", "0901222333"),
                ("b", "Minh", "84912345678"),
                ("c", "Hoa", "987654321"),
                ("d", "Cũ", "1689123456"),
                ("e", "Lạ", "12345678"),
            ]
            now = "2026-01-01T00:00:00+00:00"
            db.replace_customers(
                path,
                [{"id": cid, "name": name, "phone": phone, "createdAt": now, "updatedAt": now} for cid, name, phone in stored],
                [],
            )
            queries = ["84901222333", "0912345678", "0987654321", "01689123456", "12345678", "0900000000"]
            conn = db.connect(path)
            try:
                self.assertTrue(db.phone_keys_ready(conn))
                by_key = db.customers_matching_phones(conn, queries)
                conn.execute("DELETE FROM kv_meta WHERE key='phone_key_ready'")
                conn.commit()
                self.assertFalse(db.phone_keys_ready(conn))
                by_variant = db.customers_matching_phones(conn, queries)
            finally:
                conn.close()
            names = lambda found: {phone: [row["name"] for row in rows] for phone, rows in found.items()}
            self.assertEqual(names(by_key), names(by_variant))
            self.assertEqual(names(by_key)["84901222333"], ["Lan"])
            self.assertEqual(names(by_key)["01689123456"], ["Cũ"])
            self.assertEqual(names(by_key)["0900000000"], [])

    def test_backfill_old_rows_then_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-backfill-") as folder:
            path = Path(folder) / "server.db"
            db.init_db(path)
            conn = sqlite3.connect(path)
            conn.executemany(
                "INSERT INTO customers(id, name, phone, created_at, updated_at) VALUES(?,?,?,?,?)",
                [(f"k{index}", f"K{index}", f"0903{index:06d}", "t", "t") for index in range(300)],
            )
            conn.execute("DELETE FROM kv_meta")
            conn.commit()
            conn.close()
            self.assertEqual(db.backfill_phone_keys(path, batch=64), 300)
            found = db.customers_for_phones(path, ["84903000123"])
            self.assertEqual([row["name"] for row in found["84903000123"]], ["K123"])


class CleanTextTests(unittest.TestCase):
    def test_long_lines_are_cut_by_line_and_stay_stable(self) -> None:
        lines = [f"Địa chỉ số {index} " + ("x" * 230) for index in range(14)]
        cleaned = clean_address("\n".join(lines))
        parts = cleaned.split("\n")
        self.assertEqual(len(parts), 12)
        self.assertTrue(all(len(part) <= 200 and part == part.strip() for part in parts))
        self.assertEqual(clean_address(cleaned), cleaned)
        self.assertEqual(split_address_lines("a" * 199 + " b"), ["a" * 199])

    def test_cp1258_csv_after_ascii_head(self) -> None:
        def cp1258(text: str) -> bytes:
            decomposed = unicodedata.normalize("NFD", text)
            out = []
            for char in decomposed:
                if char in "\u0302\u0306\u031b" and out:
                    out[-1] = unicodedata.normalize("NFC", out[-1] + char)
                else:
                    out.append(char)
            return "".join(out).encode("cp1258")

        head = "".join(f"K{index},090{index:07d},Hue\n" for index in range(400))
        tail = "Nguyễn Văn Lan,0901222333,Huế\n"
        with tempfile.TemporaryDirectory(prefix="khach-csv-") as folder:
            path = Path(folder) / "cu.csv"
            path.write_bytes(cp1258("Tên,SĐT,Địa chỉ\n" + head + tail))
            rows = list(iter_csv_rows(path))
            from_bytes = list(iter_csv_rows(path.read_bytes()))
        self.assertEqual(len(rows), 402)
        self.assertEqual(rows[-1], ["Nguyễn Văn Lan", "0901222333", "Huế"])
        self.assertEqual(rows[0], ["Tên", "SĐT", "Địa chỉ"])
        self.assertEqual(from_bytes, rows)

    def test_composed_and_combining_marks_are_one_address_line(self) -> None:
        composed = unicodedata.normalize("NFC", "Huế")
        combining = unicodedata.normalize("NFD", "Huế")
        self.assertNotEqual(composed, combining)
        self.assertEqual(split_address_lines(f"{combining}\n{composed}"), [combining])


class CountAndMappingTests(unittest.TestCase):
    def _three_sheets(self) -> bytes:
        first = sheet(
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
            + "".join(
                f'<row r="{index}"><c r="A{index}" t="inlineStr"><is><t>Khách {index}</t></is></c>'
                f'<c r="B{index}"><v>90{index:07d}</v></c><c r="C{index}" t="inlineStr"><is><t>Huế</t></is></c></row>'
                for index in range(2, 252)
            )
            + '<row r="300"><c r="A300" s="4"/><c r="B300" s="4"/></row><row r="301" ht="20"/>'
        )
        second = sheet(
            "".join(
                f'<row r="{index}"><c r="A{index}" t="inlineStr"><is><t>Lan {index}</t></is></c>'
                f'<c r="B{index}"><v>91{index:07d}</v></c></row>'
                for index in range(1, 4)
            )
        )
        third = sheet('<row r="1" ht="21"/><row r="2"><c r="A2" s="65"/><c r="B2" s="65"/></row>')
        return make_xlsx([first, second, third], sst("<si><t>Tên khách</t></si><si><t>SĐT</t></si><si><t>Địa chỉ</t></si>"))

    def test_preview_count_matches_rows_read_on_import(self) -> None:
        payload = self._three_sheets()
        with tempfile.TemporaryDirectory(prefix="khach-count-") as folder:
            path = Path(folder) / "ba-sheet.xlsx"
            path.write_bytes(payload)
            self.assertEqual(count_data_rows(path.name, path), 253)
            preview = preview_table(path.name, path)
            self.assertTrue(preview["truncated"])
            self.assertEqual(preview["rowCount"], 253)
            mapping = {"name": {"index": 0, "header": "Tên khách"}, "phone": {"index": 1, "header": "SĐT"}}
            counts: dict[str, int] = {}
            file_mapping = mapping_for_file(mapping, path.name, path)
            _width, rows = iter_mapped_file(path.name, path, file_mapping, None, counts=counts, chosen=mapping)
            rows = list(rows)
        self.assertEqual(counts["fileRows"], 253)
        self.assertEqual(len(rows), 253)

    def test_sheet_with_other_layout_maps_its_own_columns(self) -> None:
        first = sheet(
            "".join(
                f'<row r="{index}"><c r="A{index}" t="inlineStr"><is><t>Khách {index}</t></is></c>'
                f'<c r="B{index}"><v>90{index:07d}</v></c><c r="C{index}" t="inlineStr"><is><t>Huế</t></is></c></row>'
                for index in range(1, 30)
            )
        )
        second = sheet(
            '<row r="1"><c r="A1" t="inlineStr"><is><t>STT</t></is></c><c r="B1" t="inlineStr"><is><t>Số điện thoại</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>Tên khách hàng</t></is></c><c r="D1" t="inlineStr"><is><t>Địa chỉ</t></is></c></row>'
            + "".join(
                f'<row r="{index}"><c r="A{index}"><v>{index - 1}</v></c><c r="B{index}"><v>98{index:07d}</v></c>'
                f'<c r="C{index}" t="inlineStr"><is><t>Chị Mai {index}</t></is></c>'
                f'<c r="D{index}" t="inlineStr"><is><t>12 Lê Lợi, Đà Nẵng</t></is></c></row>'
                for index in range(2, 6)
            )
        )
        payload = make_xlsx([first, second])
        mapping = {
            "name": {"index": 0, "header": "Cột A"},
            "phone": {"index": 1, "header": "Cột B"},
            "address": {"index": 2, "header": "Cột C", "columns": [{"index": 2, "header": "Cột C"}]},
        }
        with tempfile.TemporaryDirectory(prefix="khach-sheets-") as folder:
            path = Path(folder) / "hai-kieu.xlsx"
            path.write_bytes(payload)
            file_mapping = mapping_for_file(mapping, path.name, path)
            _width, rows = iter_mapped_file(path.name, path, file_mapping, None, counts={}, chosen=mapping)
            rows = list(rows)
        later = [row for row in rows if row["phone"].startswith("098")]
        self.assertEqual(len(later), 4)
        self.assertTrue(all(row["name"].startswith("Chị Mai") for row in later), later)
        self.assertTrue(all("Lê Lợi" in row["address"] for row in later), later)

    def test_name_column_is_people_not_repeated_labels(self) -> None:
        surnames = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ"]
        given = ["Lan", "Minh", "Hoa", "Tú", "Nam Anh", "Thu Trang", "Quốc Bảo"]
        labels = ["Áo thun trơn", "Bánh tráng trộn", "Nước mắm cốt"]
        self.assertTrue(all(looks_like_name(label) for label in labels))
        self.assertFalse(looks_like_name("Long An"))

        def person(index: int) -> str:
            return "Long An" if index % 15 == 0 else f"{surnames[index % 6]} {given[index % 7]}"

        body = "".join(
            f'<row r="{index}"><c r="A{index}"><v>90{index:07d}</v></c>'
            f'<c r="B{index}" t="inlineStr"><is><t>{person(index)}</t></is></c>'
            f'<c r="C{index}" t="inlineStr"><is><t>{index} Lê Lợi, phường {index % 9}, Huế</t></is></c>'
            f'<c r="D{index}"><v>91{index:07d}</v></c>'
            f'<c r="E{index}" t="inlineStr"><is><t>{labels[index % 3]}</t></is></c></row>'
            for index in range(1, 120)
        )
        with tempfile.TemporaryDirectory(prefix="khach-name-") as folder:
            path = Path(folder) / "tong.xlsx"
            path.write_bytes(make_xlsx([sheet(body)]))
            mapping = mapping_for_file({}, path.name, path)
        self.assertEqual(mapping["name"]["header"], "Cột B")
        self.assertEqual(mapping["phone"]["header"], "Cột A")
        self.assertEqual(mapping["address"]["header"], "Cột C")

    def test_lookup_file_reads_every_phone_column(self) -> None:
        payload = make_xlsx(
            [
                sheet(
                    '<row r="1"><c r="A1" t="inlineStr"><is><t>SĐT người gửi</t></is></c>'
                    '<c r="B1" t="inlineStr"><is><t>SĐT người nhận</t></is></c><c r="C1" t="inlineStr"><is><t>Ghi chú</t></is></c></row>'
                    '<row r="2"><c r="A2"><v>900000001</v></c><c r="B2"><v>901222333</v></c>'
                    '<c r="C2" t="inlineStr"><is><t>gọi 0912345678 - 0987654321 trước</t></is></c></row>'
                    '<row r="3"><c r="A3"><v>900000001</v></c><c r="B3" t="inlineStr"><is><t>0903111222 0903111333</t></is></c>'
                    '<c r="C3" t="inlineStr"><is><t>COD 12.000.000</t></is></c></row>'
                )
            ]
        )
        phones, reports = phones_from_files([("don.xlsx", payload)])
        self.assertEqual(
            phones,
            ["0900000001", "0901222333", "0912345678", "0987654321", "0900000001", "0903111222", "0903111333"],
        )
        self.assertEqual(reports[0]["phones"], 7)


class IngestFastPathTests(unittest.TestCase):
    def test_duplicate_rows_in_one_batch_are_saved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-dup-") as folder:
            path = Path(folder) / "server.db"
            now = "2026-01-01T00:00:00+00:00"
            rows = iter(
                [
                    {"name": "Lan", "phone": "0901222333", "address": "Huế"},
                    {"name": "Lan", "phone": "0901222333", "address": "Huế"},
                    {"name": "Minh", "phone": "0902000111"},
                    {"name": "", "phone": "0902000111", "address": "Đà Nẵng"},
                    {"name": "Minh", "phone": "84902000111"},
                ]
            )
            stats, _preview = ingest_customers(path, rows, now, flush_every=400)
            self.assertEqual((stats["created"], stats["updated"], stats["unchanged"]), (2, 1, 2))
            self.assertEqual(stats["total"], 2)
            again = iter([{"name": "Lan", "phone": "0901222333", "address": "Huế"}, {"name": "Minh", "phone": "0902000111", "address": "Đà Nẵng"}])
            second, _preview = ingest_customers(path, again, now, flush_every=400)
            found = db.customers_for_phones(path, ["0901222333", "0902000111"])
        self.assertEqual((second["created"], second["updated"], second["unchanged"]), (0, 0, 2))
        self.assertEqual([row["name"] for row in found["0901222333"]], ["Lan"])
        self.assertEqual(found["0902000111"][0]["address"], "Đà Nẵng")

    def test_two_phone_cell_and_variants_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="khach-ingest-") as folder:
            path = Path(folder) / "server.db"
            now = "2026-01-01T00:00:00+00:00"
            rows = iter(
                [
                    {"name": "Lan", "phone": "0901222333 - 0912345678", "address": "Huế"},
                    {"name": "", "phone": "84901222333", "address": "Đà Nẵng"},
                    {"name": "Minh", "phone": "901222333"},
                ]
            )
            stats, _preview = ingest_customers(path, rows, now, flush_every=50)
            self.assertEqual(stats["created"], 1)
            self.assertEqual(stats["updated"], 1)
            self.assertEqual(stats["unchanged"], 1)
            found = db.customers_for_phones(path, ["0901222333"])["0901222333"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Lan")
        self.assertEqual(found[0]["address"], "Huế\nĐà Nẵng")


if __name__ == "__main__":
    unittest.main()
