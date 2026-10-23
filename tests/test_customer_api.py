"""HTTP API kho khách: hỏi cột rồi nạp Excel."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="fb-khach-")
os.environ["CONTROL_DATA_DIR"] = _TMP
os.environ["CONTROL_DB"] = str(Path(_TMP) / "server.db")
os.environ["CONTROL_TOKEN"] = "test-token"
os.environ["CONTROL_PROXIES_FILE"] = str(Path(_TMP) / "proxies.txt")
os.environ["CONTROL_PACKAGES_DIR"] = str(Path(_TMP) / "packages")
os.environ["CONTROL_PROXY_CHECK_SEC"] = "86400"
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from control_plane import db  # noqa: E402
from control_plane.app import app  # noqa: E402
from control_plane.excel_import import write_xlsx  # noqa: E402
from control_plane.settings import settings  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


class CustomerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.data_dir = Path(_TMP)
        settings.db_path = Path(_TMP) / "server.db"
        settings.token = "test-token"
        settings.page_token = "page-token"
        if settings.db_path.exists():
            settings.db_path.unlink()
        db.init_db(settings.db_path)
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    def _xlsx(self, name: str, headers: list[str], rows: list[list[str]]) -> tuple[str, io.BytesIO]:
        payload = write_xlsx(headers, rows)
        return name, io.BytesIO(payload)

    def test_page_is_served(self) -> None:
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Kho khách Excel", home.text)
        self.assertIn("Chọn file Excel", home.text)
        self.assertIn("không dùng video", home.text)
        self.assertIn("địa chỉ hành chính", home.text)
        self.assertIn("hàng mua", home.text)
        self.assertIn("giá COD", home.text)
        alias = self.client.get("/khach")
        self.assertEqual(alias.status_code, 200)
        self.assertIn("Cột nào là dữ liệu nào", alias.text)

    def test_preview_then_import_asks_for_columns(self) -> None:
        name, body = self._xlsx(
            "lan1.xlsx",
            ["Tên khách", "Số điện thoại khách", "Địa chỉ khách"],
            [["Lan", "0901222333", "Huế"]],
        )
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["files"][0]["rowCount"], 1)
        self.assertEqual(data["suggested"]["name"], 0)
        self.assertIn("mọi file", data["applyNote"])
        self.assertIn("Đã tự nhận", data["detectNote"])

    def test_preview_detects_unlabeled_columns_by_content(self) -> None:
        name, body = self._xlsx(
            "an.xlsx",
            ["A", "B", "C", "D", "E"],
            [
                ["Nguyễn Lan", "0901222333", "Huế", "1000123456789", "@lan.hue"],
                ["Trần Minh", "84901444555", "Ha Noi", "1000987654321", "@minh.hn"],
            ],
        )
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["suggested"]["name"], 0)
        self.assertEqual(data["suggested"]["phone"], 1)
        self.assertEqual(data["suggested"]["address"], 2)
        self.assertEqual(data["suggested"]["facebookUid"], 3)
        self.assertEqual(data["suggested"]["tiktokId"], 4)
        self.assertIn("đầu số nhà mạng", data["detectNote"])

    def test_preview_keeps_every_address_column(self) -> None:
        name, body = self._xlsx(
            "diachi.xlsx",
            ["Tên khách", "Số nhà", "Phường", "Tỉnh"],
            [["Nguyễn Lan", "12 Lê Lợi", "Quận 1", "TP.HCM"]],
        )
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["suggested"]["name"], 0)
        self.assertEqual(data["suggested"]["addressColumns"], [1])
        self.assertEqual(data["suggested"]["adminAddressColumns"], [2, 3])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
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
            },
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.json()["items"][0]["address"], "12 Lê Lợi\nQuận 1\nTP.HCM")

    def test_import_admin_address_order_and_cod(self) -> None:
        name, body = self._xlsx(
            "don.xlsx",
            ["Tên khách", "SĐT", "Địa chỉ hành chính", "Nội dung hàng mua", "Giá tiền COD"],
            [["Lan", "0901222333", "Phường Phú Hội, Huế", "Giày nam size 41", "230k"]],
        )
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        suggested = preview.json()["suggested"]
        self.assertEqual(suggested["name"], 0)
        self.assertEqual(suggested["phone"], 1)
        self.assertEqual(suggested["adminAddress"], 2)
        self.assertEqual(suggested["orderContent"], 3)
        self.assertEqual(suggested["codAmount"], 4)
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "SĐT"},
                        "adminAddress": {"index": 2, "header": "Địa chỉ hành chính"},
                        "orderContent": {"index": 3, "header": "Nội dung hàng mua"},
                        "codAmount": {"index": 4, "header": "Giá tiền COD"},
                    }
                )
            },
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        item = self.client.get("/v1/customers", headers=self.headers).json()["items"][0]
        self.assertEqual(item["adminAddress"], "Phường Phú Hội, Huế")
        self.assertEqual(item["orderContent"], "Giày nam size 41")
        self.assertEqual(item["codAmount"], "230000")

    def test_import_fills_missing_fields_on_later_file(self) -> None:
        name, body = self._xlsx(
            "lan1.xlsx",
            ["Tên khách", "Số điện thoại khách", "Địa chỉ khách"],
            [["Lan", "0901222333", "Huế"]],
        )
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "Số điện thoại khách"},
                        "address": {"index": 2, "header": "Địa chỉ khách"},
                    }
                )
            },
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 1)

        name, body = self._xlsx(
            "lan2.xlsx",
            ["Tên khách", "Số điện thoại khách", "UID Facebook", "ID TikTok"],
            [["Lan", "0901222333", "10001", "lan.hue"]],
        )
        filled = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "Số điện thoại khách"},
                        "facebookUid": {"index": 2, "header": "UID Facebook"},
                        "tiktokId": {"index": 3, "header": "ID TikTok"},
                    }
                )
            },
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(filled.status_code, 200, filled.text)
        self.assertEqual(filled.json()["stats"]["updated"], 1)
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        items = listed.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Lan")
        self.assertEqual(items[0]["address"], "Huế")
        self.assertEqual(items[0]["facebookUid"], "10001")
        self.assertEqual(items[0]["tiktokId"], "lan.hue")

    def test_bulk_phone_lookup_and_export(self) -> None:
        db.replace_customers(
            settings.db_path,
            [
                {
                    "id": "c1",
                    "name": "Lan",
                    "phone": "0901222333",
                    "address": "Huế",
                    "facebookUid": "10001",
                    "tiktokId": "lan.hue",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                }
            ],
            [],
        )
        looked = self.client.post(
            "/v1/customers/lookup",
            headers=self.headers,
            data={"text": "0901222333\n0901999888\nkhông phải số"},
        )
        self.assertEqual(looked.status_code, 200, looked.text)
        body = looked.json()
        self.assertEqual(body["stats"]["found"], 1)
        self.assertEqual(body["stats"]["missing"], 1)
        self.assertEqual(body["stats"]["invalid"], 1)
        self.assertEqual(body["items"][0]["customer"]["name"], "Lan")
        self.assertFalse(body["items"][1]["found"])

        name, file_body = self._xlsx("so.xlsx", ["SĐT"], [["0901222333"], ["0901777666"]])
        from_file = self.client.post(
            "/v1/customers/lookup",
            headers=self.headers,
            data={"text": ""},
            files=[("files", (name, file_body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(from_file.status_code, 200, from_file.text)
        self.assertEqual(from_file.json()["stats"]["found"], 1)
        self.assertEqual(from_file.json()["stats"]["missing"], 1)

        exported = self.client.post(
            "/v1/customers/lookup/export",
            headers=self.headers,
            data={"text": "0901222333\n0901999888"},
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn("spreadsheetml", exported.headers["content-type"])

    def test_export_xlsx_roundtrip(self) -> None:
        db.replace_customers(
            settings.db_path,
            [
                {
                    "id": "c1",
                    "name": "Hoa",
                    "phone": "0901888777",
                    "address": "",
                    "facebookUid": "",
                    "tiktokId": "hoa.tt",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                }
            ],
            [],
        )
        response = self.client.get("/v1/customers/export", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response.headers["content-type"])
        self.assertGreater(len(response.content), 100)


if __name__ == "__main__":
    unittest.main()
