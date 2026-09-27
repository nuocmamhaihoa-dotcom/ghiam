"""Journal of operator actions: local file, search, and hub database."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from control_plane import db
from fb_poller.journal import list_local, redact_argv, remember


class JournalFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "actions.jsonl"
        self._url = os.environ.pop("CONTROL_URL", None)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._url is not None:
            os.environ["CONTROL_URL"] = self._url

    def test_remember_and_search_newest_first(self) -> None:
        remember(summary="import 300 URL", kind="cli", source="cli", path=self.path, push=False)
        remember(summary="rebalance hot", kind="note", source="manual", path=self.path, push=False, detail="size 100")
        rows = list_local(path=self.path, limit=10)
        self.assertEqual([row["summary"] for row in rows], ["rebalance hot", "import 300 URL"])
        found = list_local(q="300", path=self.path)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["summary"], "import 300 URL")
        self.assertEqual(list_local(q="size 100", path=self.path)[0]["kind"], "note")

    def test_empty_summary_rejected(self) -> None:
        with self.assertRaises(ValueError):
            remember(summary="   ", path=self.path, push=False)

    def test_redacts_token_flags(self) -> None:
        text = redact_argv(["sync-push", "--token", "secret-value", "--control-token=other"])
        self.assertNotIn("secret-value", text)
        self.assertNotIn("other", text)
        self.assertIn("--token", text)
        self.assertIn("***", text)

    def test_prunes_when_file_grows(self) -> None:
        import fb_poller.journal as journal

        old_bytes = journal._PRUNE_BYTES
        old_max = journal.MAX_LOCAL
        journal._PRUNE_BYTES = 80
        journal.MAX_LOCAL = 5
        try:
            for i in range(12):
                remember(summary=f"thao tac {i} " + ("x" * 40), path=self.path, push=False)
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertLessEqual(len(lines), 5)
            last = json.loads(lines[-1])
            self.assertIn("thao tac 11", last["summary"])
            first = json.loads(lines[0])
            self.assertNotIn("thao tac 0 ", first["summary"])
        finally:
            journal._PRUNE_BYTES = old_bytes
            journal.MAX_LOCAL = old_max

    def test_push_posts_to_hub(self) -> None:
        received: list[bytes] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                received.append(self.rfile.read(length))
                body = b'{"ok": true, "id": 1}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        os.environ["CONTROL_URL"] = f"http://127.0.0.1:{port}"
        os.environ["CONTROL_TOKEN"] = "tok"
        try:
            entry = remember(summary="doi proxy die", kind="note", source="cli", path=self.path)
        finally:
            server.shutdown()
            server.server_close()
            os.environ.pop("CONTROL_TOKEN", None)
            os.environ.pop("CONTROL_URL", None)
        self.assertTrue(entry["pushed"])
        payload = json.loads(received[0].decode("utf-8"))
        self.assertEqual(payload["summary"], "doi proxy die")
        self.assertEqual(payload["kind"], "note")


class HubActionDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "server.db"
        db.init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_search_and_kind_filter(self) -> None:
        db.record_action(
            self.db_path,
            at="2026-09-27T00:00:00+00:00",
            actor="pc20",
            source="cli",
            kind="cli",
            summary="Import URL từ posts.txt (300 dòng)",
        )
        db.record_action(
            self.db_path,
            at="2026-09-27T00:01:00+00:00",
            actor="me",
            source="hub",
            kind="proxy_check",
            summary="Check proxy: live=2 die=1",
            detail="checked=3",
        )
        newest = db.list_actions(self.db_path, limit=10)
        self.assertEqual(newest[0]["kind"], "proxy_check")
        found = db.list_actions(self.db_path, q="posts.txt")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["actor"], "pc20")
        checks = db.list_actions(self.db_path, kind="proxy_check")
        self.assertEqual(len(checks), 1)
        self.assertEqual(db.list_actions(self.db_path, q="%"), [])


if __name__ == "__main__":
    unittest.main()
