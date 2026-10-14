"""Máy chủ cục bộ cho trình xem quảng cáo Fanpage.

Chạy:
  META_ACCESS_TOKEN=... python -m ads_library
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ads_library.meta_client import MetaApiError, lookup, redact
from ads_library.page_ref import PageRefError, parse_page_ref

STATIC_DIR = Path(__file__).resolve().parent / "static"
_MAX_BODY = 8_000
_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def urllib_fetch(url: str, timeout: float) -> tuple[int, str]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ads-library-viewer/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise MetaApiError("Không kết nối được tới Meta. Kiểm tra mạng.") from exc


def search_request(body: dict[str, Any], token: str) -> dict[str, Any]:
    url = body.get("url")
    if not isinstance(url, str):
        raise PageRefError("Nhập link Fanpage.")
    country = body.get("country") or "VN"
    status = body.get("status") or "ACTIVE"
    limit = body.get("limit") if body.get("limit") is not None else 50
    if not isinstance(country, str) or not isinstance(status, str):
        raise MetaApiError("Bộ lọc không hợp lệ.")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise MetaApiError("Số quảng cáo phải từ 1 đến 100.")
    ref = parse_page_ref(url)
    result = lookup(
        ref,
        country=country.upper(),
        status=status.upper(),
        limit=limit,
        token=token,
        fetch=urllib_fetch,
    )
    return {
        "page": {"id": result.page.id, "name": result.page.name, "link": result.page.link},
        "ads": result.ads,
        "count": len(result.ads),
    }


class AdsLibraryHandler(BaseHTTPRequestHandler):
    server_version = "AdsLibrary/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        message = redact(format % args)
        print(f"[{self.log_date_time_string()}] {message}")

    def _bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/ready":
            self._json(200, {"configured": bool(os.environ.get("META_ACCESS_TOKEN", "").strip())})
            return
        item = _FILES.get(path)
        if item is None:
            self._json(404, {"error": "Không có trang này."})
            return
        name, content_type = item
        file_path = STATIC_DIR / name
        if not file_path.is_file():
            self._json(404, {"error": "Thiếu tệp giao diện."})
            return
        self._bytes(200, file_path.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/search":
            self._json(404, {"error": "Không có trang này."})
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length < 1 or length > _MAX_BODY:
            self._json(413, {"error": "Yêu cầu quá lớn."})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "Dữ liệu gửi lên không đọc được."})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "Dữ liệu gửi lên không đọc được."})
            return
        token = os.environ.get("META_ACCESS_TOKEN", "")
        try:
            self._json(200, search_request(body, token))
        except PageRefError as exc:
            self._json(400, {"error": str(exc)})
        except MetaApiError as exc:
            status = 503 if "META_ACCESS_TOKEN" in str(exc) else 502
            self._json(status, {"error": str(exc)})


def main() -> None:
    host = os.environ.get("ADS_LIBRARY_HOST", "127.0.0.1")
    port = int(os.environ.get("ADS_LIBRARY_PORT", "8090"))
    httpd = ThreadingHTTPServer((host, port), AdsLibraryHandler)
    print(f"Thư viện quảng cáo đang mở tại http://{host}:{port}")
    if not os.environ.get("META_ACCESS_TOKEN", "").strip():
        print("Chưa có META_ACCESS_TOKEN. Tìm kiếm sẽ báo lỗi cho đến khi biến này được đặt.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
