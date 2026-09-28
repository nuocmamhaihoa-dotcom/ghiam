"""HTTP API for the operator action journal."""

from __future__ import annotations

import os
import tempfile
import unittest
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
        self.assertIn("Sửa", response.text)
        self.assertIn("Lưu thành bản mới", response.text)
        self.assertIn("Lưu thành đoạn", response.text)
        self.assertIn("Kịch bản ghép", response.text)
        self.assertIn("Mở giả lập điện thoại trên PC", response.text)

    def test_phone_emulator_page(self) -> None:
        response = self.client.get("/phone")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Giả lập", response.text)
        self.assertIn('src="/?as=phone"', response.text)
        self.assertIn("Kết nối PC", response.text)

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
        profile = self.client.post(
            "/v1/people/sightings",
            headers=self.headers,
            json={"items": [{"kind": "profile", "name": "Trần Tùng", "username": "trn.tng751"}]},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["count"], 1)
        row = profile.json()["items"][0]
        self.assertEqual(row["name"], "Trần Tùng")
        self.assertEqual(row["contactName"], "A Tùng Bán Gạch")
        self.assertEqual(row["username"], "@trn.tng751")
        listed = self.client.get("/v1/people", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)
        page = self.client.get("/")
        self.assertIn("Đã lưu", page.text)
        self.assertIn("Tên trong danh bạ", page.text)
        sample = self.client.get("/sample-people")
        self.assertEqual(sample.status_code, 200)
        self.assertIn("A Tùng Bán Gạch", sample.text)
        self.assertIn("/static/watch.js", sample.text)
        phone = self.client.get("/phone")
        self.assertIn('src="/?as=phone"', phone.text)
        self.assertIn("Lướt danh bạ", phone.text)


if __name__ == "__main__":
    unittest.main()
