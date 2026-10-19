"""Bộ lọc kết quả, file Excel, chuẩn hoá số, webhook và khoá API chỉ đọc."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from control_plane.phone_vn import carrier, normalize_vn, phone_columns
from control_plane.results_out import (
    IMPORT_MAX_BYTES,
    WEBHOOK_BATCH,
    api_key_ok,
    check_public_url,
    create_api_key,
    export_xlsx,
    list_api_keys,
    parse_import,
    post_json,
    revoke_api_key,
    save_webhook,
    saved_rows_after,
    send_webhook_batch,
    webhook_settings,
)
from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.video_store import (
    BUCKET_OK,
    BUCKET_UNOPENED,
    ResultFilter,
    begin_upload,
    claim,
    commit_upload,
    connect,
    finish,
    fold_text,
    import_results,
    init_db,
    result_page,
    result_summary,
)
from control_plane.xlsx import workbook

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheet_rows(payload: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [[cell.findtext("m:is/m:t", default="", namespaces=NS) for cell in row.findall("m:c", NS)] for row in root.find("m:sheetData", NS)]


def public(_host: str) -> list[str]:
    return ["93.184.216.34"]


def private(_host: str) -> list[str]:
    return ["10.0.0.5"]


class PhoneTests(unittest.TestCase):
    def test_normalize_and_carrier(self) -> None:
        self.assertEqual(normalize_vn("+84 912 345 678"), "0912345678")
        self.assertEqual(normalize_vn("84912345678"), "0912345678")
        self.assertEqual(normalize_vn("912345678"), "0912345678")
        self.assertEqual(normalize_vn("12345"), "")
        self.assertEqual(carrier("0961234567"), "Viettel")
        self.assertEqual(carrier("0912345678"), "Vinaphone")
        self.assertEqual(carrier("0901234567"), "MobiFone")
        self.assertEqual(carrier("0921234567"), "Vietnamobile")
        self.assertEqual(phone_columns("0201234567"), ("0201234567", "", "Đầu số chưa rõ nhà mạng"))
        self.assertEqual(phone_columns("abc")[2], "Không đúng dạng số di động Việt Nam")
        self.assertEqual(phone_columns(""), ("", "", ""))

    def test_fold_text_drops_accents(self) -> None:
        self.assertEqual(fold_text("Nguyễn Văn Đức"), "nguyen van duc")


class XlsxTests(unittest.TestCase):
    def test_cells_stay_text(self) -> None:
        payload = workbook("Ket qua", ["Số", "Tên"], [["0901234567", "Nguyễn \x07Văn"], ["0", "A&B <c>"]], [12, 20])
        rows = sheet_rows(payload)
        self.assertEqual(rows[0], ["Số", "Tên"])
        self.assertEqual(rows[1], ["0901234567", "Nguyễn Văn"])
        self.assertEqual(rows[2], ["0", "A&B <c>"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIn("[Content_Types].xml", archive.namelist())
            ElementTree.fromstring(archive.read("xl/workbook.xml"))
            ElementTree.fromstring(archive.read("xl/styles.xml"))


class FilterExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "video.db"
        init_db(self.db)
        self.first = self._read(
            "f1",
            "May A",
            Table(
                rows=[Row("0901234567", "Nguyễn Văn An", "@an_01"), Row("0961234567", "Trần Thị Bé", "@be50%")],
                unopened=[Unopened("0912345678", "Lê C")],
            ),
        )
        self.second = self._read(
            "f2",
            "",
            Table(rows=[Row("0981234567", "Phạm Đức", "@duc")], review=[Review("0921234567", "Hà", "Hà Vy", "@havy", "lệch tên")]),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self, sha: str, device: str, table: Table) -> int:
        created = begin_upload(self.db, name=f"{sha}.mp4", size_bytes=5, sha256=sha, device=device)
        video_id = int(created["id"])
        path = self.root / f"{video_id}.mp4"
        path.write_bytes(b"v")
        commit_upload(self.db, video_id, str(path))
        claim(self.db, 1)
        finish(self.db, video_id, table)
        return video_id

    def _phones(self, **kwargs) -> list[str]:
        return [item["phone"] for item in result_page(self.db, limit=100, filters=ResultFilter(**kwargs))["items"]]

    def test_filters(self) -> None:
        self.assertEqual(set(self._phones(query="nguyen van")), {"0901234567"})
        self.assertEqual(set(self._phones(query="ĐỨC")), {"0981234567"})
        self.assertEqual(set(self._phones(query="be50%")), {"0961234567"})
        self.assertEqual(set(self._phones(query="@an")), {"0901234567"})
        self.assertEqual(set(self._phones(query="096")), {"0961234567"})
        self.assertEqual(set(self._phones(device="May A")), {"0901234567", "0961234567", "0912345678"})
        self.assertEqual(set(self._phones(device="")), {"0981234567", "0921234567"})
        self.assertEqual(set(self._phones(bucket=BUCKET_UNOPENED)), {"0912345678"})
        self.assertEqual(set(self._phones(video_id=self.second, bucket=BUCKET_OK)), {"0981234567"})
        with connect(self.db) as conn:
            conn.execute("UPDATE results SET checked = 'ok' WHERE phone = '0901234567'")
            conn.execute("UPDATE results SET created_at = '2020-01-01T03:00:00+00:00' WHERE phone = '0961234567'")
        self.assertEqual(self._phones(checked="ok"), ["0901234567"])
        self.assertNotIn("0901234567", self._phones(checked="none"))
        self.assertEqual(self._phones(day_from="2020-01-01", day_to="2020-01-01"), ["0961234567"])
        self.assertNotIn("0961234567", self._phones(day_from="2020-01-02"))
        page = result_page(self.db, limit=100, filters=ResultFilter(query="nguyen"))
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["carrier"], "MobiFone")
        with self.assertRaises(ValueError):
            result_page(self.db, filters=ResultFilter(day_from="16/10/2026"))

    def test_export_follows_the_filter(self) -> None:
        rows = sheet_rows(export_xlsx(self.db, ResultFilter(device="May A")))
        self.assertEqual(rows[0][:3], ["Số điện thoại", "Số chuẩn", "Nhà mạng gốc"])
        phones = [row[0] for row in rows[1:]]
        self.assertEqual(phones, ["0901234567", "0961234567", "0912345678"])
        self.assertEqual(rows[1][2], "MobiFone")
        self.assertEqual(rows[1][8], "May A")
        self.assertEqual(rows[1][9], "VPS")
        self.assertEqual(rows[3][5], "Chưa mở hồ sơ")

    def test_summary_groups_reader_and_scan_day(self) -> None:
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET helper = 'May Doc' WHERE id = ?", (self.first,))
            conn.execute("UPDATE results SET created_at = '2020-01-01T03:00:00+00:00' WHERE phone = '0961234567'")
        whole = result_summary(self.db, ResultFilter())
        self.assertEqual(whole["total"], 5)
        self.assertEqual((whole["saved"], whole["review"], whole["unopened"]), (3, 1, 1))
        by_reader = {item["reader"]: item["total"] for item in whole["by_reader"]}
        self.assertEqual(by_reader["May Doc"], 3)
        self.assertEqual(by_reader["VPS"], 2)
        self.assertEqual(whole["readers"], ["May Doc", "VPS"])
        one_day = result_summary(self.db, ResultFilter(day_from="2020-01-01", day_to="2020-01-01"))
        self.assertEqual(one_day["total"], 1)
        self.assertEqual(one_day["by_day"][0]["day"], "2020-01-01")
        self.assertEqual(one_day["readers"], ["May Doc", "VPS"])
        only_vps = result_page(self.db, limit=20, filters=ResultFilter(reader="VPS"))
        self.assertEqual({item["phone"] for item in only_vps["items"]}, {"0981234567", "0921234567"})
        self.assertTrue(all(item["helper"] == "VPS" for item in only_vps["items"]))
        named = result_page(self.db, limit=20, filters=ResultFilter(reader="May Doc"))
        self.assertEqual(named["total"], 3)

    def test_import_adds_skips_saved_and_upgrades_unopened(self) -> None:
        with connect(self.db) as conn:
            conn.execute("UPDATE videos SET helper = 'May Doc' WHERE id = ?", (self.first,))
            conn.execute(
                "UPDATE results SET created_at = '2019-05-01T00:00:00+00:00' WHERE phone = '0912345678'"
            )
        first = (
            "Số điện thoại,Tên,Username,Loại,Lý do,Đọc bởi,Đọc lúc (giờ VN)\n"
            "0901111222,An,@an01,Đã lưu,,May Quet,01/01/2020 10:00\n"
            "khong-phai-so,X,@x,Đã lưu,,,\n"
            "0913333444,Be,,Chưa mở hồ sơ,,VPS,\n"
            ",Chi,@chi,Đã lưu,,,\n"
        )
        added = import_results(self.db, parse_import(first.encode("utf-8-sig"), "nap.csv"))
        self.assertEqual(added, {"added": 2, "updated": 0, "skipped": 0, "invalid": 2})
        stored = result_page(self.db, limit=20, filters=ResultFilter(reader="May Quet"))
        self.assertEqual(stored["items"][0]["helper"], "May Quet")
        self.assertEqual(stored["items"][0]["video_id"], 0)
        with connect(self.db) as conn:
            blank = conn.execute("SELECT reader FROM results WHERE phone = '0913333444'").fetchone()
        self.assertEqual(blank["reader"], "")
        again = import_results(self.db, parse_import(first.encode("utf-8"), "nap.csv"))
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped"], 2)
        self.assertEqual(again["invalid"], 2)
        upgrade = (
            "Số điện thoại;Tên;Username;Loại;Đọc bởi\n"
            "0913333444;Bé Hai;@behai;Đã lưu;May Quet\n"
            "0912345678;Tên mới;@moi;Đã lưu;Khac\n"
            "0901234567;Tên đè;@de;Đã lưu;Khac\n"
        )
        changed = import_results(self.db, parse_import(upgrade.encode("utf-8"), "sua.csv"))
        self.assertEqual(changed["updated"], 2)
        self.assertEqual(changed["skipped"], 1)
        opened = result_page(self.db, limit=5, filters=ResultFilter(query="0913333444"))["items"][0]
        self.assertEqual(opened["bucket"], "Đã lưu")
        self.assertEqual(opened["username"], "@behai")
        self.assertEqual(opened["helper"], "May Quet")
        kept_day = result_page(self.db, limit=5, filters=ResultFilter(query="0912345678"))["items"][0]
        self.assertEqual(kept_day["created_at"], "2019-05-01T00:00:00+00:00")
        self.assertEqual(kept_day["helper"], "May Doc")
        self.assertEqual(kept_day["username"], "@moi")
        untouched = result_page(self.db, limit=5, filters=ResultFilter(query="0901234567"))["items"][0]
        self.assertEqual(untouched["name"], "Nguyễn Văn An")
        self.assertEqual(untouched["username"], "@an_01")
        scanned = result_page(self.db, limit=5, filters=ResultFilter(day_from="2020-01-01", day_to="2020-01-01"))
        self.assertEqual([item["phone"] for item in scanned["items"]], ["0901111222"])

    def test_xlsx_round_trip_and_shared_strings(self) -> None:
        parsed = parse_import(export_xlsx(self.db, ResultFilter()), "ket-qua.xlsx")
        phones = {row["phone"] for row in parsed}
        self.assertIn("0901234567", phones)
        self.assertIn("0912345678", phones)
        outcome = import_results(self.db, parsed)
        self.assertEqual(outcome["added"], 0)
        shared = parse_import(_shared_sheet(), "chung.xlsx")
        self.assertEqual(shared[0]["phone"], "933456789")
        self.assertEqual(shared[0]["name"], "Lê Chung")
        loaded = import_results(self.db, shared)
        self.assertEqual(loaded["added"], 1)
        row = result_page(self.db, limit=5, filters=ResultFilter(reader="May Chung"))["items"][0]
        self.assertEqual(row["phone"], "0933456789")
        self.assertEqual(row["name"], "Lê Chung")
        self.assertEqual(row["helper"], "May Chung")

    def test_import_rejects_a_huge_file_and_too_many_rows(self) -> None:
        with self.assertRaises(ValueError):
            parse_import(b"x" * (IMPORT_MAX_BYTES + 1), "to.csv")
        body = "Số điện thoại\n" + ("0912000111\n" * 100001)
        with self.assertRaises(ValueError):
            parse_import(body.encode("utf-8"), "nhieu.csv")

    def test_api_keys(self) -> None:
        made = create_api_key(self.db, "Phần mềm kế toán")
        self.assertTrue(str(made["key"]).startswith("gk_"))
        self.assertTrue(api_key_ok(self.db, str(made["key"])))
        self.assertFalse(api_key_ok(self.db, "gk_sai"))
        listed = list_api_keys(self.db)
        self.assertEqual(listed[0]["prefix"], str(made["key"])[:7])
        self.assertNotIn("key", listed[0])
        self.assertIsNotNone(listed[0]["last_used_at"])
        rows = saved_rows_after(self.db, 0, 10)
        self.assertEqual([row["phone"] for row in rows], ["0901234567", "0961234567", "0981234567"])
        self.assertEqual(saved_rows_after(self.db, rows[1]["id"], 10)[0]["phone"], "0981234567")
        self.assertTrue(revoke_api_key(self.db, int(made["id"])))
        self.assertFalse(api_key_ok(self.db, str(made["key"])))

    def test_webhook_sends_new_rows_once(self) -> None:
        with self.assertRaises(ValueError):
            check_public_url("ftp://example.com", public)
        with self.assertRaises(ValueError):
            check_public_url("https://example.com/hook", private)
        save_webhook(self.db, "https://example.com/hook", include_old=False, resolve=public)
        self.assertEqual(webhook_settings(self.db)["waiting"], 0)
        sent: list[dict] = []
        self.assertEqual(send_webhook_batch(self.db, lambda url, payload: sent.append(payload), resolve=public), 0)
        self._read("f3", "May B", Table(rows=[Row("0331234567", "Mới", "@moi")]))

        def broken(url: str, payload: dict) -> None:
            raise OSError("mạng đứt")

        with self.assertRaises(OSError):
            send_webhook_batch(self.db, broken, resolve=public)
        state = webhook_settings(self.db)
        self.assertEqual(state["waiting"], 1)
        self.assertIn("mạng đứt", state["error"])
        self.assertEqual(send_webhook_batch(self.db, lambda url, payload: sent.append(payload), resolve=public), 1)
        self.assertEqual(sent[0]["rows"][0]["phone"], "0331234567")
        self.assertEqual(sent[0]["rows"][0]["carrier"], "Viettel")
        self.assertEqual(send_webhook_batch(self.db, lambda url, payload: sent.append(payload), resolve=public), 0)
        state = webhook_settings(self.db)
        self.assertEqual((state["sent"], state["error"], state["waiting"]), (1, "", 0))
        save_webhook(self.db, "https://example.com/hook", include_old=True, resolve=public)
        self.assertEqual(webhook_settings(self.db)["waiting"], 4)
        self.assertGreaterEqual(WEBHOOK_BATCH, 4)
        save_webhook(self.db, "", resolve=public)
        self.assertEqual(webhook_settings(self.db)["url"], "")
        self.assertEqual(json.loads(json.dumps(sent))[0]["source"], "ghiam")

    def test_webhook_redirect_keeps_the_body_and_stays_public(self) -> None:
        seen: list[tuple[str, str, bytes]] = []

        def exchange(host: str, ip: str, _port: int, _scheme: str, target: str, body: bytes, _timeout: float):
            seen.append((host, ip, body))
            if target == "/hook":
                return 302, "https://sheets.example/exec"
            return 200, ""

        post_json("https://example.com/hook", {"rows": [1]}, resolve=public, exchange=exchange)
        self.assertEqual(seen[0][0], "example.com")
        self.assertEqual(seen[0][1], "93.184.216.34")
        self.assertEqual(seen[1][0], "sheets.example")
        self.assertEqual(seen[0][2], seen[1][2])
        self.assertIn("rows", seen[1][2].decode("utf-8"))

        def inward(host: str, ip: str, _port: int, _scheme: str, target: str, body: bytes, _timeout: float):
            return 302, "http://127.0.0.1/latest"

        with self.assertRaises(ValueError):
            post_json("https://example.com/hook", {"rows": [1]}, resolve=public, exchange=inward)
        with self.assertRaises(ValueError):
            check_public_url("http://10.1.1.1/secret", public)


def _shared_sheet() -> bytes:
    shared = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<si><t>Số điện thoại</t></si>
<si><t>Tên</t></si>
<si><t>Username</t></si>
<si><t>Loại</t></si>
<si><t>Đọc bởi</t></si>
<si><r><t>Lê </t></r><r><t>Chung</t></r></si>
<si><t>@chung</t></si>
<si><t>Đã lưu</t></si>
<si><t>May Chung</t></si>
</sst>"""
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1">
<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c>
</row>
<row r="2">
<c r="A2"><v>933456789</v></c><c r="B2" t="s"><v>5</v></c><c r="C2" t="s"><v>6</v></c><c r="D2" t="s"><v>7</v></c><c r="E2" t="s"><v>8</v></c>
</row>
</sheetData>
</worksheet>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
