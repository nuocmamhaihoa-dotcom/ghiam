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
    sheet.append(["Họ và tên", "SĐT", "Địa chỉ", "UID", "TikTok"])
    sheet.append(["Vũ K", "0933000111", "4 Đường Láng, Đống Đa", "8000111222333", "@vuk"])
    sheet.append(["Lý L", "0933000222", "6 Nguyễn Huệ, Q.1", "8000111222334", "@lyl"])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


class WarehouseApiTests(unittest.TestCase):
    def setUp(self) -> None:
        for path in (
            settings.db_path,
            Path(str(settings.db_path) + "-wal"),
            Path(str(settings.db_path) + "-shm"),
        ):
            if path.exists():
                path.unlink()
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
        self.assertIn("Hỏi lại trước khi nạp", page.text)
        self.assertIn("ở cột nào?", page.text)
        self.assertIn("Tải bản lưu về máy", page.text)
        self.assertIn("Nạp bản lưu lên VPS", page.text)

        preview = self.client.post(
            "/v1/kho/preview",
            headers=self.headers,
            files={"file": ("khach.xlsx", _xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["sheet"]["mapping"]["phone"], 1)
        self.assertEqual(body["sheet"]["sample"][0]["name"], "Vũ K")
        self.assertEqual(body["sheet"]["sample"][0]["match"], "new")

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
        self.assertEqual(found.json()["items"][0]["externalId"], "@vuk")

        exported = self.client.get("/v1/kho/export", headers=self.headers, params={"q": "Vũ"})
        self.assertEqual(exported.status_code, 200)
        self.assertIn("0933000111", exported.text)

        stats = self.client.get("/v1/kho/stats", headers=self.headers)
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["contacts"], 2)
        self.assertEqual(stats.json()["phones"], 2)

    def test_preview_marks_unique_phone_match(self) -> None:
        first = self.client.post(
            "/v1/kho/preview",
            headers=self.headers,
            files={"file": ("khach.xlsx", _xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        self.assertEqual(first.status_code, 200)
        body = first.json()
        committed = self.client.post(
            "/v1/kho/commit",
            headers=self.headers,
            json={
                "token": body["token"],
                "sheet": body["chosen"],
                "mapping": body["sheet"]["mapping"],
                "headerRow": body["sheet"]["headerRow"],
                "hasHeader": True,
            },
        )
        self.assertEqual(committed.status_code, 200)
        extra = (
            "Ten,SDT,Dia chi\n"
            'Vũ K mới,0933000111,"99 Cầu Giấy, Hà Nội"\n'
        ).encode("utf-8")
        again = self.client.post(
            "/v1/kho/preview",
            headers=self.headers,
            files={"file": ("them.csv", extra, "text/csv")},
        )
        self.assertEqual(again.status_code, 200, again.text)
        sample = again.json()["sheet"]["sample"][0]
        self.assertEqual(sample["match"], "phone")
        self.assertEqual(sample["phone"], "0933000111")
        self.assertEqual(sample["address"], "99 Cầu Giấy, Hà Nội")

    def test_download_and_restore_snapshot(self) -> None:
        preview = self.client.post(
            "/v1/kho/preview",
            headers=self.headers,
            files={"file": ("khach.xlsx", _xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        body = preview.json()
        committed = self.client.post(
            "/v1/kho/commit",
            headers=self.headers,
            json={
                "token": body["token"],
                "sheet": body["chosen"],
                "mapping": body["sheet"]["mapping"],
                "headerRow": body["sheet"]["headerRow"],
                "hasHeader": True,
            },
        )
        self.assertEqual(committed.status_code, 200, committed.text)
        ticket = self.client.post("/v1/kho/file-ticket", headers=self.headers)
        self.assertEqual(ticket.status_code, 200, ticket.text)
        downloaded = self.client.get(ticket.json()["url"])
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertTrue(downloaded.content.startswith(b"SQLite format 3\x00"))
        restored = self.client.post(
            "/v1/kho/restore",
            headers=self.headers,
            files={"file": ("kho-tong.db", downloaded.content, "application/octet-stream")},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertGreaterEqual(restored.json()["contacts"], 2)
        found = self.client.get("/v1/kho", headers=self.headers, params={"q": "0933000111"})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["items"][0]["name"], "Vũ K")
        self.assertEqual(found.json()["items"][0]["externalId"], "@vuk")
        rejected = self.client.post(
            "/v1/kho/restore",
            headers=self.headers,
            files={"file": ("kho-tong.db", b"not a database", "application/octet-stream")},
        )
        self.assertEqual(rejected.status_code, 400)
        still = self.client.get("/v1/kho", headers=self.headers, params={"q": "@vuk"})
        self.assertEqual(still.json()["items"][0]["phone"], "0933000111")

    def test_preview_requires_token(self) -> None:
        response = self.client.post(
            "/v1/kho/preview",
            files={"file": ("a.csv", b"Ten,SDT\nA,0901111222\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 401)
