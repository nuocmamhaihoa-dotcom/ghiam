"""Browser app for the phone pool. One page, phone or computer."""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parsers.column_suggest import suggest_columns
from parsers.detect import detect_format, inspect_source
from processors.pool import export_book, import_file, list_books, pool_total

PAGE = Path(__file__).with_name("index.html")
USER = os.environ.get("DANHBA_USER", "danhba")
PASSWORD = os.environ.get("DANHBA_PASSWORD", "danhba123")


class PoolApp:
    def __init__(self, kho: Path) -> None:
        self.kho = kho
        self.kho.mkdir(parents=True, exist_ok=True)
        self.uploads = self.kho / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.sessions: set[str] = set()
        self.lock = threading.Lock()

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
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._bytes(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if path.startswith("/") and path.endswith(".vcf") and "/" not in path[1:]:
                file_path = PAGE.parent / path[1:]
                if file_path.is_file():
                    payload = file_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/vcard; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="{file_path.name}"',
                    )
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
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
            if path.startswith("/api/books/") and path.endswith("/download"):
                book_id = _book_id(path)
                if book_id is None:
                    self._json(404, {"error": "Không thấy danh bạ"})
                    return
                self._download(book_id)
                return
            self._json(404, {"error": "Không thấy trang"})

        def do_POST(self) -> None:  # noqa: N802
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
                body = json.dumps({"ok": True}).encode()
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
            self._json(404, {"error": "Không thấy trang"})

        def _inspect(self) -> None:
            form = _read_form(self)
            file_item = form.get("file")
            if file_item is None or not file_item[1]:
                self._json(400, {"error": "Hãy chọn file"})
                return
            filename, payload = file_item
            suffix = Path(filename).suffix.lower()
            if suffix not in {".csv", ".xlsx", ".txt"}:
                self._json(400, {"error": "Chỉ nhận CSV, XLSX hoặc TXT"})
                return
            target = app.uploads / f"{secrets.token_hex(8)}{suffix}"
            target.write_bytes(payload)
            try:
                detected = detect_format(target)
                info = inspect_source(target, file_format=detected)
            except (OSError, ValueError) as exc:
                target.unlink(missing_ok=True)
                self._json(400, {"error": str(exc)})
                return
            name_index, phone_index = suggest_columns(info.columns, info.samples)
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
            stats = import_file(
                app.kho,
                source,
                file_format=file_format,
                delimiter=delimiter or ",",
                has_header=has_header,
                encoding="utf-8-sig",
                phone_column=phone_column,
            )
            self._json(
                200,
                {
                    "added": stats.added,
                    "duplicate": stats.duplicate,
                    "rejected": stats.rejected,
                    "seen": stats.seen,
                    "total": pool_total(app.kho),
                },
            )

        def _download_pending(self) -> None:
            pending = list_books(app.kho, "pending")
            if not pending:
                self._json(404, {"error": "Không còn danh bạ chưa tải"})
                return
            temporary = Path(tempfile.mkdtemp(prefix="danhba-zip-"))
            try:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for book in pending:
                        destination = temporary / book.file_name
                        export_book(app.kho, book.id, destination)
                        archive.write(destination, book.file_name)
                payload = buffer.getvalue()
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="danh-ba-chua-tai.zip"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _download(self, book_id: int) -> None:
            temporary = Path(tempfile.mkdtemp(prefix="danhba-"))
            try:
                destination = temporary / f"danhba_{book_id:05d}.vcf"
                try:
                    export_book(app.kho, book_id, destination)
                except ValueError as exc:
                    self._json(404, {"error": str(exc)})
                    return
                payload = destination.read_bytes()
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/vcard; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="danhba_{book_id:05d}.vcf"',
            )
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _auth(self) -> bool:
            token = ""
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "session":
                    token = value
            return app.allowed(token)

        def _json_body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            loaded = json.loads(raw.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded
            return {}

        def _json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._bytes(status, body, "application/json; charset=utf-8")

        def _bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return Handler


def _book_id(path: str) -> int | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "books" or parts[3] != "download":
        return None
    if not parts[2].isdigit():
        return None
    return int(parts[2])


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
        name = ""
        filename = ""
        for item in header_text.split(";"):
            item = item.strip()
            if item.startswith("name="):
                name = item.split("=", 1)[1].strip('"')
            elif item.startswith("filename="):
                filename = item.split("=", 1)[1].strip('"')
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
