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
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from ads_library.meta_client import InputError, MetaApiError, lookup, redact
from ads_library.page_ref import PageRefError, parse_page_ref

STATIC_DIR = Path(__file__).resolve().parent / "static"
_MAX_BODY = 8_000
_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def site_path(raw: str) -> str:
    path = raw.split("?", 1)[0]
    if path.startswith(("http://", "https://")):
        path = urlparse(path).path or "/"
    path = unquote(path)
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if path == "/index.html":
        return "/"
    return path


def urllib_fetch(url: str, timeout: float) -> tuple[int, str]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ads-library-viewer/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise MetaApiError("Không kết nối được tới Meta. Kiểm tra mạng.") from exc


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InputError("Bộ lọc không hợp lệ.")
    return value


def search_request(body: dict[str, Any], token: str) -> dict[str, Any]:
    url = _text(body, "url").strip()
    keyword = _text(body, "keyword")
    match = _text(body, "match") or "any"
    country = _text(body, "country") or "VN"
    status = _text(body, "status") or "ACTIVE"
    limit = body.get("limit") if body.get("limit") is not None else 50
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InputError("Số quảng cáo phải từ 1 đến 100.")
    ref = parse_page_ref(url) if url else None
    result = lookup(
        ref,
        keyword=keyword,
        match=match,
        country=country.upper(),
        status=status.upper(),
        limit=limit,
        token=token,
        fetch=urllib_fetch,
    )
    page = None
    if result.page is not None:
        page = {"id": result.page.id, "name": result.page.name, "link": result.page.link}
    return {"page": page, "keyword": result.keyword, "ads": result.ads, "count": len(result.ads)}


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

    def _redirect_home(self) -> None:
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = site_path(self.path)
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/ready":
            self._json(200, {"configured": bool(os.environ.get("META_ACCESS_TOKEN", "").strip())})
            return
        item = _FILES.get(path)
        if item is None:
            if path.startswith("/api/"):
                self._json(404, {"error": "Không có trang này."})
                return
            self._redirect_home()
            return
        name, content_type = item
        file_path = STATIC_DIR / name
        if not file_path.is_file():
            self._json(404, {"error": "Thiếu tệp giao diện."})
            return
        self._bytes(200, file_path.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        if site_path(self.path) != "/api/search":
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
        except (PageRefError, InputError) as exc:
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
