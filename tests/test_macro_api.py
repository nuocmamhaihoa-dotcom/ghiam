"""VPS store for PC desktop macros — homepage is the clicker hub, not Kho khách."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.request import Request

_TMP = tempfile.mkdtemp(prefix="fb-macro-")
os.environ["CONTROL_DATA_DIR"] = _TMP
os.environ["CONTROL_DB"] = str(Path(_TMP) / "server.db")
os.environ["CONTROL_TOKEN"] = "test-token"
os.environ["CONTROL_PROXIES_FILE"] = str(Path(_TMP) / "proxies.txt")
os.environ["CONTROL_PACKAGES_DIR"] = str(Path(_TMP) / "packages")
os.environ["CONTROL_PROXY_CHECK_SEC"] = "86400"
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from control_plane.app import app  # noqa: E402
from control_plane.settings import settings  # noqa: E402
from pc_macro.hub import HubError, describe, pull, push  # noqa: E402
from pc_macro.store import MacroStore  # noqa: E402
from pc_macro.version import digest_files  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


class MacroHubPageTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.data_dir = Path(_TMP)
        settings.token = "test-token"
        settings.page_token = "page-token"
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}
        self.page = {"Authorization": "Bearer page-token"}
        macros = Path(_TMP) / "macros"
        if macros.exists():
            shutil.rmtree(macros)

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    def test_homepage_is_pc_clicker_not_customer_book(self) -> None:
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Gõ số rồi Enter", home.text)
        self.assertIn('id="btnStart"', home.text)
        self.assertIn('id="btnStart2"', home.text)
        self.assertIn('id="btnContinue2"', home.text)
        self.assertIn('id="btnFrame2"', home.text)
        self.assertIn('id="lane0"', home.text)
        self.assertIn("lane-code", home.text)
        self.assertIn("lane-bind", home.text)
        self.assertIn("lane-box", home.text)
        self.assertIn('id="laneBox0"', home.text)
        self.assertIn("typeIntoLanes", home.text)
        self.assertIn("Chưa gắn ô", home.text)
        self.assertIn("Gắn trình khách", home.text)
        self.assertIn("runToggle2", home.text)
        self.assertIn("Bật&nbsp;chạy", home.text)
        self.assertIn("resumeLane2", home.text)
        self.assertIn("flagged_lanes", home.text)
        self.assertIn("cửa sổ", home.text)
        self.assertIn('id="threshold2"', home.text)
        self.assertIn('id="flags2"', home.text)
        self.assertIn('id="prefix2"', home.text)
        self.assertIn('id="dir2"', home.text)
        self.assertIn("Hướng&nbsp;2", home.text)
        self.assertIn("1&nbsp;tiến&nbsp;trình", home.text)
        self.assertIn("Evoshop", home.text)
        self.assertIn("không&nbsp;gõ", home.text)
        self.assertIn("/v1/pc-macro/run", home.text)
        self.assertIn("Mã&nbsp;khách&nbsp;hàng", home.text)
        self.assertIn("Sau Enter", home.text)
        self.assertIn("Đổi giao diện", home.text)
        self.assertIn("5 số", home.text)
        self.assertIn("trình&nbsp;duyệt", home.text)
        self.assertIn("Tiếp&nbsp;tục", home.text)
        self.assertIn("khung&nbsp;5&nbsp;số", home.text)
        self.assertIn('id="frameChange2_0"', home.text)
        self.assertIn('id="vpn"', home.text)
        self.assertIn("Bật&nbsp;VPN", home.text)
        self.assertIn("không&nbsp;lưu&nbsp;mật&nbsp;khẩu", home.text)
        self.assertIn("VPN&nbsp;Gate", home.text)
        self.assertIn("miễn&nbsp;phí", home.text)
        self.assertIn("Ẩn&nbsp;IP", home.text)
        self.assertIn("IPv6", home.text)
        self.assertIn("bảo&nbsp;mật", home.text)
        self.assertIn("day-so-enter-2", home.text)
        self.assertIn('id="btnFlag"', home.text)
        self.assertIn('id="prefix"', home.text)
        self.assertIn('id="start"', home.text)
        self.assertIn('id="end"', home.text)
        self.assertIn('id="threshold"', home.text)
        self.assertIn('id="btnRecordRecover"', home.text)
        self.assertIn('id="btnSaveRecover"', home.text)
        self.assertIn("tên", home.text)
        self.assertIn("Bôi&nbsp;màu", home.text)
        self.assertIn("Chọn&nbsp;vị&nbsp;trí", home.text)
        self.assertIn("Thêm&nbsp;ảnh", home.text)
        self.assertIn('id="paintCanvas"', home.text)
        self.assertIn('id="marks"', home.text)
        self.assertIn('id="orderStrip"', home.text)
        self.assertIn("1&nbsp;→&nbsp;2&nbsp;→&nbsp;3&nbsp;→&nbsp;4", home.text)
        self.assertIn("cả&nbsp;hai&nbsp;kịch&nbsp;bản", home.text)
        self.assertIn("tự&nbsp;bấm&nbsp;4&nbsp;nút&nbsp;trái", home.text)
        self.assertIn("clickNames", home.text)
        self.assertIn("slotFilled", home.text)
        self.assertIn("willReadFrequently", home.text)
        self.assertIn("giao diện đổi", home.text)
        self.assertIn("F9", home.text)
        self.assertIn("/tai/pc-macro.zip", home.text)
        self.assertIn("tự&nbsp;cập&nbsp;nhật", home.text)
        self.assertIn("CHAY-O-DAY.cmd", home.text)
        self.assertIn("giải&nbsp;nén", home.text)
        self.assertIn("/v1/macros", home.text)
        self.assertIn('var serverToken = "page-token";', home.text)
        self.assertIn('translate="no"', home.text)
        self.assertIn("-apple-system", home.text)
        self.assertIn("font-synthesis: none", home.text)
        self.assertIn('class="w"', home.text)
        self.assertNotIn("Kho khách Excel", home.text)
        self.assertNotIn("Chọn file Excel", home.text)
        self.assertEqual(home.headers.get("content-language"), "vi")
        health = self.client.get("/health")
        self.assertEqual(health.json()["macros"], "/")
        self.assertEqual(health.json()["customers"], "/khach")

    def test_zip_contains_recorder_and_install_script(self) -> None:
        response = self.client.get("/tai/pc-macro.zip")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response.headers["content-type"])
        archive = zipfile.ZipFile(BytesIO(response.content))
        names = set(archive.namelist())
        self.assertIn("pc-macro/pc_macro/gui.py", names)
        self.assertIn("pc-macro/pc_macro/boot.py", names)
        self.assertIn("pc-macro/pc_macro/overlay.py", names)
        self.assertIn("pc-macro/pc_macro/paint.py", names)
        self.assertIn("pc-macro/pc_macro/painter.py", names)
        self.assertIn("pc-macro/pc_macro/hub.py", names)
        self.assertIn("pc-macro/pc_macro/vpn.py", names)
        self.assertIn("pc-macro/pc_macro/free_vpn.py", names)
        self.assertIn("pc-macro/pc_macro/privacy.py", names)
        self.assertIn("pc-macro/Cai-dat.ps1", names)
        self.assertIn("pc-macro/Chay.cmd", names)
        self.assertIn("pc-macro/CHAY-O-DAY.cmd", names)
        self.assertIn("pc-macro/README.txt", names)
        launcher = archive.read("pc-macro/CHAY-O-DAY.cmd").decode("utf-8")
        self.assertEqual(launcher, archive.read("pc-macro/Chay.cmd").decode("utf-8"))
        self.assertIn("pc_macro\\__main__.py", launcher)
        self.assertIn("winget", launcher)
        self.assertIn("pip install", launcher)
        self.assertIn("pynput", launcher)
        self.assertIn("Pillow", launcher)
        self.assertIn("import pynput, PIL", launcher)
        self.assertIn("pause", launcher)
        self.assertIn("pc_macro doctor", launcher)
        installer = archive.read("pc-macro/Cai-dat.ps1").decode("utf-8")
        self.assertIn("Find-Python", installer)
        self.assertIn("tkinter", installer)
        self.assertIn("winget", installer)
        self.assertIn("Pillow", installer)
        self.assertIn("pc-macro/pc_macro/updater.py", names)
        self.assertIn("pc-macro/pc_macro/version.py", names)
        readme = archive.read("pc-macro/README.txt").decode("utf-8")
        self.assertIn("CHAY-O-DAY.cmd", readme)
        self.assertIn("BẤM ĐÚP", readme)
        self.assertIn("Đây không phải phần mềm", readme)
        self.assertIn("python -m pc_macro pull", readme)
        self.assertIn("python -m pc_macro doctor", readme)
        self.assertIn("Hướng 2", readme)
        self.assertIn("VPN trên PC", readme)
        self.assertIn("không đưa lên VPS", readme)
        self.assertIn("VPN Gate", readme)
        self.assertIn("miễn phí", readme)
        self.assertIn("Bật VPN", readme)
        self.assertIn("IPv6", readme)
        self.assertIn("WARP", readme)
        self.assertIn("tự hỏi hub bản mới", readme)
        self.assertIn("python -m pc_macro update", readme)
        version = self.client.get("/v1/pc-macro/version")
        self.assertEqual(version.status_code, 200)
        body = version.json()
        self.assertEqual(body["name"], "pc-macro")
        self.assertGreater(body["build"], 0)
        self.assertEqual(len(body["digest"]), 64)
        self.assertIn("pc_macro/updater.py", body["files"])
        self.assertIn("Chay.cmd", body["files"])
        self.assertIn("CHAY-O-DAY.cmd", body["files"])
        self.assertIn("pc_macro/boot.py", body["files"])
        files = {
            name[len("pc-macro/") :]: archive.read(name)
            for name in names
            if name.startswith("pc-macro/") and not name.endswith("/")
        }
        self.assertEqual(body["digest"], digest_files(files))

    def test_run_command_roundtrip(self) -> None:
        empty = self.client.get("/v1/pc-macro/run", headers=self.page)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["play_cmd"], "")
        posted = self.client.post(
            "/v1/pc-macro/run",
            headers=self.page,
            json={"play_cmd": "run"},
        )
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(posted.json()["play_cmd"], "run")
        beat = self.client.post(
            "/v1/pc-macro/run",
            headers=self.headers,
            json={"pc_bound": True, "current": "SPXAB00001", "status": "play"},
        )
        self.assertEqual(beat.json()["play_cmd"], "run")
        self.assertTrue(beat.json()["pc_bound"])
        self.assertEqual(beat.json()["current"], "SPXAB00001")
        board = self.client.post(
            "/v1/pc-macro/run",
            headers=self.headers,
            json={
                "digits": "00007",
                "changed": ["00003", "00007"],
                "turns": [
                    {"digits": "00007", "state": "run"},
                    {"digits": "00003", "changed": True, "state": "changed"},
                ],
            },
        )
        self.assertEqual(board.json()["digits"], "00007")
        self.assertEqual(board.json()["changed"], ["00003", "00007"])
        self.assertEqual(board.json()["turns"][0]["digits"], "00007")
        self.assertEqual(board.json()["turns"][1]["state"], "changed")
        stopped = self.client.post(
            "/v1/pc-macro/run",
            headers=self.page,
            json={"play_cmd": "stop"},
        )
        self.assertEqual(stopped.json()["play_cmd"], "stop")

    def test_save_list_get_delete(self) -> None:
        empty = self.client.get("/v1/macros", headers=self.headers)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["count"], 0)
        created = self.client.post(
            "/v1/macros",
            headers=self.headers,
            json={
                "name": "Gõ hello",
                "steps": [{"kind": "type", "value": "hello"}, {"kind": "key", "key": "enter"}],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["step_count"], 2)
        self.assertIn("Gõ hello", body["captions"][0])
        slug = body["slug"]
        listed = self.client.get("/v1/macros", headers=self.page)
        self.assertEqual(listed.json()["count"], 1)
        loaded = self.client.get(f"/v1/macros/{slug}", headers=self.headers)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(len(loaded.json()["steps"]), 2)
        gone = self.client.delete(f"/v1/macros/{slug}", headers=self.headers)
        self.assertEqual(gone.status_code, 200)
        missing = self.client.get(f"/v1/macros/{slug}", headers=self.headers)
        self.assertEqual(missing.status_code, 404)

    def test_rejects_bad_token_and_empty_script(self) -> None:
        self.assertEqual(self.client.get("/v1/macros").status_code, 401)
        self.assertEqual(
            self.client.get("/v1/macros", headers={"Authorization": "Bearer nope"}).status_code,
            401,
        )
        blank = self.client.post("/v1/macros", headers=self.headers, json={"name": "rỗng"})
        self.assertEqual(blank.status_code, 400)


class HubClientTests(unittest.TestCase):
    def test_describe_without_hub(self) -> None:
        os.environ.pop("PC_MACRO_HUB", None)
        os.environ.pop("PC_MACRO_TOKEN", None)
        self.assertIn("Chưa đặt PC_MACRO_HUB", describe())

    def test_pull_and_push_roundtrip(self) -> None:
        remote: dict[str, dict] = {}

        def fetch(request: Request, _timeout: float) -> tuple[int, bytes]:
            url = request.full_url if hasattr(request, "full_url") else request.get_full_url()
            method = request.get_method()
            if url.endswith("/v1/macros") and method == "GET":
                items = [{"name": row["name"], "slug": row["slug"], "step_count": row["step_count"]} for row in remote.values()]
                return 200, json.dumps({"count": len(items), "items": items}).encode("utf-8")
            if url.endswith("/v1/macros") and method == "POST":
                payload = json.loads(request.data.decode("utf-8"))
                slug = payload.get("slug") or "ban-ghi"
                remote[slug] = {
                    "name": payload["name"],
                    "slug": slug,
                    "steps": payload.get("steps") or [],
                    "events": payload.get("events") or [],
                    "step_count": len(payload.get("steps") or []),
                }
                return 200, json.dumps({"ok": True, **remote[slug]}).encode("utf-8")
            if "/v1/macros/" in url and method == "GET":
                slug = url.rstrip("/").rsplit("/", 1)[-1]
                if slug not in remote:
                    return 404, json.dumps({"detail": "macro not found"}).encode("utf-8")
                return 200, json.dumps(remote[slug]).encode("utf-8")
            return 500, b"{}"

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PC_MACRO_HUB"] = "http://vps.test:8088"
            os.environ["PC_MACRO_TOKEN"] = "page-token"
            try:
                store = MacroStore(Path(tmp))
                store.save("Gõ a", [{"t": 0, "kind": "key", "action": "down", "key": "a"}])
                uploaded = push(store, fetch=fetch)
                self.assertEqual(uploaded["count"], 1)
                other = MacroStore(Path(tmp) / "other")
                downloaded = pull(other, fetch=fetch)
                self.assertEqual(downloaded["count"], 1)
                self.assertEqual(downloaded["items"][0]["name"], "Gõ a")
            finally:
                os.environ.pop("PC_MACRO_HUB", None)
                os.environ.pop("PC_MACRO_TOKEN", None)

    def test_pull_without_hub_raises(self) -> None:
        os.environ.pop("PC_MACRO_HUB", None)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HubError):
                pull(MacroStore(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
