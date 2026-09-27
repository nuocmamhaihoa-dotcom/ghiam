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


if __name__ == "__main__":
    unittest.main()
