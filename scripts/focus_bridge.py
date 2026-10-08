#!/usr/bin/env python3
"""Cầu iPhone: hiện tên Focus: quét lik trên phần mềm đang chạy ở hub."""

from __future__ import annotations

import http.client
import http.server
import socketserver
from urllib.parse import urlsplit

from control_plane.focus import FOCUS_ENVIRONMENT_ID, FOCUS_LIVE_HUB, FOCUS_NAME

HOST = "0.0.0.0"
PORT = 8765
UPSTREAM = urlsplit(FOCUS_LIVE_HUB)
BANNER = (
    f'<p class="brand">{FOCUS_NAME}</p>'
    f'<p style="margin:0 0 0.45rem;font-size:13px;color:#78716c;">'
    f"Môi trường {FOCUS_ENVIRONMENT_ID}</p>"
)


def _rewrite(body: bytes, content_type: str) -> bytes:
    if "text/html" not in content_type and "manifest" not in content_type:
        return body
    text = body.decode("utf-8", errors="ignore")
    text = text.replace("<title>fb-poller</title>", f"<title>{FOCUS_NAME}</title>")
    text = text.replace('content="fb-poller"', f'content="{FOCUS_NAME}"')
    text = text.replace('<p class="brand">fb-poller</p>', BANNER)
    text = text.replace('"name": "fb-poller"', f'"name": "{FOCUS_NAME}"')
    text = text.replace('"short_name": "fb-poller"', f'"short_name": "{FOCUS_NAME}"')
    return text.encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        payload = self.rfile.read(length) if length else None
        conn = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=30)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "accept-encoding"}
        }
        headers["Host"] = UPSTREAM.netloc
        headers["Accept-Encoding"] = "identity"
        try:
            conn.request(self.command, self.path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        finally:
            conn.close()
        content_type = response.getheader("Content-Type", "")
        body = _rewrite(raw, content_type)
        skip = {"transfer-encoding", "content-length", "connection"}
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in skip:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    with Server((HOST, PORT), Handler) as httpd:
        print(f"Focus bridge http://127.0.0.1:{PORT} -> {FOCUS_LIVE_HUB}", flush=True)
        httpd.serve_forever()
