"""HTTP API for the operator action journal."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="fb-actions-")
os.environ["CONTROL_DATA_DIR"] = _TMP
os.environ["CONTROL_DB"] = str(Path(_TMP) / "server.db")
os.environ["CONTROL_TOKEN"] = "test-token"
os.environ["CONTROL_PROXIES_FILE"] = str(Path(_TMP) / "proxies.txt")
os.environ["CONTROL_PACKAGES_DIR"] = str(Path(_TMP) / "packages")
os.environ["CONTROL_PROXY_CHECK_SEC"] = "86400"
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from control_plane.app import app  # noqa: E402
from control_plane.db import session  # noqa: E402
from control_plane.settings import settings  # noqa: E402


class ActionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    def test_requires_token(self) -> None:
        response = self.client.get("/v1/actions")
        self.assertEqual(response.status_code, 401)

    def test_note_roundtrip_and_search(self) -> None:
        created = self.client.post(
            "/v1/actions",
            headers=self.headers,
            json={"summary": "import 300 URL rồi rebalance hot", "kind": "note", "source": "manual"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["id"])

        listed = self.client.get("/v1/actions", headers=self.headers, params={"q": "rebalance"})
        self.assertEqual(listed.status_code, 200)
        items = listed.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["summary"], "import 300 URL rồi rebalance hot")
        self.assertEqual(items[0]["kind"], "note")

    def test_rejects_unknown_kind(self) -> None:
        response = self.client.post(
            "/v1/actions",
            headers=self.headers,
            json={"summary": "x", "kind": "drop-table"},
        )
        self.assertEqual(response.status_code, 400)

    def test_proxy_check_is_remembered(self) -> None:
        response = self.client.post("/v1/proxies/check", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        listed = self.client.get("/v1/actions", headers=self.headers, params={"kind": "proxy_check"})
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["count"], 1)
        self.assertIn("Check proxy", listed.json()["items"][0]["summary"])

    def test_recording_roundtrip_hides_password(self) -> None:
        created = self.client.post(
            "/v1/recordings",
            headers=self.headers,
            json={
                "events": [
                    {"t": 0, "kind": "key", "target": "#token", "key": "secret-key"},
                    {"t": 5, "kind": "value", "target": "#q", "value": "xin chào"},
                    {
                        "t": 12,
                        "kind": "pointer",
                        "target": "#refresh",
                        "phase": "up",
                        "pointerType": "mouse",
                        "click": True,
                    },
                ]
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertNotIn("secret-key", created.text)
        self.assertIn("Gõ vào #q: xin chào", body["steps"])
        self.assertIn("Bấm #refresh", body["steps"])

        listed = self.client.get("/v1/recordings", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["count"], 1)

        fetched = self.client.get(f"/v1/recordings/{body['id']}", headers=self.headers)
        self.assertEqual(fetched.status_code, 200)
        kinds = [event["kind"] for event in fetched.json()["events"]]
        self.assertEqual(kinds, ["key", "value", "pointer"])
        self.assertTrue(fetched.json()["events"][0]["redacted"])
        self.assertNotIn("key", fetched.json()["events"][0])

    def test_recording_requires_token_and_real_events(self) -> None:
        denied = self.client.post("/v1/recordings", json={"events": [{"t": 0, "kind": "key", "target": "#q", "key": "a"}]})
        self.assertEqual(denied.status_code, 401)
        empty = self.client.post(
            "/v1/recordings",
            headers=self.headers,
            json={"events": [{"t": 0, "kind": "value", "target": "#token", "value": "nope"}]},
        )
        self.assertEqual(empty.status_code, 400)

    def test_dashboard_has_journal(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nhật ký thao tác", response.text)
        self.assertIn("Ghi nhớ", response.text)
        self.assertIn("Làm theo", response.text)
        self.assertIn("Ghi bấm phím và cảm ứng", response.text)
        self.assertIn('id="recordDock"', response.text)
        self.assertIn("Sau 10 giây", response.text)
        self.assertIn("Sửa", response.text)
        self.assertIn("Lưu thành bản mới", response.text)
        self.assertIn("Lưu thành đoạn", response.text)
        self.assertIn("Kịch bản ghép", response.text)
        self.assertIn("Mở giả lập điện thoại trên PC", response.text)

    def test_phone_emulator_page(self) -> None:
        response = self.client.get("/phone")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Giả lập", response.text)
        self.assertIn('src="/sample-people"', response.text)
        self.assertIn("/?as=phone", response.text)
        self.assertIn("Kết nối PC", response.text)
        self.assertIn("Tên trong danh bạ", response.text)

    def test_named_clip_updates_every_scenario(self) -> None:
        created = self.client.post(
            "/v1/clips",
            headers=self.headers,
            json={
                "name": "Gõ và bấm",
                "steps": [
                    {
                        "kind": "type",
                        "target": "#actionText",
                        "label": "Mình vừa làm gì?",
                        "value": "mẫu",
                        "valueBlank": "Nội dung",
                    },
                    {"kind": "tap", "target": "#refresh", "label": "Tải lại", "targetBlank": "Nút"},
                ],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        clip_id = created.json()["id"]
        self.assertEqual([blank["kind"] for blank in created.json()["blanks"]], ["text", "control"])
        scenario = self.client.post(
            "/v1/scenarios",
            headers=self.headers,
            json={
                "name": "Hai lần",
                "parts": [
                    {"clipId": clip_id, "fills": {"Nội dung": "AAAA", "Nút": {"target": "#searchAction", "label": "Tìm"}}},
                    {"clipId": clip_id, "fills": {"Nội dung": "BBBB"}},
                ],
            },
        )
        self.assertEqual(scenario.status_code, 200, scenario.text)
        scenario_id = scenario.json()["id"]
        compiled = self.client.post(
            "/v1/scenarios/compile",
            headers=self.headers,
            json={"name": "xem", "parts": scenario.json()["parts"]},
        )
        self.assertEqual(compiled.status_code, 200, compiled.text)
        values = [step["value"] for step in compiled.json()["steps"] if step["kind"] == "type"]
        self.assertEqual(values, ["AAAA", "BBBB"])
        self.assertEqual(compiled.json()["steps"][1]["target"], "#searchAction")
        updated = self.client.put(
            f"/v1/clips/{clip_id}",
            headers=self.headers,
            json={
                "name": "Gõ và bấm",
                "steps": [
                    {
                        "kind": "type",
                        "target": "#actionText",
                        "label": "Mình vừa làm gì?",
                        "value": "mẫu",
                        "valueBlank": "Nội dung",
                    },
                    {"kind": "tap", "target": "#refresh", "label": "Tải lại", "targetBlank": "Nút"},
                    {"kind": "tap", "target": "#recheck", "label": "Check proxy ngay"},
                ],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["scenarioCount"], 1)
        again = self.client.post(
            "/v1/scenarios/compile",
            headers=self.headers,
            json={"name": "xem", "parts": [{"clipId": clip_id, "fills": {"Nội dung": "AAAA"}}]},
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["steps"][-1]["target"], "#recheck")
        self.assertEqual(again.json()["steps"][0]["value"], "AAAA")
        blocked = self.client.delete(f"/v1/clips/{clip_id}", headers=self.headers)
        self.assertEqual(blocked.status_code, 409)
        removed = self.client.delete(f"/v1/scenarios/{scenario_id}", headers=self.headers)
        self.assertEqual(removed.status_code, 200)
        freed = self.client.delete(f"/v1/clips/{clip_id}", headers=self.headers)
        self.assertEqual(freed.status_code, 200)

    def test_edit_script_saves_a_new_copy(self) -> None:
        created = self.client.post(
            "/v1/recordings",
            headers=self.headers,
            json={
                "title": "bản gốc",
                "events": [
                    {
                        "t": 1,
                        "kind": "pointer",
                        "target": "#actionText",
                        "phase": "up",
                        "pointerType": "mouse",
                        "click": True,
                        "intent": "focus",
                        "label": "Mình vừa làm gì?",
                    },
                    {"t": 2, "kind": "value", "target": "#actionText", "value": "import"},
                    {
                        "t": 3,
                        "kind": "pointer",
                        "target": "#refresh",
                        "phase": "up",
                        "pointerType": "mouse",
                        "click": True,
                        "intent": "tap",
                        "label": "Tải lại",
                    },
                ],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        original_id = created.json()["id"]
        parsed = self.client.post(
            "/v1/recordings/parse",
            headers=self.headers,
            json={"events": self.client.get(f"/v1/recordings/{original_id}", headers=self.headers).json()["events"]},
        )
        self.assertEqual(parsed.status_code, 200, parsed.text)
        steps = parsed.json()["steps"]
        self.assertEqual([step["kind"] for step in steps], ["type", "tap"])
        steps[0]["value"] = "đã sửa"
        compiled = self.client.post(
            "/v1/recordings/compile",
            headers=self.headers,
            json={"title": "bản gốc (sửa)", "steps": steps},
        )
        self.assertEqual(compiled.status_code, 200, compiled.text)
        self.assertIn("Gõ vào #actionText: đã sửa", compiled.json()["lines"])
        saved = self.client.post(
            "/v1/recordings/from-steps",
            headers=self.headers,
            json={"title": "bản gốc (sửa)", "steps": steps},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotEqual(saved.json()["id"], original_id)
        original = self.client.get(f"/v1/recordings/{original_id}", headers=self.headers)
        self.assertIn("import", original.text)
        self.assertNotIn("đã sửa", original.text)
        denied = self.client.post("/v1/recordings/from-steps", json={"steps": steps})
        self.assertEqual(denied.status_code, 401)

    def test_scroll_sightings_merge_into_saved_people(self) -> None:
        denied = self.client.get("/v1/people")
        self.assertEqual(denied.status_code, 401)
        contact = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={"items": [{"kind": "contact", "name": "Trần Tùng", "contactName": "A Tùng Bán Gạch"}]},
        )
        self.assertEqual(contact.status_code, 200, contact.text)
        self.assertEqual(contact.json()["count"], 0)
        self.assertEqual(contact.json()["saved"], 1)
        profile = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={"items": [{"kind": "profile", "name": "Trần Tùng", "username": "trn.tng751"}]},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["count"], 1)
        self.assertEqual(profile.json()["saved"], 1)
        row = profile.json()["items"][0]
        self.assertEqual(row["name"], "Trần Tùng")
        self.assertEqual(row["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(row["username"], "@trn.tng751")
        repeat = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={
                "items": [
                    {"kind": "contact", "name": "Trần Tùng", "contactName": "Tên khác"},
                    {"kind": "profile", "name": "Trần Tùng", "username": "@khac"},
                ]
            },
        )
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertEqual(repeat.json()["saved"], 0)
        self.assertEqual(repeat.json()["items"][0]["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(repeat.json()["items"][0]["username"], "@trn.tng751")
        listed = self.client.get("/v1/people", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)
        page = self.client.get("/")
        self.assertIn("Đã lưu", page.text)
        self.assertIn("Tên trong danh bạ", page.text)
        sample = self.client.get("/sample-people")
        self.assertEqual(sample.status_code, 200)
        self.assertIn("A Tùng Bán Gạch", sample.text)
        health = self.client.get("/health")
        build = str(health.json()["iphoneBuild"])
        self.assertIn(f"/static/watch.js?v={build}", sample.text)
        phone = self.client.get("/phone")
        self.assertIn('src="/sample-people"', phone.text)
        self.assertIn("Tên trong danh bạ", phone.text)
        self.assertIn('location.replace("/iphone")', phone.text)

    def test_iphone_app_is_installable(self) -> None:
        page = self.client.get("/iphone")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("apple-mobile-web-app-capable", page.text)
        self.assertIn("viewport-fit=cover", page.text)
        health = self.client.get("/health")
        build = str(health.json()["iphoneBuild"])
        self.assertEqual(page.headers["cache-control"], "no-cache")
        self.assertIn(f'content="{build}"', page.text)
        self.assertIn(f"/static/watch.js?v={build}", page.text)
        self.assertIn("/static/version.js", page.text)
        self.assertIn("Lướt", page.text)
        self.assertIn("Điều khiển", page.text)
        self.assertIn("Đã lưu", page.text)
        self.assertIn("/?as=iphone", page.text)
        self.assertIn("Thêm vào Màn hình chính", page.text)
        self.assertIn("A Tùng Bán Gạch", page.text)
        self.assertIn("Tên trong danh bạ", page.text)
        icon = self.client.get("/apple-touch-icon.png")
        self.assertEqual(icon.status_code, 200)
        self.assertIn("image/png", icon.headers["content-type"])
        self.assertTrue(icon.content.startswith(b"\x89PNG"))
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200, manifest.text)
        body = manifest.json()
        self.assertEqual(body["start_url"], "/iphone")
        self.assertEqual(body["display"], "standalone")
        home = self.client.get("/")
        self.assertIn("Lướt trên iPhone", home.text)
        self.assertIn("Mở giả lập điện thoại trên PC", home.text)
        self.assertIn("Chạm trên iPhone được ghi để làm lại.", home.text)
        self.assertIn("/static/version.js", home.text)
        self.assertIn('href="/tai"', page.text)
        self.assertIn("Tải phần mềm", home.text)

    def test_public_delivery_package(self) -> None:
        page = self.client.get("/tai")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.headers["cache-control"], "no-cache")
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        body = health.json()
        build = str(body["iphoneBuild"])
        self.assertEqual(body["delivery"], "/tai")
        self.assertEqual(build, "5")
        self.assertIn("Mở app", page.text)
        self.assertIn('href="/iphone"', page.text)
        self.assertIn("Tải gói zip", page.text)
        self.assertIn(f'content="{build}"', page.text)
        self.assertIn(f"Bản {build}", page.text)

        info = self.client.get("/v1/delivery")
        self.assertEqual(info.status_code, 200, info.text)
        payload = info.json()
        self.assertEqual(payload["iphoneBuild"], 5)
        self.assertEqual(payload["iphonePath"], "/iphone")
        self.assertEqual(payload["installPath"], "/tai")
        package = payload["package"]
        self.assertEqual(package["path"], "/tai/goi.zip")
        self.assertEqual(package["name"], f"fb-poller-iphone-{build}.zip")
        self.assertGreater(package["bytes"], 0)
        self.assertEqual(len(package["sha256"]), 64)

        downloaded = self.client.get("/tai/goi.zip")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.headers["cache-control"], "no-cache")
        self.assertTrue(downloaded.content.startswith(b"PK"))
        self.assertIn(package["name"], downloaded.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            names = archive.namelist()
            self.assertIn("fb-poller/HUONG-DAN.txt", names)
            self.assertIn("fb-poller/iphone.html", names)
            self.assertNotIn("server.env", " ".join(names))
            joined = " ".join(names)
            self.assertNotIn(".db", joined)
            guide = archive.read("fb-poller/HUONG-DAN.txt").decode("utf-8")
            self.assertIn("/tai", guide)
            self.assertIn("Không có token", guide)
        self.assertFalse(any(Path(_TMP, "packages").glob("*.zip")))
        denied = self.client.get(f"/v1/updates/packages/{package['name']}")
        self.assertEqual(denied.status_code, 401)

        self.assertIn("Tải phần mềm Danh bạ", page.text)
        self.assertIn('href="/tai/danhba.zip"', page.text)
        self.assertIn("Mở Danh bạ", page.text)
        self.assertIn('href="/danhba/"', page.text)
        self.assertIn("Nạp từ máy tính", page.text)
        self.assertIn('href="/danhba/nap"', page.text)
        self.assertEqual(payload["danhbaPath"], "/danhba/")
        self.assertEqual(payload["danhbaBuild"], 8)
        self.assertEqual(body["danhba"], "/danhba/")
        self.assertEqual(body["danhbaBuild"], 8)

        install = self.client.get("/danhba", follow_redirects=False)
        self.assertEqual(install.status_code, 302, install.text)
        self.assertEqual(install.headers["location"], "/danhba/")
        screen = self.client.get("/danhba/")
        self.assertEqual(screen.status_code, 200, screen.text)
        self.assertEqual(screen.headers["cache-control"], "no-cache")
        self.assertIn("Nạp lên iPhone", screen.text)
        self.assertIn('content="8"', screen.text)
        self.assertIn("Đối chiếu iPhone", screen.text)
        self.assertIn("/v1/danhba/sync", screen.text)
        self.assertIn("/v1/danhba/xuat", screen.text)
        self.assertIn("createObjectURL", screen.text)
        self.assertIn("clipboardData", screen.text)
        self.assertIn("text/x-vcard", screen.text)
        self.assertNotIn("data:text/vcard", screen.text)
        self.assertNotIn("navigator.share", screen.text)
        self.assertNotIn("Đã mở", screen.text)
        self.assertIn("Đã dùng", screen.text)
        self.assertIn("Chưa dùng", screen.text)
        self.assertIn("parseLines: parseLines", screen.text)
        self.assertIn("/danhba/sw.js", screen.text)
        self.assertNotIn("/danhba/app.js", screen.text)
        version = self.client.get("/danhba/version")
        self.assertEqual(version.status_code, 200, version.text)
        self.assertEqual(version.json()["build"], 8)
        nap = self.client.get("/danhba/nap")
        self.assertEqual(nap.status_code, 200, nap.text)
        self.assertIn("Nạp lên VPS", nap.text)
        self.assertIn("tên ghi nhớ", nap.text)
        self.assertIn("/v1/danhba/sync", nap.text)
        self.assertIn("BEGIN:VCARD", nap.text)
        worker = self.client.get("/danhba/sw.js")
        self.assertEqual(worker.status_code, 200, worker.text)
        self.assertIn("javascript", worker.headers["content-type"])
        self.assertIn('var BUILD = "8";', worker.text)
        self.assertIn('var CACHE = "danhba-" + BUILD;', worker.text)
        self.assertEqual(worker.headers["service-worker-allowed"], "/danhba/")
        manifest = self.client.get("/danhba/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200, manifest.text)
        self.assertEqual(manifest.json()["start_url"], "/danhba/")
        self.assertEqual(manifest.json()["display"], "standalone")
        danhba = payload["danhba"]
        self.assertEqual(danhba["path"], "/tai/danhba.zip")
        self.assertEqual(danhba["name"], "danh-ba-iphone.zip")
        self.assertGreater(danhba["bytes"], 0)
        self.assertEqual(len(danhba["sha256"]), 64)
        downloaded_app = self.client.get("/tai/danhba.zip")
        self.assertEqual(downloaded_app.status_code, 200)
        self.assertEqual(downloaded_app.headers["cache-control"], "no-cache")
        self.assertTrue(downloaded_app.content.startswith(b"PK"))
        self.assertIn("danh-ba-iphone.zip", downloaded_app.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(downloaded_app.content)) as archive:
            names = archive.namelist()
            self.assertIn("DanhBa/HUONG-DAN.txt", names)
            self.assertIn("DanhBa/README.md", names)
            self.assertTrue(any(name.endswith("DanhBa.xcodeproj/project.pbxproj") for name in names))
            joined = " ".join(names)
            self.assertNotIn("server.env", joined)
            self.assertNotIn(".db", joined)
            self.assertNotIn("xcuserdata", joined)
            guide = archive.read("DanhBa/HUONG-DAN.txt").decode("utf-8")
            self.assertIn("Nạp lên iPhone", guide)
            self.assertIn("Đối chiếu iPhone", guide)
            self.assertIn("Đã dùng", guide)
            self.assertNotIn("token", guide.lower())

    def test_danhba_books_split_and_move_used(self) -> None:
        with session(settings.db_path) as conn:
            conn.execute("DELETE FROM contact_entries")
            conn.execute("DELETE FROM contact_books")
        denied = self.client.get("/v1/danhba/books")
        self.assertEqual(denied.status_code, 401)
        created = self.client.post(
            "/v1/danhba/import",
            headers=self.headers,
            json={
                "title": "Khach",
                "text": "Lan, +84 901-234-567\n0901234567\nMinh, 0902222222\n",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["added"], 2)
        self.assertEqual(body["duplicatePhones"], 1)
        self.assertEqual(len(body["ready"]), 1)
        self.assertEqual(body["ready"][0]["name"], "Khach 1")
        self.assertEqual(body["ready"][0]["count"], 2)
        self.assertEqual(body["used"], [])
        listed = self.client.get("/v1/danhba/books", headers=self.headers)
        self.assertNotIn("0901234567", listed.text)
        book_id = body["ready"][0]["id"]
        one = self.client.get(f"/v1/danhba/books/{book_id}", headers=self.headers)
        self.assertEqual(one.status_code, 200, one.text)
        entries = one.json()["entries"]
        self.assertEqual(entries, [
            {"name": "Lan", "phone": "0901234567"},
            {"name": "Minh", "phone": "0902222222"},
        ])
        used = self.client.post(f"/v1/danhba/books/{book_id}/use", headers=self.headers)
        self.assertEqual(used.status_code, 200, used.text)
        self.assertEqual(used.json()["status"], "used")
        self.assertNotIn("entries", used.json())
        again = self.client.get("/v1/danhba/books", headers=self.headers).json()
        self.assertEqual(again["ready"], [])
        self.assertEqual(again["used"][0]["id"], book_id)
        self.assertEqual(again["used"][0]["status"], "used")

    def test_danhba_sync_matches_saved_books_without_adding_numbers(self) -> None:
        with session(settings.db_path) as conn:
            conn.execute("DELETE FROM contact_entries")
            conn.execute("DELETE FROM contact_books")
        denied = self.client.post("/v1/danhba/sync", json={"phones": ["0901234567"]})
        self.assertEqual(denied.status_code, 401)
        created = self.client.post(
            "/v1/danhba/import",
            headers=self.headers,
            json={"title": "Khach", "text": "Lan, 0901234567\nMinh, 0902222222\n"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        book_id = created.json()["ready"][0]["id"]
        empty = self.client.post("/v1/danhba/sync", headers=self.headers, json={"text": "khong co so"})
        self.assertEqual(empty.status_code, 400)
        partial = self.client.post(
            "/v1/danhba/sync",
            headers=self.headers,
            json={"phones": ["+84 901-234-567"], "full": False},
        )
        self.assertEqual(partial.status_code, 200, partial.text)
        self.assertEqual(partial.json()["movedToUsed"], 0)
        self.assertNotIn("0901234567", partial.text)
        matched = self.client.post(
            "/v1/danhba/sync",
            headers=self.headers,
            json={
                "text": (
                    "BEGIN:VCARD\r\nFN:Lan\r\nTEL;TYPE=CELL:0901234567\r\nEND:VCARD\r\n"
                    "BEGIN:VCARD\r\nFN:Minh\r\nTEL;TYPE=CELL:0902222222\r\nEND:VCARD\r\n"
                ),
                "full": False,
            },
        )
        self.assertEqual(matched.status_code, 200, matched.text)
        body = matched.json()
        self.assertEqual(body["movedToUsed"], 1)
        self.assertEqual(body["unknownPhones"], 0)
        self.assertEqual(body["used"][0]["id"], book_id)
        self.assertNotIn("0902222222", matched.text)
        stored = self.client.get(f"/v1/danhba/books/{book_id}", headers=self.headers)
        self.assertEqual(len(stored.json()["entries"]), 2)

    def test_danhba_vcard_opens_without_share_and_marks_used(self) -> None:
        with session(settings.db_path) as conn:
            conn.execute("DELETE FROM contact_entries")
            conn.execute("DELETE FROM contact_books")
            conn.execute("DELETE FROM contact_exports")
        denied = self.client.post("/v1/danhba/xuat", json={})
        self.assertEqual(denied.status_code, 401)
        created = self.client.post(
            "/v1/danhba/import",
            headers=self.headers,
            json={"title": "Khach", "text": "Lan, 0901234567\nMinh, 0902222222\n"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        book_id = created.json()["ready"][0]["id"]
        issued = self.client.post("/v1/danhba/xuat", headers=self.headers, json={})
        self.assertEqual(issued.status_code, 200, issued.text)
        payload = issued.json()
        self.assertNotIn("0901234567", issued.text)
        self.assertEqual(payload["count"], 2)
        self.assertTrue(payload["url"].endswith(".vcf"))
        card = self.client.get(payload["url"])
        self.assertEqual(card.status_code, 200, card.text)
        self.assertIn("text/x-vcard", card.headers["content-type"])
        self.assertIn("inline", card.headers["content-disposition"])
        self.assertIn('filename="Khach 1.vcf"', card.headers["content-disposition"])
        self.assertIn("BEGIN:VCARD", card.text)
        self.assertIn("N:Lan;;;;", card.text)
        self.assertIn("FN:Lan", card.text)
        self.assertIn("TEL;TYPE=CELL:0901234567", card.text)
        self.assertIn("TEL;TYPE=CELL:0902222222", card.text)
        listed = self.client.get("/v1/danhba/books", headers=self.headers).json()
        self.assertEqual(listed["ready"], [])
        self.assertEqual(listed["used"][0]["id"], book_id)
        repeat = self.client.get(payload["url"])
        self.assertEqual(repeat.status_code, 200, repeat.text)
        self.assertIn("TEL;TYPE=CELL:0902222222", repeat.text)
        missing = self.client.get("/danhba/xuat/not-a-real-ticket.vcf")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
