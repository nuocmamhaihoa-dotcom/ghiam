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
        self.assertIn("applyChosenMapping", home.text)
        self.assertIn("Máy chủ sẽ tự lấy cột tên người", home.text)
        self.assertIn("file không lấy được dòng khách", home.text)
        self.assertIn("Mỗi file tự khớp cột", home.text)
        self.assertIn("Các lần nạp", home.text)
        self.assertIn("/v1/customers/sessions", home.text)
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
        self.assertIn("table.sheet", home.text)
        self.assertIn("display: table-cell", home.text)
        self.assertIn("overflow-wrap: anywhere", home.text)
        self.assertIn("parent.closest(\"#people tbody, #lookupPeople tbody, td.addr, .addr\")", home.text)
        self.assertIn("data-words", home.text)
        self.assertIn("watchSpaces", home.text)
        self.assertIn("Google Dịch đang bật", home.text)
        self.assertIn("Thời gian", home.text)
        self.assertIn("Tình trạng", home.text)
        self.assertIn("Máy đã tự chọn cột", home.text)
        self.assertIn("fileMaps", home.text)
        self.assertIn("Dòng đọc", home.text)
        self.assertIn("Dòng đọc cộng dồn", home.text)
        self.assertIn("Trùng SĐT hoặc nạp lại", home.text)
        self.assertIn("Giữ nguyên", home.text)
        self.assertIn("File còn thiếu", home.text)
        self.assertIn("Số điện thoại khác nhau", home.text)
        self.assertIn("Không có SĐT", home.text)
        self.assertIn("khach-build", home.text)
        self.assertIn("mỗi số điện thoại một hàng", home.text)
        self.assertIn("ô trống được bổ sung", home.text)
        self.assertIn("Không có số điện thoại thì bỏ qua, không lưu", home.text)
        self.assertIn("Số trên dòng là chuẩn lọc trùng", home.text)
        self.assertIn("Mọi dòng có số trong file mới đều được ghi", home.text)
        self.assertIn("Mỗi dòng có số điện thoại là một khách", home.text)
        self.assertIn("Số 84", home.text)
        self.assertIn("không lấy UID Facebook", home.text)
        self.assertIn("không bắt buộc chọn file", home.text)
        self.assertIn("lookupError", home.text)
        self.assertIn("không cần chọn file", home.text)
        self.assertIn("lookupJsonBody", home.text)
        self.assertIn("map-card", home.text)
        self.assertNotIn("data-label", home.text)
        self.assertNotIn("#people thead", home.text)
        self.assertNotIn("content: attr(data-label)", home.text)
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
        self.assertIn("Mỗi file tự khớp", data["applyNote"])
        self.assertIn("Khớp cột", data["detectNote"])
        self.assertEqual(len(data["groups"]), 1)
        self.assertEqual(data["groups"][0]["suggested"]["name"], 0)
        self.assertEqual(data["groups"][0]["mappedSamples"][0]["name"], "Lan")

    def test_preview_mixed_headerless_and_delivery_keeps_two_layouts(self) -> None:
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[
                self._file(
                    "khong-tieu-de.xlsx",
                    ["Nguyễn Lan", "0901222333", "12 Lê Lợi, Huế"],
                    [],
                ),
                self._file(
                    "delivery_export.xlsx",
                    [
                        "STT",
                        "Mã vận đơn",
                        "Đơn vị vận chuyển",
                        "Tên người nhận",
                        "SĐT người nhận",
                        "Địa chỉ nhận",
                        "Tỉnh/Thành phố nhận",
                        "Phường/Xã nhận",
                        "Tên hàng hóa",
                        "Tiền thu hộ COD",
                    ],
                    [["1", "VD1", "GHN", "Trần Minh", "0901444555", "12 Lê Lợi", "Hồ Chí Minh", "Quận 1", "Nước mắm", "150000"]],
                ),
            ],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(len(data["groups"]), 2)
        by_name = {}
        for group in data["groups"]:
            for item in group["files"]:
                by_name[item["name"]] = group
        headerless = by_name["khong-tieu-de.xlsx"]
        delivery = by_name["delivery_export.xlsx"]
        self.assertEqual(delivery["headers"][3], "Tên người nhận")
        self.assertEqual(delivery["suggested"]["name"], 3)
        self.assertEqual(delivery["suggested"]["phone"], 4)
        self.assertEqual(delivery["suggested"]["address"], 5)
        self.assertEqual(delivery["mappedSamples"][0]["name"], "Trần Minh")
        self.assertEqual(delivery["mappedSamples"][0]["phone"], "0901444555")
        self.assertIn("Lê Lợi", delivery["mappedSamples"][0]["address"])
        self.assertNotEqual(headerless["suggested"]["name"], delivery["suggested"]["name"])
        merged_headers = [str(col.get("header") or "") for col in data["columns"]]
        self.assertEqual(merged_headers[data["suggested"]["name"]], "Tên người nhận")

    def test_import_wrong_shared_indexes_still_reads_each_file(self) -> None:
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 2, "header": "Cột C"},
                        "phone": {"index": 3, "header": "Cột D"},
                        "address": {"index": 4, "header": "Cột E"},
                    }
                )
            },
            files=[
                self._file(
                    "khong-tieu-de.xlsx",
                    ["ghi chú", "mã", "Nguyễn Lan", "0901222333", "12 Lê Lợi, Huế"],
                    [],
                ),
                self._file(
                    "delivery_export.xlsx",
                    ["STT", "Mã vận đơn", "Tên người nhận", "SĐT người nhận", "Địa chỉ nhận"],
                    [["1", "VD1", "Trần Minh", "0901444555", "12 Lê Lợi, Quận 1"]],
                ),
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["emptyFiles"], [])
        self.assertEqual(imported.json()["stats"]["created"], 2)
        items = {item["phone"]: item for item in self.client.get("/v1/customers", headers=self.headers).json()["items"]}
        self.assertEqual(items["0901222333"]["name"], "Nguyễn Lan")
        self.assertEqual(items["0901444555"]["name"], "Trần Minh")
        self.assertIn("Lê Lợi", items["0901444555"]["address"])

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
            ["Tên khách", "SĐT", "Số nhà", "Phường", "Tỉnh"],
            [["Nguyễn Lan", "0901222333", "12 Lê Lợi", "Quận 1", "TP.HCM"]],
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
        self.assertEqual(data["suggested"]["addressColumns"], [2])
        self.assertEqual(data["suggested"]["adminAddressColumns"], [3, 4])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
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
            ["Tên khách", "SĐT", "Hàng mua", "Sản phẩm"],
            [["Nguyễn Lan", "0901222333", "Nước mắm", "chai 500ml"]],
        )
        preview = self.client.post(
            "/v1/customers/preview",
            headers=self.headers,
            files=[("files", (name, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()
        self.assertEqual(data["suggested"]["orderContentColumns"], [2, 3])
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
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
        by_country = self.client.post(
            "/v1/customers/lookup",
            headers=self.headers,
            data={"text": "84901222333"},
        )
        self.assertEqual(by_country.status_code, 200, by_country.text)
        self.assertEqual(by_country.json()["stats"]["found"], 1)
        self.assertEqual(by_country.json()["items"][0]["customer"]["name"], "Lan")

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
                },
                {
                    "id": "c2",
                    "name": "Minh",
                    "phone": "0375866792",
                    "address": "Đà Nẵng",
                    "facebookUid": "",
                    "tiktokId": "",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                },
            ],
            [],
        )
        by_json = self.client.post(
            "/v1/customers/lookup",
            headers=self.headers,
            json={"text": "0375866792\n0901222333\n0985098234"},
        )
        self.assertEqual(by_json.status_code, 200, by_json.text)
        json_body = by_json.json()
        self.assertEqual(json_body["stats"]["found"], 2)
        self.assertEqual(json_body["stats"]["missing"], 1)
        self.assertNotIn("total", json_body["stats"])
        self.assertEqual(json_body["items"][0]["customer"]["name"], "Minh")
        broken = self.client.post(
            "/v1/customers/lookup",
            headers={**self.headers, "Content-Type": "application/json"},
            content=b"{not-json",
        )
        self.assertEqual(broken.status_code, 400, broken.text)
        self.assertIn("không đọc được", broken.json()["detail"])

        exported_json = self.client.post(
            "/v1/customers/lookup/export",
            headers=self.headers,
            json={"text": "0375866792"},
        )
        self.assertEqual(exported_json.status_code, 200)
        self.assertIn("spreadsheetml", exported_json.headers["content-type"])

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
        history = self.client.get("/v1/customers/sessions", headers=self.headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["total"], 1)
        self.assertEqual(history.json()["uniquePhones"], 1)
        self.assertEqual(history.json()["rowSum"], 2)
        self.assertEqual(history.json()["repeatRows"], 1)

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
        kept = list((settings.data_dir / "imports").rglob("*.xlsx")) if (settings.data_dir / "imports").exists() else []
        self.assertTrue(any(path.name == "ngan.xlsx" for path in kept), kept)
        history = self.client.get("/v1/customers/sessions", headers=self.headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["total"], 1200)
        self.assertEqual(history.json()["uniquePhones"], 1200)
        self.assertEqual(history.json()["emptyPhone"], 0)
        self.assertEqual(history.json()["duplicatePhones"], 0)
        self.assertEqual(history.json()["rowSum"], 1200)
        self.assertEqual(history.json()["repeatRows"], 0)
        self.assertTrue(history.json()["sessions"])
        latest = history.json()["sessions"][0]
        self.assertEqual(latest["incoming"], 1200)
        self.assertEqual(latest["created"], 1200)
        self.assertEqual(latest["unchanged"], 0)
        self.assertTrue(latest["readOk"])
        self.assertTrue(history.json()["complete"])

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

    def test_import_fills_name_phone_when_client_only_mapped_address(self) -> None:
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "address": {
                            "index": 2,
                            "header": "lô 41 đường số 2, KCN An Đồn, Sơn Trà, Đà Nẵng",
                        }
                    }
                )
            },
            files=[
                self._file(
                    "tong-hop.xlsx",
                    ["Nguyễn Lan", "0901222333", "lô 41 đường số 2, KCN An Đồn, Sơn Trà, Đà Nẵng"],
                    [["Trần Minh", "0901444555", "12 Lê Lợi, Quận 1, TP.HCM"]],
                )
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 2)
        items = {item["phone"]: item for item in self.client.get("/v1/customers", headers=self.headers).json()["items"]}
        self.assertEqual(items["0901222333"]["name"], "Nguyễn Lan")
        self.assertIn("An Đồn", items["0901222333"]["address"])
        self.assertEqual(items["0901444555"]["name"], "Trần Minh")
        self.assertIn("Lê Lợi", items["0901444555"]["address"])

    def test_import_receiver_headers_still_fill_when_only_address_chosen(self) -> None:
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {"address": {"index": 2, "header": "Địa chỉ người nhận"}}
                )
            },
            files=[
                self._file(
                    "nhan-thieu.xlsx",
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

    def test_import_second_file_with_different_layout_still_keeps_customers(self) -> None:
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
            files=[
                self._file(
                    "file-a.xlsx",
                    ["Tên khách", "SĐT", "Địa chỉ"],
                    [["Lan", "0901222333", "Huế"]],
                ),
                self._file(
                    "file-b.xlsx",
                    ["Số điện thoại", "Họ tên", "Địa chỉ giao"],
                    [["0901444555", "Trần Minh", "12 Lê Lợi, Quận 1, TP.HCM"]],
                ),
                self._file(
                    "file-c.xlsx",
                    ["Nguyễn Hoa", "0901999888", "lô 41 đường số 2, Sơn Trà, Đà Nẵng"],
                    [],
                ),
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 3)
        self.assertEqual(imported.json()["emptyFiles"], [])
        items = {item["phone"]: item for item in self.client.get("/v1/customers", headers=self.headers).json()["items"]}
        self.assertEqual(items["0901222333"]["name"], "Lan")
        self.assertEqual(items["0901444555"]["name"], "Trần Minh")
        self.assertIn("Lê Lợi", items["0901444555"]["address"])
        self.assertEqual(items["0901999888"]["name"], "Nguyễn Hoa")

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

    def test_later_import_clears_old_empty_file(self) -> None:
        db.record_action(
            settings.db_path,
            at="2026-10-24T04:33:56+00:00",
            actor="me",
            source="hub",
            kind="note",
            summary="Nạp Excel: thêm 0, bổ sung 0, giữ 0, gộp 0.",
            detail=json.dumps(
                {
                    "stats": {"created": 0, "updated": 0, "unchanged": 0, "incoming": 0},
                    "files": [{"name": "delivery_export_1.xlsx", "rows": 0}],
                    "emptyFiles": ["delivery_export_1.xlsx"],
                    "fileCount": 1,
                    "okFiles": 0,
                },
                ensure_ascii=False,
            ),
        )
        stale = self.client.get("/v1/customers/sessions", headers=self.headers)
        self.assertEqual(stale.status_code, 200, stale.text)
        self.assertIn("delivery_export_1.xlsx", stale.json()["needReupload"])
        self.assertFalse(stale.json()["complete"])
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
                    "delivery_export_1.xlsx",
                    ["Tên người nhận", "SĐT người nhận", "Địa chỉ người nhận"],
                    [["Nguyễn Lan", "0901222333", "Huế"]],
                )
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["stats"]["created"], 1)
        history = self.client.get("/v1/customers/sessions", headers=self.headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["needReupload"], [])
        self.assertTrue(history.json()["complete"])
        old = [item for item in history.json()["sessions"] if item["at"] == "2026-10-24T04:33:56+00:00"][0]
        self.assertEqual(old["emptyFiles"], [])
        self.assertEqual(old["resolvedEmpty"], ["delivery_export_1.xlsx"])
        self.assertTrue(old["laterOk"])
        self.assertEqual(history.json()["uniquePhones"], 1)
        self.assertEqual(history.json()["emptyPhone"], 0)
        self.assertEqual(history.json()["duplicatePhones"], 0)

    def test_lookup_file_skips_facebook_uid(self) -> None:
        db.replace_customers(
            settings.db_path,
            [
                {
                    "id": "c1",
                    "name": "Lan",
                    "phone": "0901222333",
                    "address": "Huế",
                    "facebookUid": "1000123456789",
                    "tiktokId": "",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                }
            ],
            [],
        )
        name, file_body = self._xlsx(
            "so.xlsx",
            ["SĐT", "UID Facebook"],
            [["0901222333", "1000123456789"], ["", "1000999888777"]],
        )
        looked = self.client.post(
            "/v1/customers/lookup",
            headers=self.headers,
            data={"text": ""},
            files=[("files", (name, file_body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        )
        self.assertEqual(looked.status_code, 200, looked.text)
        body = looked.json()
        self.assertEqual(body["stats"]["queried"], 1)
        self.assertEqual(body["stats"]["found"], 1)
        self.assertEqual(body["stats"]["missing"], 0)
        self.assertEqual(body["items"][0]["customer"]["name"], "Lan")

    def test_search_phone_variant_does_not_need_full_warehouse(self) -> None:
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
        found = self.client.get("/v1/customers?q=84901222333", headers=self.headers)
        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json()["matched"], 1)
        self.assertEqual(found.json()["items"][0]["name"], "Lan")

    def test_import_uses_phone_on_row_and_keeps_every_number_from_new_file(self) -> None:
        first = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={
                "mapping": json.dumps(
                    {
                        "name": {"index": 0, "header": "Tên"},
                        "phone": {"index": 1, "header": "SĐT"},
                        "address": {"index": 2, "header": "Địa chỉ"},
                    }
                )
            },
            files=[self._file("cu.xlsx", ["Tên", "SĐT", "Địa chỉ"], [["Lan", "0901111111", "Huế"]])],
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["stats"]["created"], 1)
        mapping = {
            "name": {"index": 0, "header": "Tên"},
            "phone": {"index": 1, "header": "Giới tính"},
            "facebookUid": {"index": 2, "header": "UID Facebook"},
            "address": {"index": 4, "header": "Địa chỉ"},
        }
        rows = [
            ["Lan", "Nữ", "100007416894544", "0901111111", "Đà Nẵng"],
            ["Minh", "Nam", "100007416894545", "0902222222", "Nha Trang"],
            ["Hoa", "Nữ", "100007416894546", "84903333333", "Quy Nhơn"],
            ["Không số", "Nam", "100007416894547", "", "Chỉ địa chỉ"],
        ]
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={"mapping": json.dumps(mapping)},
            files=[self._file("moi.xlsx", ["Tên", "Giới tính", "UID Facebook", "Ghi chú", "Địa chỉ"], rows)],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        stats = imported.json()["stats"]
        files = imported.json()["files"]
        self.assertEqual(stats["incoming"], 3)
        self.assertEqual(stats["created"], 2)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(files[0]["phonesOnFile"], 3)
        self.assertEqual(files[0]["rows"], 3)
        listed = self.client.get("/v1/customers", headers=self.headers)
        items = {item["phone"]: item for item in listed.json()["items"]}
        self.assertEqual(set(items), {"0901111111", "0902222222", "0903333333"})
        self.assertEqual(items["0901111111"]["name"], "Lan")
        self.assertEqual(items["0901111111"]["address"], "Huế\nĐà Nẵng")
        self.assertEqual(items["0902222222"]["name"], "Minh")
        self.assertEqual(items["0903333333"]["name"], "Hoa")
        again = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={"mapping": json.dumps(mapping)},
            files=[self._file("moi2.xlsx", ["Tên", "Giới tính", "UID Facebook", "Ghi chú", "Địa chỉ"], rows)],
        )
        self.assertEqual(again.status_code, 200, again.text)
        again_stats = again.json()["stats"]
        self.assertEqual(again_stats["created"], 0)
        self.assertEqual(again_stats["incoming"], 3)
        self.assertEqual(again_stats["created"] + again_stats["updated"] + again_stats["unchanged"], 3)
        self.assertEqual(self.client.get("/v1/customers", headers=self.headers).json()["total"], 3)

    def test_import_complete_scenarios_every_row_is_a_customer(self) -> None:
        from control_plane.excel_import import write_xlsx_sheets

        mapping = {
            "name": {"index": 0, "header": "Tên"},
            "phone": {"index": 1, "header": "SĐT"},
            "address": {"index": 2, "header": "Địa chỉ"},
        }
        payload = write_xlsx_sheets(
            [
                (
                    "Mot",
                    [
                        ["Tên", "SĐT", "Địa chỉ"],
                        ["Lan", "0901111111", "Huế"],
                        ["", "", ""],
                        ["Minh", "LH 0902.222.222", "Đà Nẵng"],
                    ],
                ),
                (
                    "Hai",
                    [
                        ["Họ tên", "Số điện thoại", "Địa chỉ nhận"],
                        ["Hoa", "84903333333", "Nha Trang"],
                        ["An", "0901444444", "Quy Nhơn"],
                    ],
                ),
                (
                    "Ba",
                    [
                        ["Bình", "0901555555", "Hà Nội"],
                        ["Lan lại", "0901111111", "Huế 2"],
                    ],
                ),
            ]
        )
        imported = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={"mapping": json.dumps(mapping)},
            files=[
                (
                    "files",
                    (
                        "du-kien.xlsx",
                        payload,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        stats = imported.json()["stats"]
        self.assertEqual(stats["incoming"], 6)
        self.assertEqual(stats["created"], 5)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["created"] + stats["updated"] + stats["unchanged"], 6)
        self.assertEqual(imported.json()["files"][0]["phonesOnFile"], 6)
        items = {item["phone"]: item for item in self.client.get("/v1/customers", headers=self.headers).json()["items"]}
        self.assertEqual(
            set(items),
            {"0901111111", "0902222222", "0903333333", "0901444444", "0901555555"},
        )
        self.assertEqual(items["0901111111"]["address"], "Huế\nHuế 2")
        again = self.client.post(
            "/v1/customers/import",
            headers=self.headers,
            data={"mapping": json.dumps(mapping)},
            files=[
                (
                    "files",
                    (
                        "du-kien-2.xlsx",
                        payload,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        self.assertEqual(again.status_code, 200, again.text)
        again_stats = again.json()["stats"]
        self.assertEqual(again_stats["created"], 0)
        self.assertEqual(again_stats["incoming"], 6)
        self.assertEqual(again_stats["created"] + again_stats["updated"] + again_stats["unchanged"], 6)
        self.assertEqual(self.client.get("/v1/customers", headers=self.headers).json()["total"], 5)


if __name__ == "__main__":
    unittest.main()
