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
    WEBHOOK_BATCH,
    api_key_ok,
    check_public_url,
    create_api_key,
    export_xlsx,
    list_api_keys,
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
    init_db,
    result_page,
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
        self.assertEqual(rows[3][5], "Chưa mở hồ sơ")

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


if __name__ == "__main__":
    unittest.main()
