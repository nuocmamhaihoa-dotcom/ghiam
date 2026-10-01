"""PC tải tiếp từ byte đã ghi khi hub đóng kết nối giữa chừng."""

from __future__ import annotations

import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pc_agent.video_worker as worker
from pc_agent.video_worker import HubClient


PAYLOAD = b"0123456789abcdef"


class _ShortHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        payload: bytes = self.server.payload  # type: ignore[attr-defined]
        start = 0
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            text = header.split("=", 1)[1].split("-", 1)[0]
            if text.isdigit():
                start = int(text)
        self.server.ranges.append(header)  # type: ignore[attr-defined]
        if self.server.drops and start == 0:  # type: ignore[attr-defined]
            self.server.drops -= 1  # type: ignore[attr-defined]
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload[:4])
            self.wfile.flush()
            return
        body = payload[start:]
        code = 206 if start else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if code == 206:
            end = len(payload) - 1
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class VideoTransferTest(unittest.TestCase):
    def test_download_resumes_after_the_connection_drops(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ShortHandler)
        server.payload = PAYLOAD  # type: ignore[attr-defined]
        server.drops = 1  # type: ignore[attr-defined]
        server.ranges = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as folder:
                dest = Path(folder) / "clip.mp4"
                HubClient(f"http://127.0.0.1:{port}", "token").download("job", "worker", dest)
                self.assertEqual(dest.read_bytes(), PAYLOAD)
        finally:
            server.shutdown()
            server.server_close()
        ranges = server.ranges  # type: ignore[attr-defined]
        self.assertGreaterEqual(len(ranges), 2)
        self.assertIn("bytes=4-", ranges[1])

    def test_large_download_uses_several_connections(self) -> None:
        payload = bytes((index * 17) % 251 for index in range(1024))
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        server.payload = payload  # type: ignore[attr-defined]
        server.ranges = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        previous = (worker._PARALLEL_MIN, worker._LANE_BYTES, worker._LANES)
        worker._PARALLEL_MIN = 64
        worker._LANE_BYTES = 128
        worker._LANES = 4
        try:
            with tempfile.TemporaryDirectory() as folder:
                dest = Path(folder) / "clip.mp4"
                HubClient(f"http://127.0.0.1:{port}", "token").download("job", "worker", dest)
                self.assertEqual(dest.read_bytes(), payload)
        finally:
            worker._PARALLEL_MIN, worker._LANE_BYTES, worker._LANES = previous
            server.shutdown()
            server.server_close()
        ranges = [item for item in server.ranges if item.startswith("bytes=")]  # type: ignore[attr-defined]
        self.assertGreaterEqual(len(ranges), 4)
        self.assertTrue(any(item.startswith("bytes=128-") for item in ranges))


class _RangeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        payload: bytes = self.server.payload  # type: ignore[attr-defined]
        header = self.headers.get("Range") or ""
        self.server.ranges.append(header)  # type: ignore[attr-defined]
        start = 0
        end = len(payload)
        if header.startswith("bytes="):
            spec = header.split("=", 1)[1]
            left, _, right = spec.partition("-")
            if left.isdigit():
                start = int(left)
            if right.isdigit():
                end = min(len(payload), int(right) + 1)
        body = payload[start:end]
        code = 206 if header.startswith("bytes=") else 200
        self.send_response(code)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{len(payload)}")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    unittest.main()
