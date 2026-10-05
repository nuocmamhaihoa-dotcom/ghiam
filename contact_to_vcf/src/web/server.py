"""Browser app for the phone pool. One page, phone or computer."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import zipfile
from urllib.parse import quote, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parsers.column_suggest import suggest_columns
from parsers.detect import detect_format, inspect_source, prepare_source
from processors.pool import (
    CONTACTS_PER_FILE,
    ImportStats,
    export_book,
    import_file,
    list_books,
    pool_total,
)

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
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self.close_connection = True
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._bytes(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if path.startswith("/") and path.endswith(".vcf") and "/" not in path[1:]:
                file_path = PAGE.parent / path[1:]
                if file_path.is_file():
                    self._vcf(file_path.read_bytes(), file_path.name)
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
            self._json(200, _import_payload(app.kho, stats))

        def _import_now(self) -> None:
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
                _name_index, phone_index = suggest_columns(info.columns, info.samples)
                per_file = _per_file_value(form.get("per_file"))
                stats = import_file(
                    app.kho,
                    ready,
                    file_format=info.file_format,
                    delimiter=info.delimiter or ",",
                    has_header=info.has_header,
                    encoding=info.encoding or "utf-8-sig",
                    phone_column=phone_index,
                    contacts_per_file=per_file,
                )
            except (OSError, ValueError) as exc:
                target.unlink(missing_ok=True)
                self._json(400, {"error": str(exc)})
                return
            payload_json = _import_payload(app.kho, stats)
            payload_json["upload_id"] = target.name
            payload_json["phone_column"] = phone_index
            payload_json["columns"] = info.columns
            payload_json["samples"] = [list(row) for row in info.samples]
            payload_json["has_header"] = info.has_header
            payload_json["delimiter"] = info.delimiter
            self._json(200, payload_json)

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

        def _iphone_page(self) -> None:
            books = list_books(app.kho, "all")
            key = quote(self._cookie_token(), safe="")
            links = []
            for book in books:
                links.append(
                    f'<a class="go" href="/iphone/{book.id}.vcf?key={key}">'
                    f"Tải {book.file_name} ({book.contact_count} số)</a>"
                )
            body = (
                "<!DOCTYPE html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                "<title>Tải trên iPhone</title><style>"
                "body{font-family:system-ui,sans-serif;margin:0;background:#f4f1ea;color:#1c1917}"
                ".wrap{max-width:520px;margin:0 auto;padding:20px}"
                "a.go{display:flex;align-items:center;justify-content:center;min-height:52px;"
                "margin:0 0 12px;border-radius:14px;background:#0f766e;color:#fff;"
                "text-decoration:none;font-weight:650}"
                "p{color:#78716c;line-height:1.45}"
                "</style></head><body><div class=\"wrap\">"
                "<h1>Tải trên iPhone</h1>"
                "<p>Bấm một file. Safari hiện mũi tên tải xuống ở phía trên. Bấm mũi tên đó để lưu tệp vào iPhone.</p>"
                + ("".join(links) if links else "<p>Chưa có danh bạ.</p>")
                + "<p><a href=\"/\">Quay lại</a></p></div></body></html>"
            )
            self._bytes(200, body.encode("utf-8"), "text/html; charset=utf-8")

        def _download(self, book_id: int, for_iphone: bool = False) -> None:
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
            self._vcf(payload, f"danhba_{book_id:05d}.vcf", for_iphone=for_iphone)

        def _cookie_token(self) -> str:
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "session":
                    return value
            return ""

        def _auth(self) -> bool:
            query_key = parse_qs(urlparse(self.path).query).get("key", [""])[0]
            if app.allowed(query_key):
                return True
            return app.allowed(self._cookie_token())

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


def _import_payload(kho: Path, stats: ImportStats) -> dict[str, object]:
    return {
        "added": stats.added,
        "duplicate": stats.duplicate,
        "rejected": stats.rejected,
        "seen": stats.seen,
        "total": pool_total(kho),
        "pending": [book.id for book in list_books(kho, "pending")],
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


def _per_file_value(field: tuple[str, bytes] | None) -> int:
    if field is None or not field[1].strip():
        return CONTACTS_PER_FILE
    text = field[1].decode("utf-8", "replace").strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text) or re.fullmatch(r"\d{1,3}(?:,\d{3})+", text):
        text = text.replace(".", "").replace(",", "")
    if not text.isdigit():
        raise ValueError("Số điện thoại mỗi danh bạ phải là số nguyên")
    value = int(text)
    if value < 1 or value > 1_000_000:
        raise ValueError("Số điện thoại mỗi danh bạ phải từ 1 đến 1.000.000")
    return value


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
