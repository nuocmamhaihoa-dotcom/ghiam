"""Nhịp sống của PC không được kẹt sau một lệnh đọc video dài."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from control_plane.video_helpers import LEASE_SECONDS, HelperBook
from pc_agent.video_worker import HubClient


class _LaneHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/slow":
            self.server.slow_started.set()  # type: ignore[attr-defined]
            time.sleep(1.2)
            body = b"{}"
        else:
            body = b'{"workerId":"pc-1","build":16}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)


class PcLinkTests(unittest.TestCase):
    def test_heartbeat_stays_ahead_of_a_slow_job_call(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _LaneHandler)
        server.slow_started = threading.Event()  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        previous = os.environ.get("HTTP_PROXY")
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
        os.environ["http_proxy"] = "http://127.0.0.1:1"
        client = HubClient(f"http://127.0.0.1:{port}", "token")
        try:
            held: list[BaseException] = []

            def slow() -> None:
                try:
                    client._work.call("POST", "/slow", {"workerId": "pc-1"}, 5)
                except BaseException as error:  # noqa: BLE001 - giữ lỗi của luồng phụ
                    held.append(error)

            worker = threading.Thread(target=slow)
            worker.start()
            self.assertTrue(server.slow_started.wait(2))  # type: ignore[attr-defined]
            started = time.monotonic()
            worker_id, build = client.heartbeat("pc-1", "Nha", 8)
            elapsed = time.monotonic() - started
            worker.join(3)
        finally:
            client._beat.close()
            client._work.close()
            if previous is None:
                os.environ.pop("HTTP_PROXY", None)
                os.environ.pop("http_proxy", None)
            else:
                os.environ["HTTP_PROXY"] = previous
                os.environ["http_proxy"] = previous
            server.shutdown()
            server.server_close()
        self.assertEqual(held, [])
        self.assertEqual(worker_id, "pc-1")
        self.assertEqual(build, 16)
        self.assertLess(elapsed, 0.9)

    def test_a_short_blip_does_not_drop_the_pc(self) -> None:
        book = HelperBook()
        worker_id = book.beat("pc-1", "Nha", 8)
        book._items[worker_id].seen = time.monotonic() - 20
        self.assertTrue(book.fresh(worker_id))
        book._items[worker_id].seen = time.monotonic() - (LEASE_SECONDS - 5)
        self.assertFalse(book.fresh(worker_id))
        self.assertTrue(book.note(worker_id))
        self.assertTrue(book.fresh(worker_id))
        book._items[worker_id].seen = time.monotonic() - (LEASE_SECONDS + 5)
        self.assertFalse(book.note(worker_id))
        self.assertFalse(book.fresh("khong-co"))
        self.assertFalse(book.note("khong-co"))

    def test_hub_restart_still_knows_the_pc(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "video_workers.json"
            first = HelperBook(path)
            worker_id = first.beat("pc-1", "Nha", 20, workers=16)
            second = HelperBook(path)
            self.assertTrue(second.fresh(worker_id))
            self.assertEqual(second.public()["name"], "Nha")
            self.assertEqual(second.public()["cpus"], 20)
            path.write_text(
                json.dumps([{"workerId": "pc-cu", "name": "Cu", "cpus": 4, "seenWall": time.time() - 120}]),
                encoding="utf-8",
            )
            third = HelperBook(path)
            self.assertFalse(third.fresh("pc-cu"))


if __name__ == "__main__":
    unittest.main()
