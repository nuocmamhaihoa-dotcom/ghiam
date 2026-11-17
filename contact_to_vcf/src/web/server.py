"""Browser app for the phone pool. One page, phone or computer."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import zipfile
from urllib.parse import quote, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parsers.column_suggest import phone_column_for_import, suggest_columns
from parsers.detect import detect_encoding, detect_format, inspect_source, prepare_source
from processors.pool import (
    CONTACTS_PER_FILE,
    ImportStats,
    BookInfo,
    import_file,
    list_books,
    mark_books_downloaded,
    pool_total,
    read_book_vcf,
    write_assigned_catalog,
    write_book_catalog,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_SKIP_PARTS = {"__pycache__", ".pytest_cache", ".venv", "uploads", "exports"}

MAX_UPLOAD_BYTES = 512 * 1024 * 1024

PAGE = Path(__file__).with_name("index.html")
USER = os.environ.get("DANHBA_USER", "danhba")
PASSWORD = os.environ.get("DANHBA_PASSWORD", "danhba123")


class PoolApp:
    def __init__(self, kho: Path) -> None:
        self.kho = kho
        self.kho.mkdir(parents=True, exist_ok=True)
        self.uploads = self.kho / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        _purge_old_uploads(self.uploads)
        self.sessions: set[str] = set()
        self.lock = threading.Lock()
        self.export_lock = threading.Lock()
        self.software_zip: bytes | None = None
        self.download_key = _load_download_key(self.kho)

    def login(self, user: str, password: str) -> str | None:
        if user != USER or password != PASSWORD:
            return None
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.sessions.add(token)
        return token

    def allowed(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            return token in self.sessions


def serve(kho: Path, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    app = PoolApp(kho)
    handler = _handler(app)
    server = ThreadingHTTPServer((host, port), handler)
    server.app = app  # type: ignore[attr-defined]
    return server


def _handler(app: PoolApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self.close_connection = True
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._bytes(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/api/kho":
                books = list_books(app.kho, "all")
                self._json(200, {"total": pool_total(app.kho), "books": len(books)})
                return
            if path.startswith("/") and path.endswith(".vcf") and "/" not in path[1:]:
                file_path = PAGE.parent / path[1:]
                if file_path.is_file():
                    self._vcf(file_path.read_bytes(), file_path.name)
                    return
            if path == "/tai" or path.startswith("/tai/"):
                if not self._auth() and not self._download_key_ok():
                    self._json(401, {"error": "Chưa đăng nhập"})
                    return
                self._serve_download(path)
                return
            if not self._auth():
                self._json(401, {"error": "Chưa đăng nhập"})
                return
            if path == "/api/books":
                status = parse_qs(urlparse(self.path).query).get("status", ["all"])[0]
                books = list_books(app.kho, status if status in {"all", "pending", "downloaded"} else "all")
                self._json(
                    200,
                    {
                        "total": pool_total(app.kho),
                        "key": self._cookie_token(),
                        "books": [
                            {
                                "id": book.id,
                                "name": book.file_name,
                                "count": book.contact_count,
                                "downloaded_at": book.downloaded_at,
                            }
                            for book in books
                        ],
                    },
                )
                return
            if path == "/api/download-pending":
                self._download_pending()
                return
            if path == "/api/download-filled":
                self._download_filled()
                return
            if path == "/iphone":
                self._iphone_page()
                return
            iphone_id = _iphone_book_id(path)
            if iphone_id is not None:
                self._download(iphone_id, for_iphone=True)
                return
            if path.startswith("/api/books/") and path.endswith("/download"):
                book_id = _book_id(path)
                if book_id is None:
                    self._json(404, {"error": "Không thấy danh bạ"})
                    return
                self._download(book_id)
                return
            self._json(404, {"error": "Không thấy trang"})

        def do_POST(self) -> None:  # noqa: N802
            self.close_connection = True
            path = urlparse(self.path).path
            if path == "/api/login":
                data = self._json_body()
                token = app.login(str(data.get("user", "")), str(data.get("password", "")))
                if token is None:
                    self._json(401, {"error": "Sai tài khoản hoặc mật khẩu"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Lax")
                body = json.dumps({"ok": True, "token": token}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not self._auth():
                self._json(401, {"error": "Chưa đăng nhập"})
                return
            if path == "/api/inspect":
                self._inspect()
                return
            if path == "/api/import":
                self._import()
                return
            if path == "/api/import-now":
                self._import_now()
                return
            self._json(404, {"error": "Không thấy trang"})

        def _inspect(self) -> None:
            if self._too_large():
                return
            form = _read_form(self)
            file_item = form.get("file")
            if file_item is None or not file_item[1]:
                self._json(400, {"error": "Hãy chọn file"})
                return
            filename, payload = file_item
            target = app.uploads / f"{secrets.token_hex(8)}{_safe_suffix(filename)}"
            target.write_bytes(payload)
            try:
                ready = prepare_source(target)
                detected = detect_format(ready)
                info = inspect_source(ready, file_format=detected)
            except (OSError, ValueError) as exc:
                target.unlink(missing_ok=True)
                self._json(400, {"error": _import_error(exc)})
                return
            name_index, _suggested = suggest_columns(info.columns, info.samples)
            phone_index = phone_column_for_import(info.file_format, info.columns, info.samples)
            self._json(
                200,
                {
                    "upload_id": target.name,
                    "format": info.file_format,
                    "delimiter": info.delimiter,
                    "has_header": info.has_header,
                    "columns": info.columns,
                    "samples": [list(row) for row in info.samples],
                    "rows": info.estimated_rows,
                    "phone_column": phone_index,
                    "name_column": name_index,
                },
            )

        def _import(self) -> None:
            data = self._json_body()
            upload_name = Path(str(data.get("upload_id", ""))).name
            source = app.uploads / upload_name
            if not source.is_file():
                self._json(400, {"error": "File vừa chọn không còn. Hãy chọn lại."})
                return
            try:
                phone_column = int(data["phone_column"])
                has_header = bool(data.get("has_header", True))
                delimiter = str(data.get("delimiter") or ",")
                file_format = detect_format(source)
            except (KeyError, TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            if file_format == "xlsx":
                delimiter = ""
            encoding = "utf-8-sig" if file_format == "xlsx" else detect_encoding(source)
            try:
                stats = import_file(
                    app.kho,
                    source,
                    file_format=file_format,
                    delimiter=delimiter or ",",
                    has_header=has_header,
                    encoding=encoding,
                    phone_column=phone_column,
                    contacts_per_file=CONTACTS_PER_FILE,
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                self._json(400, {"error": _import_error(exc)})
                return
            except Exception as exc:
                self._json(400, {"error": _import_error(exc)})
                return
            _delete_upload(source)
            self._json(200, _import_payload(app.kho, stats))

        def _import_now(self) -> None:
            if self._too_large():
                return
            form = _read_form(self)
            file_item = form.get("file")
            if file_item is None or not file_item[1]:
                self._json(400, {"error": "Hãy chọn file"})
                return
            filename, payload = file_item
            target = app.uploads / f"{secrets.token_hex(8)}{_safe_suffix(filename)}"
            target.write_bytes(payload)
            try:
                ready = prepare_source(target)
                detected = detect_format(ready)
                info = inspect_source(ready, file_format=detected)
                phone_index = phone_column_for_import(
                    info.file_format, info.columns, info.samples
                )
                stats = import_file(
                    app.kho,
                    ready,
                    file_format=info.file_format,
                    delimiter=info.delimiter or ",",
                    has_header=info.has_header,
                    encoding=info.encoding or "utf-8-sig",
                    phone_column=phone_index,
                    contacts_per_file=CONTACTS_PER_FILE,
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                _delete_upload(target)
                self._json(400, {"error": _import_error(exc)})
                return
            except Exception as exc:
                _delete_upload(target)
                self._json(400, {"error": _import_error(exc)})
                return
            _delete_upload(target)
            payload_json = _import_payload(app.kho, stats)
            payload_json["upload_id"] = target.name
            payload_json["phone_column"] = phone_index
            payload_json["columns"] = info.columns
            payload_json["samples"] = [list(row) for row in info.samples]
            payload_json["has_header"] = info.has_header
            payload_json["delimiter"] = info.delimiter
            self._json(200, payload_json)

        def _serve_download(self, path: str) -> None:
            if path == "/tai":
                self._download_page()
                return
            if path == "/tai/phan-mem.zip":
                self._download_software()
                return
            if path == "/tai/danh-ba-pc.zip":
                self._download_pc_bundle()
                return
            if path == "/tai/DanhBa-Setup.exe":
                self._download_pc_installer()
                return
            if path == "/tai/danh-sach-so.csv":
                self._download_numbers()
                return
            if path == "/tai/danh-sach-danh-ba.csv":
                self._download_book_list()
                return
            self._json(404, {"error": "Không thấy trang"})

        def _download_page(self) -> None:
            key = quote(app.download_key, safe="")
            host = self.headers.get("Host", "127.0.0.1")
            installer = f"http://{host}/tai/DanhBa-Setup.exe?key={key}"
            total = pool_total(app.kho)
            books = list_books(app.kho, "all")
            body = (
                "<!DOCTYPE html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                "<title>Tải phần mềm</title><style>"
                "body{font-family:system-ui,sans-serif;margin:0;background:#f4f1ea;color:#1c1917}"
                ".wrap{max-width:560px;margin:0 auto;padding:20px}"
                "a.go{display:flex;align-items:center;justify-content:center;min-height:52px;"
                "margin:0 0 12px;border-radius:14px;background:#0f766e;color:#fff;"
                "text-decoration:none;font-weight:650;text-align:center;padding:8px 12px}"
                "code{display:block;overflow-wrap:anywhere;background:#fff;border-radius:12px;"
                "padding:12px;margin:0 0 12px}"
                "p{color:#78716c;line-height:1.45}"
                "</style></head><body><div class=\"wrap\">"
                "<h1>Tải phần mềm và danh sách số</h1>"
                f"<p>Kho đang có {total} số, đã chia vào {len(books)} danh bạ. "
                "Mỗi danh bạ mới đủ 4.000 số. Số đã chia không sang danh bạ khác.</p>"
                f'<a class="go" href="{installer}">Tải file cài đặt DanhBa-Setup.exe</a>'
                f"<p>Link tải file cài đặt:</p><code>{installer}</code>"
                f'<a class="go" href="/tai/danh-ba-pc.zip?key={key}">Tải bản máy tính kèm kho số đã làm</a>'
                f'<a class="go" href="/tai/phan-mem.zip?key={key}">Tải mã phần mềm, không kèm kho</a>'
                f'<a class="go" href="/tai/danh-sach-so.csv?key={key}">Tải mọi số đã chia vào danh bạ</a>'
                f'<a class="go" href="/tai/danh-sach-danh-ba.csv?key={key}">Tải danh sách từng danh bạ</a>'
                "<p>File số gồm cột so và danh_ba. Tải danh sách này không khóa danh bạ "
                "và không chuyển số sang file khác. Lần đầu với kho lớn có thể mất vài phút. "
                "File lớn có thể tải tiếp nếu mạng bị đứt.</p>"
                '<p><a href="/">Quay lại</a></p></div></body></html>'
            )
            self._bytes(200, body.encode("utf-8"), "text/html; charset=utf-8")

        def _download_pc_installer(self) -> None:
            path = app.kho / "exports" / "DanhBa-Setup.exe"
            if not path.is_file():
                self._json(404, {"error": "File cài đặt chưa được đóng gói."})
                return
            self._send_path(path, "application/octet-stream", "DanhBa-Setup.exe")

        def _download_pc_bundle(self) -> None:
            path = app.kho / "exports" / "danh-ba-pc.zip"
            if not path.is_file():
                self._json(404, {"error": "Bản máy tính chưa được đóng gói."})
                return
            self._send_path(path, "application/zip", "danh-ba-pc.zip")

        def _download_software(self) -> None:
            payload = _software_zip(app)
            self._attachment(payload, "application/zip", "danh-ba-phan-mem.zip")

        def _download_numbers(self) -> None:
            path = _assigned_catalog(app)
            self._send_path(path, "text/csv; charset=utf-8", "danh-sach-so.csv")

        def _download_book_list(self) -> None:
            destination = app.kho / "exports" / "danh-sach-danh-ba.csv"
            with app.export_lock:
                write_book_catalog(app.kho, destination)
            self._send_path(destination, "text/csv; charset=utf-8", "danh-sach-danh-ba.csv")

        def _send_path(self, path: Path, content_type: str, filename: str) -> None:
            size = path.stat().st_size
            start, end, partial = _byte_range(self.headers.get("Range"), size)
            if start is None or end is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def _attachment(self, payload: bytes, content_type: str, filename: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _download_pending(self) -> None:
            pending = list_books(app.kho, "pending")
            if not pending:
                self._json(404, {"error": "Không còn danh bạ chưa tải"})
                return
            self._send_books(pending)

        def _download_filled(self) -> None:
            raw = parse_qs(urlparse(self.path).query).get("ids", [""])[0]
            wanted: list[int] = []
            for piece in raw.split(","):
                if piece.isdigit():
                    wanted.append(int(piece))
            if not wanted:
                self._json(404, {"error": "Không có danh bạ vừa đủ số"})
                return
            by_id = {book.id: book for book in list_books(app.kho, "all")}
            chosen = [by_id[book_id] for book_id in wanted if book_id in by_id]
            if not chosen:
                self._json(404, {"error": "Không thấy danh bạ vừa đủ số"})
                return
            self._send_books(chosen)

        def _send_books(self, books: list[BookInfo]) -> None:
            buffer = io.BytesIO()
            sent_ids: list[int] = []
            try:
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for book in books:
                        payload = read_book_vcf(app.kho, book.id)
                        archive.writestr(book.file_name, payload)
                        sent_ids.append(book.id)
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
                return
            body = buffer.getvalue()
            mark_books_downloaded(app.kho, sent_ids)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="danh-ba-chua-tai.zip"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _iphone_page(self) -> None:
            books = list_books(app.kho, "all")
            key = quote(self._cookie_token(), safe="")
            pending = [book for book in books if book.downloaded_at is None]
            downloaded = [book for book in books if book.downloaded_at is not None]

            def link(book: BookInfo) -> str:
                return (
                    f'<a class="go" href="/iphone/{book.id}.vcf?key={key}">'
                    f"Tải {book.file_name} ({book.contact_count} số)</a>"
                )

            sections = [
                "<h1>Tải trên iPhone</h1>",
                "<p>Bấm một file. Safari hiện mũi tên tải xuống ở phía trên. Bấm mũi tên đó để lưu tệp vào iPhone.</p>",
            ]
            if pending:
                sections.append("<h2>Chưa tải</h2>")
                sections.append(
                    "<p>Danh bạ mới ở đây. File đã đủ có 4.000 số. "
                    "File cuối cùng vẫn đang nhận số cho đến khi đủ 4.000.</p>"
                )
                sections.extend(link(book) for book in pending)
            if downloaded:
                sections.append("<h2>Đã tải</h2>")
                sections.append(
                    "<p>Các file này giữ đúng số lượng lúc tải. "
                    "File tải lúc chia 2.000 hoặc 1.000 số không được thêm số nữa.</p>"
                )
                sections.extend(link(book) for book in downloaded)
            if not books:
                sections.append("<p>Chưa có danh bạ.</p>")
            sections.append('<p><a href="/">Quay lại</a></p>')
            body = (
                "<!DOCTYPE html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                "<title>Tải trên iPhone</title><style>"
                "body{font-family:system-ui,sans-serif;margin:0;background:#f4f1ea;color:#1c1917}"
                ".wrap{max-width:520px;margin:0 auto;padding:20px}"
                "h2{font-size:1.1rem;margin:18px 0 8px}"
                "a.go{display:flex;align-items:center;justify-content:center;min-height:52px;"
                "margin:0 0 12px;border-radius:14px;background:#0f766e;color:#fff;"
                "text-decoration:none;font-weight:650}"
                "p{color:#78716c;line-height:1.45}"
                "</style></head><body><div class=\"wrap\">"
                + "".join(sections)
                + "</div></body></html>"
            )
            self._bytes(200, body.encode("utf-8"), "text/html; charset=utf-8")

        def _download(self, book_id: int, for_iphone: bool = False) -> None:
            try:
                payload = read_book_vcf(app.kho, book_id)
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
                return
            mark_books_downloaded(app.kho, [book_id])
            self._vcf(payload, f"danhba_{book_id:05d}.vcf", for_iphone=for_iphone)

        def _cookie_token(self) -> str:
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "session":
                    return value
            return ""

        def _query_key(self) -> str:
            return parse_qs(urlparse(self.path).query).get("key", [""])[0]

        def _download_key_ok(self) -> bool:
            query_key = self._query_key()
            if not query_key or not app.download_key:
                return False
            return secrets.compare_digest(query_key, app.download_key)

        def _auth(self) -> bool:
            if app.allowed(self._query_key()):
                return True
            return app.allowed(self._cookie_token())

        def _too_large(self) -> bool:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            if length > MAX_UPLOAD_BYTES:
                self._json(400, {"error": "File lớn hơn 512 MB. Hãy chia nhỏ file."})
                return True
            return False

        def _json_body(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                return {}
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                loaded = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                return {}
            if isinstance(loaded, dict):
                return loaded
            return {}

        def _json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._bytes(status, body, "application/json; charset=utf-8")

        def _vcf(self, payload: bytes, filename: str, for_iphone: bool = False) -> None:
            # iPhone saves a file when the type is not one Safari displays,
            # and the link itself carries the login key because iOS omits cookies on downloads.
            if for_iphone:
                content_type = "application/octet-stream"
                disposition = "attachment"
            else:
                content_type = "text/x-vcard"
                disposition = "inline"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'{disposition}; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return Handler


def _load_download_key(kho: Path) -> str:
    path = kho / "exports" / "download.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_urlsafe(24)
    path.write_text(key + "\n", encoding="utf-8")
    return key


def _byte_range(header: str | None, size: int) -> tuple[int | None, int | None, bool]:
    if size <= 0:
        return 0, -1, False
    if not header or not header.startswith("bytes="):
        return 0, size - 1, False
    spec = header.removeprefix("bytes=").split(",", 1)[0].strip()
    left, separator, right = spec.partition("-")
    if not separator:
        return None, None, False
    try:
        if left == "":
            length = int(right)
            if length <= 0:
                return None, None, False
            start = max(size - length, 0)
            end = size - 1
        else:
            start = int(left)
            end = int(right) if right else size - 1
    except ValueError:
        return None, None, False
    if start < 0 or start >= size or end < start:
        return None, None, False
    return start, min(end, size - 1), True


def _assigned_catalog(app: PoolApp) -> Path:
    destination = app.kho / "exports" / "danh-sach-so.csv"
    marker = destination.with_suffix(".count")
    with app.export_lock:
        total = pool_total(app.kho)
        if destination.is_file() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == str(total):
            return destination
        write_assigned_catalog(app.kho, destination)
        marker.write_text(str(total), encoding="utf-8")
        return destination


def _software_zip(app: PoolApp) -> bytes:
    if app.software_zip is not None:
        return app.software_zip
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("contact_to_vcf/TINH_NANG.txt", _feature_note())
        for path in PACKAGE_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            if path.suffix in {".sqlite", ".pyc"}:
                continue
            relative = path.relative_to(PACKAGE_ROOT)
            archive.write(path, f"contact_to_vcf/{relative.as_posix()}")
    app.software_zip = buffer.getvalue()
    return app.software_zip


def _feature_note() -> str:
    return """PHẦN MỀM DANH BẠ
