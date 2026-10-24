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

    def _file(self, name: str, headers: list[str], rows: list[list[str]]) -> tuple[str, tuple[str, bytes, str]]:
        return (
            "files",
            (name, write_xlsx(headers, rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        )

    def test_page_is_served(self) -> None:
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Kho khách Excel", home.text)
        self.assertIn("Chọn file Excel", home.text)
        self.assertEqual(home.headers.get("content-language"), "vi")
        self.assertIn('translate="no"', home.text)
        self.assertIn("notranslate", home.text)
        self.assertIn("color-scheme: only light", home.text)
        self.assertIn("word-spacing", home.text)
        self.assertIn(".sp {", home.text)
        self.assertIn("padSpaces", home.text)
        self.assertNotIn("font-weight: 650", home.text)
        self.assertIn("không dùng video", home.text)
        self.assertIn("không giới hạn số file", home.text)
        self.assertIn("dung lượng", home.text)
        self.assertIn("Thêm file", home.text)
        self.assertIn("địa chỉ hành chính", home.text)
        self.assertIn("hàng mua", home.text)
        self.assertIn("giá COD", home.text)
        self.assertIn("giới tính, ngày sinh, link", home.text)
        self.assertIn("3 * 60 * 60 * 1000", home.text)
        self.assertIn("orderContentColumns", home.text)
        self.assertIn("giới tính", home.text)
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
        self.assertIn("Khớp cột", data["detectNote"])

    def test_preview_skips_unlabeled_and_gender_columns(self) -> None:
        name, body = self._xlsx(
            "an.xlsx",
            ["A", "B", "C", "D", "E", "Giới tính"],
            [
                ["Nguyễn Lan", "0901222333", "Huế", "1000123456789", "@lan.hue", "female"],
                ["Trần Minh", "84901444555", "Ha Noi", "1000987654321", "@minh.hn", "male"],
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
        self.assertIsNone(data["suggested"]["facebookUid"])
        self.assertIsNone(data["suggested"]["tiktokId"])
        self.assertIn("số điện thoại", data["detectNote"])

    def test_import_repairs_gender_name_and_facebook_dump_address(self) -> None:
        name, body = self._xlsx(
            "dump.xlsx",
            ["Tên khách", "Số điện thoại", "Địa chỉ", "UID Facebook"],
            [
                [
                    "female",
                    "0903449598",
                    "1000183715327222\n84903449598\nNguyễn Ngân\nfemale\nhttps://fb.com/1000183715327222",
                    "1000183715327222",
                ]
            ],
        )
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "Số điện thoại"},
                        "address": {"index": 2, "header": "Địa chỉ"},
                        "facebookUid": {"index": 3, "header": "UID Facebook"},
                    }
                )
            },
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        item = self.client.get("/v1/customers", headers=self.headers).json()["items"][0]
        self.assertEqual(item["name"], "Nguyễn Ngân")
        self.assertEqual(item["phone"], "0903449598")
        self.assertEqual(item["address"], "")
        self.assertEqual(item["facebookUid"], "1000183715327222")

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
        self.assertEqual(suggested["orderContentColumns"], [3])
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
                        "orderContent": {
                            "index": 3,
                            "header": "Nội dung hàng mua",
                            "columns": [{"index": 3, "header": "Nội dung hàng mua"}],
                        },
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

    def test_preview_keeps_every_order_content_column(self) -> None:
        name, body = self._xlsx(
            "hang.xlsx",
            ["Tên khách", "Hàng mua", "Sản phẩm"],
            [["Nguyễn Lan", "Nước mắm", "chai 500ml"]],
        )
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["suggested"]["orderContentColumns"], [1, 2])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "orderContent": {
                            "columns": [
                                {"index": 1, "header": "Hàng mua"},
                                {"index": 2, "header": "Sản phẩm"},
                            ]
                        },
                    }
                )
            },
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        item = self.client.get("/v1/customers", headers=self.headers).json()["items"][0]
        self.assertEqual(item["orderContent"], "Nước mắm\nchai 500ml")

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

    def test_preview_and_import_many_files(self) -> None:
        files = [
            self._file(f"f{index}.xlsx", ["Tên khách", "SĐT"], [[f"K{index}", f"0901000{index:03d}"]])
            for index in range(12)
        ]
        preview = self.client.post("/v1/customers/preview", headers=self.headers, files=files)
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(len(body["files"]), 12)
        self.assertIn("Không giới hạn số file và dung lượng", body["applyNote"])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "SĐT"},
                    }
                )
            },
            files=[
                self._file(f"f{index}.xlsx", ["Tên khách", "SĐT"], [[f"K{index}", f"0901000{index:03d}"]])
                for index in range(12)
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 12)
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.json()["total"], 12)

    def test_upload_size_is_unlimited(self) -> None:
        from control_plane.settings import settings as live

        self.assertEqual(live.max_upload_mb, 0)
        self.assertIsNone(live.upload_limit_bytes())
        rows = [[f"K{index}", f"0901{index:06d}", f"Địa chỉ {index}"] for index in range(250)]
        name, body = self._xlsx("lon.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], rows)
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["files"][0]["rowCount"], 250)
        self.assertTrue(data["files"][0]["truncated"])
        self.assertIn("dung lượng", data["applyNote"])

    def test_bad_file_does_not_block_import(self) -> None:
        files = [
            self._file("ok1.xlsx", ["Tên khách", "SĐT"], [["Lan", "0901222333"]]),
            ("files", ("hong.xlsx", b"not-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            self._file("ok2.xlsx", ["Tên khách", "SĐT"], [["Hoa", "0901999888"]]),
        ]
        preview = self.client.post("/v1/customers/preview", headers=self.headers, files=files)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(len(preview.json()["files"]), 2)
        self.assertTrue(preview.json()["fileErrors"])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "SĐT"},
                    }
                )
            },
            files=[
                self._file("ok1.xlsx", ["Tên khách", "SĐT"], [["Lan", "0901222333"]]),
                ("files", ("hong.xlsx", b"not-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
                self._file("ok2.xlsx", ["Tên khách", "SĐT"], [["Hoa", "0901999888"]]),
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 2)
        self.assertTrue(imported.json()["fileErrors"])
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.json()["total"], 2)

    def test_reimport_same_rows_keeps_warehouse(self) -> None:
        mapping = json.dumps(
            {
                "name": {"index": 0, "header": "Tên khách"},
                "phone": {"index": 1, "header": "SĐT"},
                "address": {"index": 2, "header": "Địa chỉ"},
            }
        )
        first = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={"mapping": mapping},
            files=[self._file("lan.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], [["Lan", "0901222333", "Huế"]])],
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["stats"]["created"], 1)
        again = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={"mapping": mapping},
            files=[self._file("lan.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], [["Lan", "0901222333", "Huế"]])],
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["stats"]["unchanged"], 1)
        self.assertEqual(again.json()["stats"]["created"], 0)
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.json()["total"], 1)
        self.assertEqual(listed.json()["items"][0]["address"], "Huế")

    def test_lookup_skips_bad_file_and_uses_text(self) -> None:
        db.replace_customers(
            settings.db_path,
            [
                {
                    "id": "c1",
                    "name": "Lan",
                    "phone": "0901222333",
                    "address": "Huế",
                    "facebookUid": "",
                    "tiktokId": "",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                }
            ],
            [],
        )
        looked = self.client.post(
            "/v1/customers/lookup",
            headers=self.headers,
            data={"text": "0901222333"},
            files=[
                ("files", ("hong.xlsx", b"not-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            ],
        )
        self.assertEqual(looked.status_code, 200, looked.text)
        self.assertEqual(looked.json()["stats"]["found"], 1)
        self.assertTrue(looked.json()["fileErrors"])

    def test_list_caps_visible_items_without_hiding_total(self) -> None:
        now = "2026-01-01T00:00:00+00:00"
        db.replace_customers(
            settings.db_path,
            [
                {
                    "id": f"c{index}",
                    "name": f"K{index}",
                    "phone": f"0901{index:06d}",
                    "address": "",
                    "facebookUid": "",
                    "tiktokId": "",
                    "createdAt": now,
                    "updatedAt": now,
                }
                for index in range(2005)
            ],
            [],
        )
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        self.assertEqual(body["total"], 2005)
        self.assertEqual(len(body["items"]), 2000)
        self.assertEqual(body["count"], 2000)
        self.assertEqual(body["matched"], 2005)
        searched = self.client.get("/v1/customers?q=K", headers=self.headers)
        self.assertEqual(searched.status_code, 200, searched.text)
        found = searched.json()
        self.assertEqual(found["total"], 2005)
        self.assertEqual(found["matched"], 2005)
        self.assertEqual(found["count"], 2000)
        self.assertEqual(len(found["items"]), 2000)

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

    def test_import_batches_keep_every_row(self) -> None:
        from control_plane import customer_api as api

        previous = api.IMPORT_BATCH
        api.IMPORT_BATCH = 3
        try:
            rows = [[f"K{index}", f"0902{index:06d}", f"Địa chỉ {index}"] for index in range(11)]
            imported = self.client.post(
                "/v1/customers/import",
                headers=self.headers,
                data={
                    "mapping": json.dumps(
                        {
                            "name": {"index": 0, "header": "Tên khách"},
                            "phone": {"index": 1, "header": "SĐT"},
                            "address": {"index": 2, "header": "Địa chỉ"},
                        }
                    )
                },
                files=[self._file("lo.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], rows)],
            )
        finally:
            api.IMPORT_BATCH = previous
        self.assertEqual(imported.status_code, 200, imported.text)
        body = imported.json()
        self.assertEqual(body["stats"]["created"], 11)
        self.assertEqual(body["files"][0]["rows"], 11)
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.json()["total"], 11)
        phones = {item["phone"] for item in listed.json()["items"]}
        self.assertEqual(len(phones), 11)

    def test_preview_and_import_thousands_of_rows(self) -> None:
        rows = [[f"K{index}", f"0903{index:06d}", f"Huế {index}"] for index in range(1200)]
        name, body = self._xlsx("ngan.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], rows)
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["files"][0]["rowCount"], 1200)
        self.assertTrue(data["files"][0]["truncated"])
        self.assertIn("đĩa", data["applyNote"])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "SĐT"},
                        "address": {"index": 2, "header": "Địa chỉ"},
                    }
                )
            },
            files=[self._file("ngan.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], rows)],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 1200)
        self.assertEqual(imported.json()["total"], 1200)
        leftover = list((settings.data_dir / "uploads").glob("khach-up-*")) if (settings.data_dir / "uploads").exists() else []
        self.assertEqual(leftover, [])

    def test_import_keeps_all_unique_phones_across_batches(self) -> None:
        rows = [[f"K{index}", f"0905{index:06d}", f"Huế {index}"] for index in range(3500)]
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên khách"},
                        "phone": {"index": 1, "header": "SĐT"},
                        "address": {"index": 2, "header": "Địa chỉ"},
                    }
                )
            },
            files=[self._file("du.xlsx", ["Tên khách", "SĐT", "Địa chỉ"], rows)],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        body = imported.json()
        self.assertEqual(body["stats"]["incoming"], 3500)
        self.assertEqual(body["stats"]["created"], 3500)
        self.assertEqual(body["total"], 3500)
        listed = self.client.get("/v1/customers", headers=self.headers)
        self.assertEqual(listed.json()["total"], 3500)

    def test_import_headerless_file_keeps_name_phone_address(self) -> None:
        rows = [
            ["Nguyễn Lan", "0901222333", "12 Lê Lợi, Quận 1, TP.HCM"],
            ["Trần Minh", "0901444555", "Ha Noi"],
        ]
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Cột A"},
                        "phone": {"index": 1, "header": "Cột B"},
                        "address": {"index": 2, "header": "Cột C"},
                    }
                )
            },
            files=[self._file("khong-tieu-de.xlsx", rows[0], rows[1:])],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 2)
        listed = self.client.get("/v1/customers", headers=self.headers)
        items = {item["phone"]: item for item in listed.json()["items"]}
        self.assertEqual(items["0901222333"]["name"], "Nguyễn Lan")
        self.assertIn("Lê Lợi", items["0901222333"]["address"])
        self.assertEqual(items["0901444555"]["name"], "Trần Minh")

    def test_import_receiver_headers_keeps_name_phone_address(self) -> None:
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên người nhận"},
                        "phone": {"index": 1, "header": "SĐT người nhận"},
                        "address": {"index": 2, "header": "Địa chỉ người nhận"},
                    }
                )
            },
            files=[
                self._file(
                    "nhan.xlsx",
                    ["Tên người nhận", "SĐT người nhận", "Địa chỉ người nhận"],
                    [["Nguyễn Lan", "0901222333", "Huế"]],
                )
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        item = self.client.get("/v1/customers", headers=self.headers).json()["items"][0]
        self.assertEqual(item["name"], "Nguyễn Lan")
        self.assertEqual(item["phone"], "0901222333")
        self.assertEqual(item["address"], "Huế")


if __name__ == "__main__":
    unittest.main()
