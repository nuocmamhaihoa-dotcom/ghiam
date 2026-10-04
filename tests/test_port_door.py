"""Cổng chuyển tiếp để mở IP không kèm cổng vẫn vào đúng hub."""

from __future__ import annotations

import socket
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from control_plane.port_door import health_ok, start_door


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class PortDoorTests(unittest.TestCase):
    def test_door_forwards_health(self) -> None:
        hub = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
        threading.Thread(target=hub.serve_forever, daemon=True).start()
        self.addCleanup(hub.shutdown)
        self.addCleanup(hub.server_close)
        door_sock = socket.socket()
        door_sock.bind(("127.0.0.1", 0))
        door_port = door_sock.getsockname()[1]
        door_sock.close()
        door = start_door(door_port, hub.server_address[1], host="127.0.0.1")
        self.assertIsNotNone(door)
        self.addCleanup(door.close)  # type: ignore[union-attr]
        with urllib.request.urlopen(f"http://127.0.0.1:{door_port}/health", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"ok", response.read())

    def test_same_port_is_skipped(self) -> None:
        self.assertIsNone(start_door(8088, 8088))
        self.assertIsNone(start_door(0, 8088))

    def test_health_ok_sees_a_closed_port(self) -> None:
        self.assertFalse(health_ok(1, timeout=0.2))


if __name__ == "__main__":
    unittest.main()