=================

Tính năng đang dùng
- Nạp nhiều file một lúc, không giới hạn số file.
- Đọc CSV, Excel, TXT, UTF-16 và file có chứa số điện thoại.
- File Excel lấy số ở cột B và đưa về 10 số nhà mạng Việt Nam.
- Tên liên hệ ghi đúng bằng số điện thoại đó.
- Mỗi danh bạ mới chia đúng 4.000 số.
- Số đã chia vào danh bạ nào thì giữ nguyên danh bạ đó.
- Các cách viết 090…, 84…, +84… và đầu số 11 số cũ đều bị lọc trùng.
- Tải từng danh bạ trên máy tính và trên iPhone.
- Bản tải về máy tính chạy độc lập, không cần VPS, và mang theo kho số đã chia.

Danh sách số đã chia
- Mở trang web, vào «Tải phần mềm và danh sách số».
- File danh-sach-so.csv có cột so và danh_ba.
- File danh-sach-danh-ba.csv có từng danh bạ, số lượng và trạng thái đã tải hay chưa.
- Tải hai file này không khóa danh bạ và không chuyển số sang danh bạ khác.

Kho số nằm trên máy chủ. Gói phần mềm này là chương trình. Danh sách số tải riêng từ kho đang chạy.
"""


def _import_payload(kho: Path, stats: ImportStats) -> dict[str, object]:
    return {
        "added": stats.added,
        "duplicate": stats.duplicate,
        "rejected": stats.rejected,
        "seen": stats.seen,
        "total": pool_total(kho),
        "pending": [book.id for book in list_books(kho, "pending")],
        "filled": list(stats.filled),
    }


def _iphone_book_id(path: str) -> int | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or parts[0] != "iphone" or not parts[1].endswith(".vcf"):
        return None
    number = parts[1][:-4]
    if not number.isdigit():
        return None
    return int(number)


def _book_id(path: str) -> int | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "books" or parts[3] != "download":
        return None
    if not parts[2].isdigit():
        return None
    return int(parts[2])


def _import_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return "Kho đang bận vì một file khác. Hãy đợi file đó nạp xong rồi thử lại."
    message = str(exc).strip()
    return message or "Không đọc được file"


def _delete_upload(path: Path) -> None:
    path.unlink(missing_ok=True)
    sidecar = path.with_name(path.name + ".phones.txt")
    sidecar.unlink(missing_ok=True)


def _purge_old_uploads(folder: Path) -> None:
    cutoff = time.time() - 24 * 60 * 60
    for child in folder.iterdir():
        try:
            if child.is_file() and child.stat().st_mtime < cutoff:
                child.unlink()
        except OSError:
            continue


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower().strip()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return ".txt"


def _content_filename(header_text: str) -> str:
    """Read the uploaded name, including the UTF-8 form some browsers send alone."""
    star = re.search(r"filename\*\s*=\s*([^;\r\n]+)", header_text, re.IGNORECASE)
    if star:
        raw = star.group(1).strip().strip('"')
        if "''" in raw:
            raw = raw.split("''", 1)[1]
        name = unquote(raw).strip().strip('"')
        if name:
            return Path(name.replace("\\", "/")).name
    plain = re.search(
        r"filename\s*=\s*(?:\"([^\"]*)\"|([^;\r\n]+))",
        header_text,
        re.IGNORECASE,
    )
    if plain:
        name = (plain.group(1) if plain.group(1) is not None else plain.group(2) or "").strip()
        name = name.strip('"').strip()
        if name:
            return Path(name.replace("\\", "/")).name
    return "upload.txt"


def _read_form(handler: BaseHTTPRequestHandler) -> dict[str, tuple[str, bytes]]:
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", "0") or "0")
    body = handler.rfile.read(length) if length else b""
    marker = ""
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.startswith("boundary="):
            marker = piece.split("=", 1)[1].strip('"')
    if not marker:
        return {}
    result: dict[str, tuple[str, bytes]] = {}
    for chunk in body.split(b"--" + marker.encode()):
        if b"Content-Disposition" not in chunk:
            continue
        header_blob, _, data = chunk.partition(b"\r\n\r\n")
        header_text = header_blob.decode("utf-8", "replace")
        name_match = re.search(r"\bname\s*=\s*(?:\"([^\"]*)\"|([^;\r\n]+))", header_text)
        name = ""
        if name_match:
            name = (name_match.group(1) if name_match.group(1) is not None else name_match.group(2) or "")
            name = name.strip().strip('"')
        filename = _content_filename(header_text)
        if data.endswith(b"\r\n"):
            data = data[:-2]
        if name:
            result[name] = (filename, data)
    return result


def main() -> None:
    kho = Path(os.environ.get("DANHBA_KHO", "/var/lib/danhba"))
    port = int(os.environ.get("DANHBA_PORT", "80"))
    server = serve(kho, port=port)
    server.serve_forever()


if __name__ == "__main__":
    main()
