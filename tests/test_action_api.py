"""HTTP API for the operator action journal."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix="fb-actions-")
os.environ["CONTROL_DATA_DIR"] = _TMP
os.environ["CONTROL_DB"] = str(Path(_TMP) / "server.db")
os.environ["CONTROL_TOKEN"] = "test-token"
os.environ["CONTROL_PROXIES_FILE"] = str(Path(_TMP) / "proxies.txt")
os.environ["CONTROL_PACKAGES_DIR"] = str(Path(_TMP) / "packages")
os.environ["CONTROL_PROXY_CHECK_SEC"] = "86400"
Path(_TMP, "proxies.txt").write_text("", encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from control_plane.app import _raw_transfer, app  # noqa: E402
from control_plane.pc_connector import bundle_sha256  # noqa: E402
from control_plane.pc_reader import connector_template_sha256  # noqa: E402
from control_plane.settings import settings  # noqa: E402
from control_plane.video_api import PC_TICKET, PC_TICKET_SECONDS, ticket_ok  # noqa: E402
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
        self.assertIn("Báo cáo nhanh", response.text)
        self.assertIn("Thống kê hôm nay", response.text)
        self.assertIn("Thống kê toàn bộ thời gian", response.text)
        self.assertIn("Chi tiết theo điện thoại", response.text)
        self.assertIn("Thiếu tên", response.text)
        self.assertIn("Điện thoại này", response.text)
        self.assertIn("Hôm nay theo điện thoại", response.text)
        self.assertIn("Toàn bộ thời gian", response.text)
        self.assertEqual(response.headers["cache-control"], "no-cache, no-store, must-revalidate")
        self.assertIn('data-span="all"', response.text)
        self.assertIn('data-span="today"', response.text)
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
        self.assertEqual(payload["danhbaBuild"], 3)
        self.assertEqual(body["danhba"], "/danhba/")
        self.assertEqual(body["danhbaBuild"], 3)

        install = self.client.get("/danhba", follow_redirects=False)
        self.assertEqual(install.status_code, 302, install.text)
        self.assertEqual(install.headers["location"], "/danhba/")
        screen = self.client.get("/danhba/")
        self.assertEqual(screen.status_code, 200, screen.text)
        self.assertEqual(screen.headers["cache-control"], "no-cache")
        self.assertIn("Nạp lên iPhone", screen.text)
        self.assertIn('content="3"', screen.text)
        self.assertIn("/danhba/sw.js", screen.text)
        self.assertIn("/danhba/app.js?v=3", screen.text)
        self.assertIn("Thêm vào Màn hình chính", screen.text)
        self.assertIn("Bỏ nhóm trên trang", screen.text)
        self.assertIn("\\/v1\\/results\\/missing\\/", screen.text)
        version = self.client.get("/danhba/version")
        self.assertEqual(version.status_code, 200, version.text)
        self.assertEqual(version.json()["build"], 3)
        worker = self.client.get("/danhba/sw.js")
        self.assertEqual(worker.status_code, 200, worker.text)
        self.assertIn("javascript", worker.headers["content-type"])
        self.assertIn('var BUILD = "3";', worker.text)
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
        self.assertIn("runtime", body)
        package = self.client.get("/v1/pc/reader.zip", headers=self.headers)
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.headers["x-reader-sha256"], body["sha256"])
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            self.assertEqual(archive.read("doc_video/pipeline.py"), (Path(__file__).resolve().parents[1] / "doc_video" / "pipeline.py").read_bytes())

    def test_plain_letters_match_the_folder_tesseract_uses_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "eng.traineddata").write_bytes(b"plain-eng")
            (folder / "vie.traineddata").write_bytes(b"plain-vie")
            with patch("control_plane.pc_reader.default_tessdata_dir", return_value=folder):
                self.assertEqual(self.client.get("/v1/pc/tessdata-default.zip").status_code, 401)
                manifest = self.client.get("/v1/pc/reader", headers=self.headers).json()
                expected = bundle_sha256(folder, ["eng.traineddata", "vie.traineddata"])
                self.assertEqual(manifest["tessdata_default"], {"sha256": expected, "bytes": 18})
                served = self.client.get("/v1/pc/tessdata-default.zip", headers=self.headers)
                self.assertEqual(served.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(served.content)) as archive:
                    self.assertEqual(archive.read("vie.traineddata"), b"plain-vie")
            with patch("control_plane.pc_reader.default_tessdata_dir", return_value=None):
                self.assertIsNone(self.client.get("/v1/pc/reader", headers=self.headers).json()["tessdata_default"])
                self.assertEqual(self.client.get("/v1/pc/tessdata-default.zip", headers=self.headers).status_code, 404)

    def test_package_bakes_the_page_and_updates_without_a_new_install(self) -> None:
        ticket = self.client.post("/v1/pc/ticket", headers=self.headers)
        self.assertEqual(ticket.status_code, 200, ticket.text)
        issued = ticket.json()
        self.assertTrue(issued["url"].startswith("/v1/pc/package/"))
        self.assertTrue(issued["link"].endswith(issued["url"]))
        self.assertTrue(issued["link"].startswith("http"))
        expiry = int(issued["url"].rsplit("/", 1)[-1].split("-", 1)[0])
        self.assertGreater(expiry, int(time.time()) + 23 * 60 * 60)
        self.assertTrue(ticket_ok(issued["url"].rsplit("/", 1)[-1], time.time(), PC_TICKET))
        self.assertEqual(PC_TICKET_SECONDS, 24 * 60 * 60)
        downloaded = self.client.get(issued["url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("ghiam-pc.bat", downloaded.headers["content-disposition"])
        body = downloaded.content.decode("utf-8")
        self.assertIn(settings.page_token, body)
        self.assertNotIn("__GHIAM_", body)
        self.assertIn("/v1/pc/open.ps1", body)
        self.assertNotIn("WaitForExit", body)
        self.assertNotIn("python.org", body)
        self.assertNotIn("pypi.org", body)
        self.assertEqual(self.client.get("/v1/pc/open.ps1").status_code, 401)
        installer = self.client.get("/v1/pc/open.ps1", headers={"Authorization": f"Bearer {settings.page_token}"})
        self.assertEqual(installer.status_code, 200)
        self.assertNotIn("content-encoding", installer.headers)
        steps = installer.content.decode("utf-8")
        self.assertTrue(steps.startswith("# GHIAM-OPEN"))
        self.assertNotIn(settings.page_token, steps)
        self.assertIn("/v1/pc/connector.py", steps)
        self.assertIn("/v1/pc/tools/python.zip", steps)
        self.assertIn("/v1/pc/tools/wheels.zip", steps)
        self.assertIn("/v1/pc/tools/tesseract.zip", steps)
        self.assertIn("/v1/pc/tools/crt.zip", steps)
        self.assertIn("/v1/pc/tools/ffmpeg.zip", steps)
        self.assertTrue(_raw_transfer("/v1/pc/open.ps1"))
        self.assertTrue(_raw_transfer("/v1/pc/tessdata-default.zip"))
        script = self.client.get("/v1/pc/connector.py", headers=self.headers)
        self.assertEqual(script.status_code, 200)
        program = script.content.decode("utf-8")
        self.assertIn(settings.page_token, program)
        self.assertNotIn("__GHIAM_", program)
        self.assertIn("doc_video.pipeline", program)
        self.assertIn("DOC_VIDEO_CPU_FRACTION", program)
        self.assertIn("DOC_VIDEO_RAM_FRACTION", program)
        self.assertIn("socket.gethostname", program)
        self.assertIn('{"name": name, "version": str(CONNECTOR_VERSION)}', program)
        version = self.client.get("/v1/pc/connector/version", headers=self.headers)
        self.assertEqual(version.status_code, 200)
        self.assertGreaterEqual(version.json()["version"], 4)
        self.assertEqual(version.json()["sha256"], connector_template_sha256())
        self.assertEqual(len(version.json()["sha256"]), 64)
        missing = self.client.get("/v1/pc/tools/python.zip")
        self.assertEqual(missing.status_code, 401)
        absent = self.client.get("/v1/pc/tools/python.zip", headers=self.headers)
        self.assertEqual(absent.status_code, 404)
        unknown = self.client.get("/v1/pc/tools/not-a-tool", headers=self.headers)
        self.assertEqual(unknown.status_code, 404)
        tool_dir = settings.data_dir / "pc-tools"
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "python-3.12.7-embed-amd64.zip").write_bytes(b"python-zip")
        tool = self.client.get("/v1/pc/tools/python.zip", headers=self.headers)
        self.assertEqual(tool.status_code, 200)
        self.assertEqual(tool.content, b"python-zip")
        runtime_path = tool_dir / "runtime-cp312-win.zip"
        runtime_path.unlink(missing_ok=True)
        before_runtime = self.client.get("/v1/pc/reader", headers=self.headers)
        self.assertIsNone(before_runtime.json()["runtime"])
        runtime_bytes = b"runtime-zip"
        runtime_path.write_bytes(runtime_bytes)
        runtime = self.client.get("/v1/pc/tools/runtime.zip", headers=self.headers)
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.content, runtime_bytes)
        again = self.client.get("/v1/pc/reader", headers=self.headers)
        self.assertEqual(again.json()["runtime"]["bytes"], len(runtime_bytes))
        self.assertEqual(again.json()["runtime"]["sha256"], hashlib.sha256(runtime_bytes).hexdigest())
        self.assertTrue(_raw_transfer("/v1/pc/tools/python.zip"))
        backup = self.client.post("/v1/backup/ticket", headers=self.headers)
        stolen = self.client.get("/v1/pc/package/" + backup.json()["url"].rsplit("/", 1)[-1])
        self.assertEqual(stolen.status_code, 404)

    def test_pc_uploads_the_table_it_read(self) -> None:
        init_db(settings.video_db_path)
        created = begin_upload(settings.video_db_path, name="pc.mp4", size_bytes=5, sha256="pc-api-video")
        self.assertFalse(created["duplicate"])
        video_id = int(created["id"])
        path = settings.video_dir / f"{video_id}.mp4"
        payload_bytes = b"v" * 800
        path.write_bytes(payload_bytes)
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
            headers={**self.headers, "Accept-Encoding": "gzip"},
            params={"lease": job["lease"]},
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, payload_bytes)
        self.assertNotIn("gzip", downloaded.headers.get("content-encoding", ""))
        self.assertTrue(_raw_transfer(f"/v1/pc/videos/{video_id}/file"))
        self.assertFalse(_raw_transfer("/v1/videos"))
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

    def _queue_only(self, sha: str) -> int:
        init_db(settings.video_db_path)
        with connect(settings.video_db_path) as conn:
            conn.execute("UPDATE videos SET status = 'done' WHERE status IN ('queued', 'pc')")
        created = begin_upload(settings.video_db_path, name=f"{sha}.mp4", size_bytes=5, sha256=sha)
        video_id = int(created["id"])
        path = settings.video_dir / f"{video_id}.mp4"
        path.write_bytes(b"v" * 64)
        commit_upload(settings.video_db_path, video_id, str(path))
        return video_id

    def test_invite_link_gives_a_pc_only_code_that_can_be_revoked(self) -> None:
        made = self.client.post("/v1/pc/invites", headers=self.headers, json={"label": "Văn phòng"})
        self.assertEqual(made.status_code, 200, made.text)
        invite = made.json()
        self.assertTrue(invite["link"].endswith("/v1/pc/join/" + invite["code"]))
        self.assertTrue(_raw_transfer("/v1/pc/join/" + invite["code"]))
        self.assertTrue(_raw_transfer("/v1/pc/launcher.bat"))
        joined = self.client.get("/v1/pc/join/" + invite["code"])
        self.assertEqual(joined.status_code, 200)
        bat = joined.content.decode("utf-8")
        self.assertNotIn(settings.page_token, bat)
        found = re.search(r"\$token = '([^']+)'", bat)
        assert found is not None
        token = found.group(1)
        pc = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/v1/pc/reader", headers=pc).status_code, 200)
        program = self.client.get("/v1/pc/connector.py", headers=pc).content.decode("utf-8")
        self.assertIn(token, program)
        self.assertNotIn(settings.page_token, program)
        self.assertIn(token, self.client.get("/v1/pc/launcher.bat", headers=pc).content.decode("utf-8"))
        self.assertEqual(self.client.get("/v1/results", headers=pc).status_code, 401)
        self.assertEqual(self.client.get("/v1/pcs", headers=pc).status_code, 401)
        self.assertEqual(self.client.post("/v1/pc/invites", headers=pc, json={}).status_code, 401)
        self.assertEqual(self.client.post("/v1/pc/ticket", headers=pc).status_code, 401)
        status = self.client.post("/v1/pc/status", headers=pc, json={"name": "PC mời", "state": "testing"})
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["wait"], "")
        listed = self.client.get("/v1/pcs", headers=self.headers).json()
        item = next(entry for entry in listed["items"] if entry["name"] == "PC mời")
        self.assertEqual((item["invite"], item["state"], item["online"]), ("Văn phòng", "testing", True))
        self.assertEqual(next(entry for entry in listed["invites"] if entry["code"] == invite["code"])["pcs"], 1)
        revoked = self.client.post(f"/v1/pc/invites/{invite['id']}/revoke", headers=self.headers)
        self.assertEqual(revoked.status_code, 200)
        denied = self.client.get("/v1/pc/reader", headers=pc)
        self.assertEqual(denied.status_code, 401)
        self.assertIn("thu hồi", denied.json()["detail"])
        self.assertEqual(self.client.get("/v1/pc/join/" + invite["code"]).status_code, 404)
        listed = self.client.get("/v1/pcs", headers=self.headers).json()
        self.assertEqual(next(entry for entry in listed["invites"] if entry["code"] == invite["code"])["link"], "")
        page_pc = {"Authorization": f"Bearer {settings.page_token}"}
        self.assertEqual(self.client.get("/v1/pc/reader", headers=page_pc).status_code, 200)

    def test_paused_pc_waits_and_progress_reaches_the_page(self) -> None:
        video_id = self._queue_only("pause-api")
        self.assertEqual(self.client.post("/v1/pc/status", headers=self.headers, json={"name": "PC tạm"}).status_code, 200)
        paused = self.client.post("/v1/pcs/update", headers=self.headers, json={"name": "PC tạm", "paused": True})
        self.assertEqual(paused.status_code, 200, paused.text)
        waiting = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC tạm"}).json()
        self.assertIsNone(waiting["video"])
        self.assertEqual(waiting["wait"], "paused")
        bad = self.client.post("/v1/pcs/update", headers=self.headers, json={"name": "PC tạm", "schedule": "25:00-26:00"})
        self.assertEqual(bad.status_code, 400)
        missing = self.client.post("/v1/pcs/update", headers=self.headers, json={"name": "Không có", "paused": True})
        self.assertEqual(missing.status_code, 404)
        self.client.post("/v1/pcs/update", headers=self.headers, json={"name": "PC tạm", "paused": False, "autostart": True})
        claimed = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC tạm", "version": "5"}).json()
        self.assertEqual(claimed["video"]["id"], video_id)
        self.assertTrue(claimed["autostart"])
        lease = claimed["video"]["lease"]
        beat = self.client.post(
            f"/v1/pc/videos/{video_id}/heartbeat",
            headers=self.headers,
            json={"lease": lease, "stage": "read", "done": 3, "total": 10},
        )
        self.assertEqual(beat.status_code, 200)
        listed = self.client.get("/v1/pcs", headers=self.headers).json()["items"]
        item = next(entry for entry in listed if entry["name"] == "PC tạm")
        self.assertEqual((item["video"]["id"], item["video"]["stage"], item["video"]["done"]), (video_id, "read", 3))
        self.assertEqual(item["version"], "5")
        queue = self.client.get("/v1/videos", headers=self.headers).json()["items"]
        row = next(entry for entry in queue if entry["id"] == video_id)
        self.assertEqual((row["progress_stage"], row["progress_done"], row["progress_total"]), ("read", 3, 10))
        busy = self.client.post("/v1/pcs/forget", headers=self.headers, json={"name": "PC tạm"})
        self.assertEqual(busy.status_code, 409)
        posted = self.client.post(
            f"/v1/pc/videos/{video_id}/result",
            headers=self.headers,
            json={"lease": lease, "rows": [], "review": [], "unopened": [], "download_s": 12.5, "read_s": 300},
        )
        self.assertEqual(posted.status_code, 200, posted.text)
        item = next(entry for entry in self.client.get("/v1/pcs", headers=self.headers).json()["items"] if entry["name"] == "PC tạm")
        self.assertEqual((item["avg_download_s"], item["avg_read_s"], item["ok_count"]), (12.5, 300.0, 1))
        self.assertIsNone(item["video"])

    def test_urgent_video_moves_to_the_front(self) -> None:
        first = self._queue_only("urgent-a")
        created = begin_upload(settings.video_db_path, name="urgent-b.mp4", size_bytes=5, sha256="urgent-b")
        second = int(created["id"])
        path = settings.video_dir / f"{second}.mp4"
        path.write_bytes(b"v" * 64)
        commit_upload(settings.video_db_path, second, str(path))
        queue = {item["id"]: item for item in self.client.get("/v1/videos", headers=self.headers).json()["items"]}
        self.assertEqual((queue[first]["position"], queue[second]["position"]), (1, 2))
        marked = self.client.post(f"/v1/videos/{second}/priority", headers=self.headers, json={"urgent": True})
        self.assertEqual(marked.status_code, 200, marked.text)
        queue = {item["id"]: item for item in self.client.get("/v1/videos", headers=self.headers).json()["items"]}
        self.assertEqual((queue[second]["position"], queue[second]["priority"]), (1, 1))
        claimed = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC gấp"}).json()
        self.assertEqual(claimed["video"]["id"], second)
        gone = self.client.post(f"/v1/videos/{second}/priority", headers=self.headers, json={"urgent": False})
        self.assertEqual(gone.status_code, 404)
        self.client.post(f"/v1/pc/videos/{second}/release", headers=self.headers, json={"lease": claimed["video"]["lease"], "reason": "disk"})

    def test_results_filters_export_and_read_only_keys(self) -> None:
        video_id = self._queue_only("export-api")
        claimed = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC xuất"}).json()
        posted = self.client.post(
            f"/v1/pc/videos/{video_id}/result",
            headers=self.headers,
            json={"lease": claimed["video"]["lease"], "rows": [{"phone": "0977000111", "name": "Đặng Thị Xuân", "username": "@xuan_ok"}]},
        )
        self.assertEqual(posted.status_code, 200, posted.text)
        found = self.client.get("/v1/results", headers=self.headers, params={"q": "dang thi xuan"}).json()
        self.assertEqual([item["phone"] for item in found["items"]], ["0977000111"])
        self.assertEqual(found["items"][0]["carrier"], "Viettel")
        by_video = self.client.get("/v1/results", headers=self.headers, params={"video": video_id, "bucket": "saved"}).json()
        self.assertEqual(by_video["total"], 1)
        bad = self.client.get("/v1/results", headers=self.headers, params={"day_from": "16/10/2026"})
        self.assertEqual(bad.status_code, 400)
        ticket = self.client.post("/v1/results/export/ticket", headers=self.headers).json()
        self.assertTrue(_raw_transfer(ticket["url"]))
        excel = self.client.get(ticket["url"], params={"video": video_id})
        self.assertEqual(excel.status_code, 200)
        self.assertIn(".xlsx", excel.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(excel.content)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("0977000111", sheet)
        self.assertIn("Đặng Thị Xuân", sheet)
        self.assertEqual(self.client.get("/v1/results/export/1-abc").status_code, 404)
        self.assertEqual(self.client.post("/v1/results/export/ticket").status_code, 401)
        made = self.client.post("/v1/api-keys", headers=self.headers, json={"name": "Kế toán"}).json()
        key = made["key"]
        self.assertEqual(self.client.get("/v1/api/results").status_code, 401)
        api = self.client.get("/v1/api/results", headers={"X-Api-Key": key}, params={"after_id": 0, "limit": 5000}).json()
        self.assertIn("0977000111", [item["phone"] for item in api["items"]])
        bearer = self.client.get("/v1/api/stats", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(bearer.status_code, 200)
        self.assertEqual(self.client.get("/v1/results", headers={"Authorization": f"Bearer {key}"}).status_code, 401)
        self.assertEqual(self.client.get("/v1/pc/reader", headers={"Authorization": f"Bearer {key}"}).status_code, 401)
        listed = self.client.get("/v1/api-keys", headers=self.headers).json()["items"]
        self.assertNotIn("key", listed[0])
        self.assertEqual(self.client.post(f"/v1/api-keys/{made['id']}/revoke", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/v1/api/stats", headers={"X-Api-Key": key}).status_code, 401)

    def test_webhook_rejects_internal_addresses(self) -> None:
        saved = self.client.post("/v1/webhook", headers=self.headers, json={"url": "http://127.0.0.1:9/hook"})
        self.assertEqual(saved.status_code, 400)
        tried = self.client.post("/v1/webhook/test", headers=self.headers, json={"url": "http://169.254.169.254/x"})
        self.assertEqual(tried.status_code, 400)
        cleared = self.client.post("/v1/webhook", headers=self.headers, json={"url": ""})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["url"], "")
        self.assertEqual(self.client.get("/v1/webhook").status_code, 401)

    def test_disk_release_from_a_pc_is_not_counted(self) -> None:
        video_id = self._queue_only("disk-api")
        claimed = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC đầy"}).json()
        released = self.client.post(
            f"/v1/pc/videos/{video_id}/release",
            headers=self.headers,
            json={"lease": claimed["video"]["lease"], "reason": "disk"},
        )
        self.assertTrue(released.json()["ok"])
        item = next(entry for entry in self.client.get("/v1/pcs", headers=self.headers).json()["items"] if entry["name"] == "PC đầy")
        self.assertEqual((item["fail_count"], item["state"]), (0, "wait-disk"))

    def test_missing_numbers_can_be_handed_to_danhba(self) -> None:
        video_id = self._queue_only("missing-api")
        claimed = self.client.post("/v1/pc/claim", headers=self.headers, json={"name": "PC thiếu"}).json()
        posted = self.client.post(
            f"/v1/pc/videos/{video_id}/result",
            headers=self.headers,
            json={"lease": claimed["video"]["lease"], "unopened": [{"phone": "0901234567", "name": "Mai"}]},
        )
        self.assertEqual(posted.status_code, 200, posted.text)
        listed = self.client.get("/v1/results/missing", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        mine = next(item for item in body["items"] if item["video_id"] == video_id)
        self.assertEqual(mine["unopened"], 1)
        self.assertNotIn("0901234567", listed.text)
        cover = self.client.get("/v1/results/coverage", headers=self.headers)
        self.assertEqual(cover.status_code, 200)
        self.assertTrue(any(item["carrier"] == "MobiFone" and item["unopened"] >= 1 for item in cover.json()["carriers"]))
        self.assertEqual(self.client.get("/v1/results/missing").status_code, 401)
        ticket = self.client.post("/v1/results/missing/ticket", headers=self.headers)
        self.assertEqual(ticket.status_code, 200, ticket.text)
        downloaded = self.client.get(ticket.json()["url"], params={"video": video_id})
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, "Mai\t0901234567\n".encode())
        self.assertIn("so-chua-mo", downloaded.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
