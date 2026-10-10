"""API kho tổng: xem trước, nạp, tìm, xuất."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

_TMP = tempfile.mkdtemp(prefix="fb-kho-")
os.environ.setdefault("CONTROL_DATA_DIR", _TMP)
os.environ.setdefault("CONTROL_DB", str(Path(_TMP) / "server.db"))
os.environ.setdefault("CONTROL_TOKEN", "test-token")
os.environ.setdefault("CONTROL_PROXIES_FILE", str(Path(_TMP) / "proxies.txt"))
os.environ.setdefault("CONTROL_PACKAGES_DIR", str(Path(_TMP) / "packages"))
os.environ.setdefault("CONTROL_PROXY_CHECK_SEC", "86400")
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from control_plane.app import app  # noqa: E402
from control_plane.warehouse_store import init_warehouse  # noqa: E402
from control_plane.settings import settings  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


def _xlsx() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "KH"
    sheet.append(["Họ và tên", "SĐT", "Địa chỉ", "UID", "ID"])
    sheet.append(["Vũ K", "0933000111", "4 Đường Láng, Đống Đa", "8000111222333", "K-1"])
    sheet.append(["Lý L", "0933000222", "6 Nguyễn Huệ, Q.1", "8000111222334", "K-2"])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


class WarehouseApiTests(unittest.TestCase):
    def setUp(self) -> None:
        init_warehouse(settings.db_path)
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    def test_page_and_preview_commit_search_export(self) -> None:
        page = self.client.get("/kho")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Kho tổng", page.text)

        preview = self.client.post(
            "/v1/kho/preview",
            headers=self.headers,
            files={"file": ("khach.xlsx", _xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["sheet"]["mapping"]["phone"], 1)
        self.assertEqual(body["sheet"]["sample"][0]["name"], "Vũ K")

        committed = self.client.post(
            "/v1/kho/commit",
            headers=self.headers,
            json={
                "token": body["token"],
                "sheet": body["chosen"],
                "mapping": body["sheet"]["mapping"],
                "source": "form-test",
                "fingerprint": body["sheet"]["fingerprint"],
                "headers": [col["header"] for col in body["sheet"]["columns"]],
            },
        )
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()["inserted"], 2)

        found = self.client.get("/v1/kho", headers=self.headers, params={"q": "0933000111"})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["items"][0]["externalId"], "K-1")

        exported = self.client.get("/v1/kho/export", headers=self.headers, params={"q": "Vũ"})
        self.assertEqual(exported.status_code, 200)
        self.assertIn("0933000111", exported.text)

    def test_preview_requires_token(self) -> None:
        response = self.client.post(
            "/v1/kho/preview",
            files={"file": ("a.csv", b"Ten,SDT\nA,0901111222\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 401)
