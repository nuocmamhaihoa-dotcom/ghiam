"""HTTP API for the operator action journal."""

from __future__ import annotations

import io
import os
import shutil
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
from control_plane.settings import settings  # noqa: E402
from control_plane.video_store import begin_upload, commit_upload, connect, init_db, stats as video_stats  # noqa: E402


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


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

    def test_video_queue_has_no_count_cap(self) -> None:
        wide = self.client.get("/v1/videos", headers=self.headers, params={"limit": 500})
        self.assertEqual(wide.status_code, 200, wide.text)
        opened = self.client.get("/v1/videos", headers=self.headers)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["count"], len(opened.json()["items"]))

    def test_upload_lists_and_backup_ticket(self) -> None:
        denied = self.client.post("/v1/videos", files={"file": ("a.mp4", b"fake-video", "video/mp4")})
        self.assertEqual(denied.status_code, 401)
        uploaded = self.client.post(
            "/v1/videos",
            headers=self.headers,
            files={"file": ("a.mp4", b"fake-video", "video/mp4")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        body = uploaded.json()
        self.assertEqual(body["status"], "queued")
        self.assertFalse(body["duplicate"])
        again = self.client.post(
            "/v1/videos",
            headers=self.headers,
            files={"file": ("a-copy.mp4", b"fake-video", "video/mp4")},
        )
        self.assertTrue(again.json()["duplicate"])
        stats = self.client.get("/v1/videos/stats", headers=self.headers)
        self.assertEqual(stats.status_code, 200)
        self.assertGreaterEqual(stats.json()["queued"], 1)
        self.assertGreaterEqual(stats.json()["workers"], 1)
        ticket = self.client.post("/v1/backup/ticket", headers=self.headers)
        self.assertEqual(ticket.status_code, 200, ticket.text)
        downloaded = self.client.get(ticket.json()["url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("gzip", downloaded.headers["content-type"])
        self.assertIn("sao-luu-danh-ba", downloaded.headers["content-disposition"])

    def test_dashboard_is_the_video_queue(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Đọc video danh bạ", response.text)
        self.assertIn("Thêm video", response.text)
        self.assertIn("Tải bản sao lưu về PC", response.text)
        self.assertIn("Thống kê", response.text)
        self.assertIn("Điện thoại này", response.text)
        self.assertIn("Hôm nay theo điện thoại", response.text)
        self.assertIn("Hiện mọi dòng đã quét. Dòng mới nhất ở trên.", response.text)
        self.assertIn("Không giới hạn số video", response.text)
        self.assertIn("Nhiều danh sách trên một máy cũng tải lên cùng lúc.", response.text)
        self.assertIn("Vài video lên máy chủ cùng lúc.", response.text)
        self.assertIn("top: -9999px", response.text)
        self.assertNotIn("/v1/videos?limit=", response.text)
        self.assertNotIn("#file { display: none", response.text)
        self.assertIn('var serverToken = "%s";' % settings.page_token, response.text)
        self.assertNotIn("test-token", response.text)
        self.assertNotIn("__CONTROL_TOKEN__", response.text)
        self.assertNotIn("Mã kết nối", response.text)
        self.assertNotIn("Check proxy ngay", response.text)
        self.assertNotIn("Mở giả lập điện thoại trên PC", response.text)

    def test_page_token_opens_only_the_video_api(self) -> None:
        page = {"Authorization": f"Bearer {settings.page_token}"}
        self.assertTrue(settings.page_token)
        self.assertNotEqual(settings.page_token, "test-token")
        self.assertEqual(self.client.get("/v1/videos/stats", headers=page).status_code, 200)
        listed = self.client.get("/v1/results", headers=page)
        self.assertEqual(listed.status_code, 200)
        body = listed.json()
        self.assertEqual(body["count"], len(body["items"]))
        self.assertIn("total", body)
        self.assertIn("before", body)
        self.assertEqual(self.client.post("/v1/backup/ticket", headers=page).status_code, 200)
        self.assertEqual(self.client.get("/v1/actions", headers=page).status_code, 401)
        self.assertEqual(self.client.get("/v1/agents", headers=page).status_code, 401)
        self.assertEqual(
            self.client.post("/v1/updates/packages/upload", headers=page, files={"file": ("x.zip", b"x")}).status_code,
            401,
        )
        self.assertEqual(self.client.get("/v1/actions", headers=self.headers).status_code, 200)

    def test_api_docs_are_not_published(self) -> None:
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_backup_ticket_cannot_be_forged_or_reused_after_expiry(self) -> None:
        from control_plane.video_api import make_ticket, ticket_ok

        ticket = make_ticket(1000.0)
        self.assertTrue(ticket_ok(ticket, 1000.0))
        self.assertFalse(ticket_ok(ticket, 1000.0 + 601))
        expiry, _, signature = ticket.partition("-")
        self.assertFalse(ticket_ok(f"{int(expiry) + 3600}-{signature}", 1000.0))
        self.assertFalse(ticket_ok("garbage", 1000.0))
        self.assertEqual(self.client.get(f"/v1/backup/{expiry}-{'0' * 32}").status_code, 404)

    def test_upload_keeps_the_phone_name(self) -> None:
        uploaded = self.client.post(
            "/v1/videos",
            headers=self.headers,
            data={"device": "  Máy 1  "},
            files={"file": ("may.mp4", b"from-phone-1", "video/mp4")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()["device"], "Máy 1")
        listed = self.client.get("/v1/videos", headers=self.headers, params={"device": "Máy 1"})
        self.assertEqual(listed.status_code, 200)
        self.assertIn("may.mp4", [item["name"] for item in listed.json()["items"]])
        phones = self.client.get("/v1/videos/stats", headers=self.headers).json()["phones"]
        self.assertIn("Máy 1", phones["known"])
        mine = next(item for item in phones["items"] if item["device"] == "Máy 1")
        self.assertGreaterEqual(mine["uploaded"], 1)
        self.assertEqual(mine["errors"], 0)

    def _chunk(
        self,
        upload_id: str,
        name: str,
        payload: bytes,
        offset: int,
        total: int,
        device: str = "Máy 1",
    ):
        return self.client.post(
            "/v1/videos/chunks",
            headers=self.headers,
            data={
                "upload_id": upload_id,
                "name": name,
                "size": str(total),
                "offset": str(offset),
                "device": device,
            },
            files={"file": ("chunk", payload, "application/octet-stream")},
        )

    def test_chunked_upload_resumes_and_ignores_a_lost_response(self) -> None:
        upload_id = "a1" * 16
        payload = b"hello world"
        denied = self.client.post(
            "/v1/videos/chunks",
            data={"upload_id": upload_id, "name": "clip.mp4", "size": "11", "offset": "0", "device": "Máy 1"},
            files={"file": ("chunk", b"hello", "application/octet-stream")},
        )
        self.assertEqual(denied.status_code, 401)
        first = self._chunk(upload_id, "clip.mp4", b"hello", 0, len(payload), device="  Máy 1  ")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["received"], 5)
        self.assertFalse(first.json()["complete"])
        # Mảnh đã ghi gửi lại thì không nhân đôi byte.
        again = self._chunk(upload_id, "clip.mp4", b"hello", 0, len(payload))
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["received"], 5)
        gap = self._chunk(upload_id, "clip.mp4", b"!", 8, len(payload))
        self.assertEqual(gap.status_code, 409, gap.text)
        self.assertEqual(gap.json()["received"], 5)
        status = self.client.get(f"/v1/videos/chunks/{upload_id}", headers=self.headers)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["received"], 5)
        done = self._chunk(upload_id, "clip.mp4", b" world", 5, len(payload))
        self.assertEqual(done.status_code, 200, done.text)
        body = done.json()
        self.assertTrue(body["complete"])
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["device"], "Máy 1")
        self.assertFalse(body["duplicate"])
        stored = settings.video_dir / f"{body['id']}.mp4"
        self.assertEqual(stored.read_bytes(), payload)
        # Phản hồi chốt file bị mất. Hỏi lại thì vẫn nhận đúng kết quả, không gửi lại cả file.
        replay = self.client.get(f"/v1/videos/chunks/{upload_id}", headers=self.headers)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["id"], body["id"])
        self.assertTrue(replay.json()["duplicate"] or replay.json()["complete"])
        other = "b2" * 16
        duplicate = self._chunk(other, "clip-copy.mp4", payload, 0, len(payload))
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(duplicate.json()["id"], body["id"])

    def test_chunk_rejects_a_bad_file_and_a_foreign_upload_id(self) -> None:
        upload_id = "c3" * 16
        bad = self._chunk(upload_id, "notes.txt", b"abc", 0, 3)
        self.assertEqual(bad.status_code, 400, bad.text)
        missing = self.client.get("/v1/videos/chunks/not-a-real-id", headers=self.headers)
        self.assertEqual(missing.status_code, 400, missing.text)
        huge = self._chunk(upload_id, "big.mp4", b"ab", 0, settings.max_upload_mb * 1024 * 1024 + 1)
        self.assertEqual(huge.status_code, 413, huge.text)

    def test_same_video_after_it_is_read_is_read_again(self) -> None:
        from control_plane.video_store import connect

        first = self.client.post(
            "/v1/videos", headers=self.headers, files={"file": ("again.mp4", b"read-me-twice", "video/mp4")}
        )
        video_id = first.json()["id"]
        with connect(settings.video_db_path) as conn:
            conn.execute("UPDATE videos SET status = 'done', path = '' WHERE id = ?", (video_id,))
        second = self.client.post(
            "/v1/videos", headers=self.headers, files={"file": ("again.mp4", b"read-me-twice", "video/mp4")}
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()["duplicate"])
        self.assertEqual(second.json()["id"], video_id)
        self.assertEqual(second.json()["status"], "queued")
        self.assertNotIn("old_path", second.json())

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
        self.assertIn("Kết quả đã lưu", page.text)
        self.assertIn("Tải bản sao lưu về PC", page.text)
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
        self.assertIn("Đọc video danh bạ", home.text)
        self.assertIn("Tải bản sao lưu về PC", home.text)
        self.assertNotIn("Lướt trên iPhone", home.text)
        self.assertIn('href="/tai"', page.text)

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
        self.assertIn("Cài Danh bạ trên iPhone", page.text)
        self.assertIn('href="/danhba/"', page.text)
        self.assertEqual(payload["danhbaPath"], "/danhba/")
        self.assertEqual(payload["danhbaBuild"], 1)
        self.assertEqual(body["danhba"], "/danhba/")
        self.assertEqual(body["danhbaBuild"], 1)

        install = self.client.get("/danhba", follow_redirects=False)
        self.assertEqual(install.status_code, 302, install.text)
        self.assertEqual(install.headers["location"], "/danhba/")
        screen = self.client.get("/danhba/")
        self.assertEqual(screen.status_code, 200, screen.text)
        self.assertEqual(screen.headers["cache-control"], "no-cache")
        self.assertIn("Nạp lên iPhone", screen.text)
        self.assertIn('content="1"', screen.text)
        self.assertIn("/danhba/sw.js", screen.text)
        self.assertIn("/danhba/app.js?v=1", screen.text)
        self.assertIn("Thêm vào Màn hình chính", screen.text)
        version = self.client.get("/danhba/version")
        self.assertEqual(version.status_code, 200, version.text)
        self.assertEqual(version.json()["build"], 1)
        worker = self.client.get("/danhba/sw.js")
        self.assertEqual(worker.status_code, 200, worker.text)
        self.assertIn("javascript", worker.headers["content-type"])
        self.assertIn('var BUILD = "1";', worker.text)
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
            self.assertNotIn("token", guide.lower())


class PcReaderApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)

    def test_reader_zip_matches_the_files_on_disk(self) -> None:
        denied = self.client.get("/v1/pc/reader")
        self.assertEqual(denied.status_code, 401)
        manifest = self.client.get("/v1/pc/reader", headers=self.headers)
        self.assertEqual(manifest.status_code, 200, manifest.text)
        body = manifest.json()
        self.assertEqual(body["entry"], "doc_video.pipeline:read_video")
        self.assertIn("doc_video/pipeline.py", body["files"])
        package = self.client.get("/v1/pc/reader.zip", headers=self.headers)
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.headers["x-reader-sha256"], body["sha256"])
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            self.assertEqual(archive.read("doc_video/pipeline.py"), (Path(__file__).resolve().parents[1] / "doc_video" / "pipeline.py").read_bytes())

    def test_package_bakes_the_page_and_updates_without_a_new_install(self) -> None:
        ticket = self.client.post("/v1/pc/ticket", headers=self.headers)
        self.assertEqual(ticket.status_code, 200, ticket.text)
        downloaded = self.client.get(ticket.json()["url"])
        self.assertEqual(downloaded.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            script = archive.read("ghiam-pc.py").decode("utf-8")
            self.assertIn("ghiam-pc.bat", archive.namelist())
        self.assertIn(settings.page_token, script)
        self.assertNotIn("__GHIAM_", script)
        self.assertIn("doc_video.pipeline", script)
        version = self.client.get("/v1/pc/connector/version", headers=self.headers)
        self.assertEqual(version.status_code, 200)
        self.assertGreaterEqual(version.json()["version"], 1)
        backup = self.client.post("/v1/backup/ticket", headers=self.headers)
        stolen = self.client.get("/v1/pc/package/" + backup.json()["url"].rsplit("/", 1)[-1])
        self.assertEqual(stolen.status_code, 404)

    def test_pc_uploads_the_table_it_read(self) -> None:
        init_db(settings.video_db_path)
        created = begin_upload(settings.video_db_path, name="pc.mp4", size_bytes=5, sha256="pc-api-video")
        self.assertFalse(created["duplicate"])
        video_id = int(created["id"])
        path = settings.video_dir / f"{video_id}.mp4"
        path.write_bytes(b"video")
        commit_upload(settings.video_db_path, video_id, str(path))
        with connect(settings.video_db_path) as conn:
            conn.execute("UPDATE videos SET status = 'done' WHERE status = 'queued' AND id != ?", (video_id,))
        before = video_stats(settings.video_db_path)
        claimed = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC"})
        self.assertEqual(claimed.status_code, 200, claimed.text)
        job = claimed.json()["video"]
        self.assertEqual(job["id"], video_id)
        downloaded = self.client.get(
            f"/v1/pc/videos/{video_id}/file",
            headers=self.headers,
            params={"lease": job["lease"]},
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"video")
        denied = self.client.get(f"/v1/pc/videos/{video_id}/file", headers=self.headers, params={"lease": "sai"})
        self.assertEqual(denied.status_code, 409)
        posted = self.client.post(
            f"/v1/pc/videos/{video_id}/result",
            headers=self.headers,
            json={
                "lease": job["lease"],
                "rows": [{"phone": "0900000091", "name": "Mot", "username": "@mot91"}],
                "review": [],
                "unopened": [{"phone": "0900000092", "name": "Hai"}],
            },
        )
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertEqual(posted.json()["rows"], 1)
        after = video_stats(settings.video_db_path)
        self.assertEqual(after["saved"], before["saved"] + 1)
        self.assertEqual(after["unopened"], before["unopened"] + 1)
        self.assertEqual(after["pc"], 0)
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
