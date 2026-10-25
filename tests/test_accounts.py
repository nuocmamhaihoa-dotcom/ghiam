"""Đăng nhập quản lý, tài khoản nhân viên, hạn mức tra cứu theo ngày."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="fb-accounts-")
os.environ["CONTROL_DATA_DIR"] = _TMP
os.environ["CONTROL_DB"] = str(Path(_TMP) / "server.db")
os.environ["CONTROL_TOKEN"] = "test-token"
os.environ["CONTROL_PAGE_TOKEN"] = "page-token"
os.environ["CONTROL_PROXIES_FILE"] = str(Path(_TMP) / "proxies.txt")
os.environ["CONTROL_PACKAGES_DIR"] = str(Path(_TMP) / "packages")
os.environ["CONTROL_PROXY_CHECK_SEC"] = "86400"
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from control_plane import db  # noqa: E402
from control_plane.app import app  # noqa: E402
from control_plane.settings import settings  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


class AccountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = (settings.data_dir, settings.db_path, settings.token, settings.page_token)
        settings.data_dir = Path(_TMP)
        settings.db_path = Path(_TMP) / "server.db"
        settings.token = "test-token"
        settings.page_token = "page-token"
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(settings.db_path) + suffix)
            if path.exists():
                path.unlink()
        db.init_db(settings.db_path)
        self._clients = [TestClient(app).__enter__() for _ in range(3)]
        self.token_client, self.manager, self.staff = self._clients
        self.master = {"Authorization": "Bearer test-token"}
        self.page = {"Authorization": "Bearer page-token"}

    def tearDown(self) -> None:
        for client in self._clients:
            client.__exit__(None, None, None)
        settings.data_dir, settings.db_path, settings.token, settings.page_token = self._prev

    def _usage(self, account_id: str) -> int:
        with db.session(settings.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM lookup_usage WHERE account_id=?",
                (account_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def _setup(self) -> dict:
        response = self.manager.post(
            "/v1/accounts/setup",
            json={"username": "quanly", "password": "matkhau1", "displayName": "Quản lý kho"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["account"]

    def _staff(self, limit: int = 2, username: str = "nhanvien", password: str = "nhanvien1") -> dict:
        response = self.manager.post(
            "/v1/accounts",
            json={
                "username": username,
                "password": password,
                "displayName": "Nhân viên kho",
                "dailyLookupLimit": limit,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["account"]

    def _login(self, client: TestClient, username: str, password: str) -> dict:
        client.cookies.clear()
        response = client.post("/v1/accounts/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["account"]

    def _wait_job(self, client: TestClient, job_id: str) -> dict:
        last = {}
        for _ in range(40):
            response = client.get(f"/v1/customers/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            last = response.json()["job"]
            if last.get("status") in ("done", "error", "interrupted"):
                return last
            time.sleep(0.05)
        self.fail(f"việc tra cứu chưa xong: {last}")

    def test_page_explains_login_and_does_not_embed_the_token(self) -> None:
        home = self.token_client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Đăng nhập", home.text)
        self.assertIn("Tạo tài khoản quản lý", home.text)
        self.assertIn("Tài khoản nhân viên", home.text)
        self.assertIn("Hạn mức tra cứu", home.text)
        self.assertIn("boot()", home.text)
        self.assertIn('var serverToken = "";', home.text)
        self.assertIn("20261025h", home.text)
        self.assertIn("min-width: 1100px", home.text)
        self.assertIn("Cách nạp kho và tra cứu", home.text)
        self.assertNotIn("page-token", home.text)
        self.assertNotIn("test-token", home.text)
        self.assertNotIn("__CONTROL_TOKEN__", home.text)

    def test_first_visit_creates_one_manager(self) -> None:
        session = self.token_client.get("/v1/accounts/session")
        self.assertEqual(session.status_code, 200)
        self.assertTrue(session.json()["needsSetup"])
        self.assertIsNone(session.json()["account"])
        weak = self.manager.post("/v1/accounts/setup", json={"username": "quanly", "password": "ngan"})
        self.assertEqual(weak.status_code, 400, weak.text)
        self.assertTrue(self.token_client.get("/v1/accounts/session").json()["needsSetup"])
        created = self.manager.post(
            "/v1/accounts/setup",
            json={"username": "QuanLy", "password": "matkhau1", "displayName": "Quản lý kho"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        account = created.json()["account"]
        self.assertEqual(account["username"], "quanly")
        self.assertEqual(account["role"], "manager")
        self.assertIsNone(account["dailyLookupLimit"])
        cookie = created.headers.get("set-cookie", "")
        self.assertIn("khach_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Secure", cookie)
        again = self.staff.post(
            "/v1/accounts/setup",
            json={"username": "khac", "password": "matkhau1"},
        )
        self.assertEqual(again.status_code, 409, again.text)
        fresh = TestClient(app)
        try:
            opened = fresh.get("/v1/accounts/session")
        finally:
            fresh.close()
        self.assertFalse(opened.json()["needsSetup"])
        self.assertIsNone(opened.json()["account"])

    def test_master_token_stays_manager_and_page_token_cannot_open_customers(self) -> None:
        denied = self.token_client.get("/v1/customers")
        self.assertEqual(denied.status_code, 401)
        opened = self.token_client.get("/v1/customers", headers=self.master)
        self.assertEqual(opened.status_code, 200, opened.text)
        page = self.token_client.get("/v1/customers", headers=self.page)
        self.assertEqual(page.status_code, 401, page.text)
        self._setup()
        still = self.token_client.get("/v1/customers", headers=self.master)
        self.assertEqual(still.status_code, 200, still.text)
        password = self.token_client.post(
            "/v1/accounts/password",
            headers=self.master,
            json={"password": "matkhau1", "next": "matkhau2"},
        )
        self.assertEqual(password.status_code, 403, password.text)

    def test_wrong_password_matches_unknown_user_and_then_locks(self) -> None:
        self._setup()
        missing = self.staff.post("/v1/accounts/login", json={"username": "khongco", "password": "matkhau1"})
        wrong = self.staff.post("/v1/accounts/login", json={"username": "quanly", "password": "sai-mat-khau"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.json()["detail"], wrong.json()["detail"])
        self.staff.cookies.clear()
        for _ in range(7):
            attempt = self.staff.post("/v1/accounts/login", json={"username": "quanly", "password": "sai-mat-khau"})
            self.assertEqual(attempt.status_code, 401, attempt.text)
        locked = self.staff.post("/v1/accounts/login", json={"username": "quanly", "password": "matkhau1"})
        self.assertEqual(locked.status_code, 429, locked.text)
        self.assertIn("15 phút", locked.json()["detail"])

    def test_staff_lookup_quota_rejects_the_whole_request(self) -> None:
        self._setup()
        created = self._staff(limit=1)
        self._login(self.staff, "nhanvien", "nhanvien1")
        self.assertEqual(self.staff.get("/v1/customers").status_code, 403)
        self.assertEqual(self.staff.get("/v1/customers/export").status_code, 403)
        self.assertEqual(self.staff.get("/v1/accounts").status_code, 403)
        self.assertEqual(self.staff.get("/v1/customers/sessions").status_code, 403)
        blocked = self.staff.post(
            "/v1/customers/lookup",
            json={"text": "0901000001\n0901000002"},
        )
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("Hết hạn mức", blocked.json()["detail"])
        self.assertEqual(self._usage(created["id"]), 0)
        first = self.staff.post("/v1/customers/lookup", json={"text": "0901000001"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["quota"]["charged"], 1)
        self.assertEqual(first.json()["quota"]["used"], 1)
        self.assertEqual(first.json()["quota"]["remaining"], 0)
        self.assertTrue(first.json()["items"])
        again = self.staff.post("/v1/customers/lookup", json={"text": "84901000001"})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["quota"]["charged"], 0)
        self.assertEqual(again.json()["quota"]["used"], 1)
        self.assertEqual(self._usage(created["id"]), 1)
        another = self.staff.post("/v1/customers/lookup", json={"text": "0901000002"})
        self.assertEqual(another.status_code, 429, another.text)
        self.assertEqual(self._usage(created["id"]), 1)
        started = self.staff.post(
            "/v1/customers/lookup",
            json={"text": "0901000003\n0901000004", "requestId": uuid.uuid4().hex},
        )
        self.assertEqual(started.status_code, 200, started.text)
        job = started.json()["job"]
        if job.get("status") not in ("done", "error", "interrupted"):
            job = self._wait_job(self.staff, job["id"])
        self.assertEqual(job["status"], "error")
        self.assertIn("Hết hạn mức", job.get("error") or "")
        self.assertEqual(self._usage(created["id"]), 1)

    def test_manager_sets_limit_and_is_not_charged(self) -> None:
        manager = self._setup()
        staff = self._staff(limit=200)
        listed = self.manager.get("/v1/accounts")
        self.assertEqual(listed.status_code, 200, listed.text)
        names = [item["username"] for item in listed.json()["accounts"]]
        self.assertEqual(names, ["quanly", "nhanvien"])
        changed = self.manager.patch(f"/v1/accounts/{staff['id']}", json={"dailyLookupLimit": 5})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["account"]["dailyLookupLimit"], 5)
        forbidden = self.manager.patch(f"/v1/accounts/{manager['id']}", json={"dailyLookupLimit": 5})
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        duplicate = self.manager.post(
            "/v1/accounts",
            json={"username": "NhanVien", "password": "nhanvien1", "dailyLookupLimit": 1},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        looked = self.manager.post("/v1/customers/lookup", json={"text": "0901000001\n0901000002"})
        self.assertEqual(looked.status_code, 200, looked.text)
        self.assertIsNone(looked.json()["quota"]["limit"])
        self.assertEqual(looked.json()["quota"]["charged"], 0)
        self.assertEqual(self._usage(manager["id"]), 0)
        master = self.token_client.post(
            "/v1/customers/lookup",
            headers=self.master,
            json={"text": "0901000009"},
        )
        self.assertEqual(master.status_code, 200, master.text)
        self.assertIsNone(master.json()["quota"]["limit"])

    def test_disable_and_password_reset_drop_the_session(self) -> None:
        self._setup()
        staff = self._staff(limit=10)
        self._login(self.staff, "nhanvien", "nhanvien1")
        self.assertEqual(self.staff.get("/v1/accounts/me").status_code, 200)
        disabled = self.manager.patch(f"/v1/accounts/{staff['id']}", json={"active": False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["account"]["active"])
        self.assertEqual(self.staff.get("/v1/accounts/me").status_code, 401)
        locked = self.staff.post("/v1/accounts/login", json={"username": "nhanvien", "password": "nhanvien1"})
        self.assertEqual(locked.status_code, 401, locked.text)
        opened = self.manager.patch(f"/v1/accounts/{staff['id']}", json={"active": True, "password": "matkhaumoi"})
        self.assertEqual(opened.status_code, 200, opened.text)
        old = self.staff.post("/v1/accounts/login", json={"username": "nhanvien", "password": "nhanvien1"})
        self.assertEqual(old.status_code, 401, old.text)
        self._login(self.staff, "nhanvien", "matkhaumoi")
        changed = self.staff.post(
            "/v1/accounts/password",
            json={"password": "sai-mat-khau", "next": "matkhau99"},
        )
        self.assertEqual(changed.status_code, 401, changed.text)
        changed = self.staff.post(
            "/v1/accounts/password",
            json={"password": "matkhaumoi", "next": "matkhau99"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.staff.get("/v1/accounts/me").status_code, 401)
        self._login(self.staff, "nhanvien", "matkhau99")
        self.assertEqual(self.staff.get("/v1/accounts/me").json()["account"]["role"], "staff")
