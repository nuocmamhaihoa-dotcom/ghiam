"""Đưa kết quả ra ngoài: file Excel theo bộ lọc, đọc file nạp lại, gửi dòng mới tới webhook hoặc Google Sheets, khoá API chỉ đọc."""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import ipaddress
import json
import secrets
import socket
import ssl
import urllib.error
import urllib.parse
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from control_plane.phone_vn import phone_columns
from control_plane.video_store import (
    BUCKET_OK,
    IMPORT_ROW_LIMIT,
    READER_SQL,
    VN_TZ,
    ResultFilter,
    connect,
    get_meta,
    iter_results,
    set_meta,
    utcnow,
)
from control_plane.xlsx import workbook

EXPORT_HEADER = [
    "Số điện thoại",
    "Số chuẩn",
    "Nhà mạng gốc",
    "Tên",
    "Username",
    "Loại",
    "Lý do",
    "Video",
    "Điện thoại tải",
    "Đọc bởi",
    "Kiểm",
    "Đọc lúc (giờ VN)",
]
EXPORT_WIDTHS = [14, 13, 13, 28, 24, 14, 32, 8, 16, 14, 8, 18]
_CHECK = {"ok": "Khớp", "diff": "Lệch"}


def vn_time(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    return moment.astimezone(VN_TZ).strftime("%d/%m/%Y %H:%M")


def export_rows(db_path: Path, filters: ResultFilter) -> Iterator[list[object]]:
    for item in iter_results(db_path, filters):
        yield [
            item["phone"],
            item["normalized"],
            item["carrier"],
            item["name"],
            item["username"],
            item["bucket"],
            item["reason"],
            item["video_id"] or "",
            item["device"],
            item["helper"] or "VPS",
            _CHECK.get(str(item["checked"]), ""),
            vn_time(str(item["created_at"])),
        ]


def export_xlsx(db_path: Path, filters: ResultFilter) -> bytes:
    return workbook("Ket qua", EXPORT_HEADER, export_rows(db_path, filters), EXPORT_WIDTHS)


IMPORT_MAX_BYTES = 20 * 1024 * 1024
_SHEET_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_SHEET_T = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
_IMPORT_FIELDS = {
    "số điện thoại": "phone",
    "phone": "phone",
    "tên": "name",
    "name": "name",
    "username": "username",
    "loại": "bucket",
    "bucket": "bucket",
    "lý do": "reason",
    "reason": "reason",
    "đọc bởi": "reader",
    "helper": "reader",
    "reader": "reader",
    "đọc lúc (giờ vn)": "created_at",
    "created_at": "created_at",
}


def _field_name(cell: str) -> str:
    return _IMPORT_FIELDS.get(" ".join(str(cell or "").split()).casefold(), "")


def _csv_grid(payload: bytes) -> list[list[str]]:
    text = payload.decode("utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _column_index(ref: str) -> int:
    index = 0
    for ch in ref:
        if not ch.isalpha():
            break
        index = index * 26 + (ord(ch.upper()) - 64)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(_SHEET_T)) for item in root.findall("m:si", _SHEET_NS)]


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t") or ""
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_SHEET_T))
    raw = cell.findtext("m:v", default="", namespaces=_SHEET_NS)
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if raw.endswith(".0") and raw[:-2].isdigit():
        return raw[:-2]
    return raw


def _xlsx_grid(payload: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared = _shared_strings(archive)
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    grid: list[list[str]] = []
    sheet = root.find("m:sheetData", _SHEET_NS)
    if sheet is None:
        return grid
    for row in sheet.findall("m:row", _SHEET_NS):
        parsed: list[str] = []
        for cell in row.findall("m:c", _SHEET_NS):
            index = _column_index(cell.get("r") or "")
            if index < 0:
                continue
            while len(parsed) <= index:
                parsed.append("")
            parsed[index] = _cell_text(cell, shared).strip()
        grid.append(parsed)
    return grid


def _rows_from_grid(grid: list[list[str]]) -> list[dict[str, str]]:
    if not grid:
        return []
    header = [_field_name(cell) for cell in grid[0]]
    if "phone" not in header:
        raise ValueError("File thiếu cột số điện thoại.")
    rows: list[dict[str, str]] = []
    blank = {"phone": "", "name": "", "username": "", "bucket": "", "reason": "", "reader": "", "created_at": ""}
    for cells in grid[1:]:
        if not any(str(cell).strip() for cell in cells):
            continue
        item = dict(blank)
        for index, key in enumerate(header):
            if key and index < len(cells):
                item[key] = str(cells[index]).strip()
        rows.append(item)
        if len(rows) > IMPORT_ROW_LIMIT:
            raise ValueError("File vượt 100000 dòng.")
    return rows


def parse_import(payload: bytes, filename: str) -> list[dict[str, str]]:
    """Đọc file Excel xuất ra hoặc CSV. Excel lưu lại bằng shared string và ô số đều đọc được."""
    if len(payload) > IMPORT_MAX_BYTES:
        raise ValueError("File quá 20 MB.")
    suffix = Path(str(filename or "")).suffix.lower()
    try:
        if suffix == ".xlsx" or (suffix != ".csv" and payload[:2] == b"PK"):
            grid = _xlsx_grid(payload)
        else:
            grid = _csv_grid(payload)
    except (zipfile.BadZipFile, ElementTree.ParseError, UnicodeError) as exc:
        raise ValueError("File không đọc được.") from exc
    return _rows_from_grid(grid)


# Khoá API chỉ đọc: chỉ lưu mã băm, khoá đầy đủ chỉ hiện một lần lúc tạo.
API_PREFIX = "gk_"


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_api_key(db_path: Path, name: str) -> dict[str, object]:
    key = API_PREFIX + secrets.token_urlsafe(24)
    stamp = utcnow()
    label = " ".join(str(name or "").split())[:60]
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO api_keys (name, prefix, key_hash, created_at) VALUES (?, ?, ?, ?)",
            (label, key[:7], _key_hash(key), stamp),
        )
        key_id = int(cur.lastrowid or 0)
    return {"id": key_id, "name": label, "key": key, "prefix": key[:7], "created_at": stamp}


def list_api_keys(db_path: Path) -> list[dict[str, object]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, prefix, created_at, revoked_at, last_used_at FROM api_keys ORDER BY id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_api_key(db_path: Path, key_id: int) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (utcnow(), key_id),
        )
        return cur.rowcount == 1


def api_key_ok(db_path: Path, key: str) -> bool:
    """Khoá còn dùng được. Ghi lần dùng gần nhất tối đa mỗi phút một lần."""
    if not key.startswith(API_PREFIX):
        return False
    digest = _key_hash(key)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, revoked_at, last_used_at FROM api_keys WHERE key_hash = ?",
            (digest,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return False
        last = str(row["last_used_at"] or "")
        now = utcnow()
        if not last or now[:16] != last[:16]:
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, int(row["id"])))
    return True


def saved_rows_after(db_path: Path, after_id: int, limit: int) -> list[dict[str, object]]:
    """Dòng đã lưu đủ số, tên và @id, id tăng dần, sau một id. Dùng cho khoá API và webhook."""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT r.id, r.phone, r.name, r.username, r.video_id, r.created_at,
                   COALESCE(v.device, '') AS device, {READER_SQL} AS helper
            FROM results r
            LEFT JOIN videos v ON v.id = r.video_id
            WHERE r.id > ? AND r.bucket = ? AND r.phone != '' AND r.name != '' AND r.username != ''
            ORDER BY r.id
            LIMIT ?
            """,
            (int(after_id), BUCKET_OK, max(1, min(int(limit), 5000))),
        ).fetchall()
    items = []
    for row in rows:
        normalized, network, _note = phone_columns(str(row["phone"]))
        items.append(
            {
                "id": int(row["id"]),
                "phone": str(row["phone"]),
                "normalized": normalized,
                "carrier": network,
                "name": str(row["name"]),
                "username": str(row["username"]),
                "video_id": int(row["video_id"] or 0),
                "device": str(row["device"]),
                "read_by": str(row["helper"] or "VPS"),
                "read_at": str(row["created_at"]),
            }
        )
    return items


# Webhook: gửi dòng đã lưu mới ra một địa chỉ, ví dụ Google Apps Script để ghi vào Google Sheets.
WEBHOOK_URL = "webhook_url"
WEBHOOK_LAST = "webhook_last_id"
WEBHOOK_STATE = "webhook_state"
WEBHOOK_BATCH = 200


def check_public_url(url: str, resolve: Callable[[str], list[str]] | None = None) -> str:
    """Chỉ nhận http/https tới địa chỉ công khai, để không gửi dữ liệu vào máy nội bộ."""
    text = str(url or "").strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Địa chỉ phải bắt đầu bằng https:// hoặc http://")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        literal = False
    else:
        literal = True
    lookup = resolve or _resolve
    try:
        addresses = [host] if literal else lookup(host)
    except OSError as exc:
        raise ValueError("Không tìm thấy địa chỉ này.") from exc
    if not addresses:
        raise ValueError("Không tìm thấy địa chỉ này.")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise ValueError("Không gửi tới địa chỉ nội bộ.")
    return text


def _resolve(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None)})


def webhook_settings(db_path: Path) -> dict[str, object]:
    raw = get_meta(db_path, WEBHOOK_STATE, "{}")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        state = {}
    url = get_meta(db_path, WEBHOOK_URL, "")
    last = int(get_meta(db_path, WEBHOOK_LAST, "0") or 0)
    with connect(db_path) as conn:
        waiting = conn.execute(
            """
            SELECT COUNT(*) AS n FROM results
            WHERE id > ? AND bucket = ? AND phone != '' AND name != '' AND username != ''
            """,
            (last, BUCKET_OK),
        ).fetchone()
    return {
        "url": url,
        "last_id": last,
        "waiting": int(waiting["n"]) if url else 0,
        "sent": int(state.get("sent", 0)),
        "error": str(state.get("error", "")),
        "at": str(state.get("at", "")),
    }


def save_webhook(db_path: Path, url: str, include_old: bool = False, resolve=None) -> None:
    """Lưu địa chỉ. Mặc định chỉ gửi dòng mới từ lúc lưu; include_old thì gửi cả dòng đã có."""
    text = str(url or "").strip()
    if not text:
        set_meta(db_path, WEBHOOK_URL, "")
        return
    checked = check_public_url(text, resolve)
    with connect(db_path) as conn:
        newest = int(conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM results").fetchone()["n"])
    set_meta(db_path, WEBHOOK_LAST, "0" if include_old else str(newest))
    set_meta(db_path, WEBHOOK_STATE, json.dumps({"sent": 0, "error": "", "at": ""}))
    set_meta(db_path, WEBHOOK_URL, checked)


def _public_ip(host: str, resolve: Callable[[str], list[str]] | None) -> str:
    """Một địa chỉ công khai để nối tới. Địa chỉ viết sẵn thì dùng đúng địa chỉ đó."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        lookup = resolve or _resolve
        try:
            addresses = lookup(host)
        except OSError as exc:
            raise ValueError("Không tìm thấy địa chỉ này.") from exc
    else:
        addresses = [host]
    if not addresses:
        raise ValueError("Không tìm thấy địa chỉ này.")
    for raw in addresses:
        if not ipaddress.ip_address(raw).is_global:
            raise ValueError("Không gửi tới địa chỉ nội bộ.")
    return addresses[0]


class _PinnedConnection(http.client.HTTPConnection):
    """Nối tới đúng địa chỉ đã kiểm, nhưng vẫn gửi tên máy cho chứng chỉ và tiêu đề Host."""

    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext | None) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip = ip
        self._context = context

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        if self._context is not None:
            sock = self._context.wrap_socket(sock, server_hostname=self.host)
        self.sock = sock


def _exchange(host: str, ip: str, port: int, scheme: str, target: str, body: bytes, timeout: float) -> tuple[int, str]:
    context = ssl.create_default_context() if scheme == "https" else None
    conn = _PinnedConnection(host, ip, port, timeout, context)
    try:
        conn.request("POST", target, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        status = response.status
        location = response.getheader("Location") or ""
        response.read()
        return status, location
    finally:
        conn.close()


def post_json(
    url: str,
    payload: dict,
    timeout: float = 30,
    resolve: Callable[[str], list[str]] | None = None,
    exchange: Callable[[str, str, int, str, str, bytes, float], tuple[int, str]] | None = None,
) -> None:
    """Gửi JSON. Giữ nguyên nội dung khi địa chỉ chuyển hướng, và không đi vào máy nội bộ."""
    sender = exchange or _exchange
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    current = url
    for _hop in range(5):
        checked = check_public_url(current, resolve)
        parts = urllib.parse.urlsplit(checked)
        host = parts.hostname or ""
        ip = _public_ip(host, resolve)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        status, location = sender(host, ip, port, parts.scheme, target, body, timeout)
        if status in (301, 302, 303, 307, 308) and location:
            current = urllib.parse.urljoin(checked, location)
            continue
        if status >= 300:
            raise urllib.error.HTTPError(checked, status, "gửi không thành công", None, None)
        return
    raise ValueError("Quá nhiều lần chuyển hướng.")


def send_webhook_batch(db_path: Path, poster: Callable[[str, dict], None] = post_json, resolve=None) -> int:
    """Gửi một lô dòng mới. Gửi xong mới dời mốc, nên lỗi mạng không làm mất dòng nào."""
    url = get_meta(db_path, WEBHOOK_URL, "")
    if not url:
        return 0
    last = int(get_meta(db_path, WEBHOOK_LAST, "0") or 0)
    rows = saved_rows_after(db_path, last, WEBHOOK_BATCH)
    if not rows:
        return 0
    raw = get_meta(db_path, WEBHOOK_STATE, "{}")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        state = {}
    try:
        check_public_url(url, resolve)
        payload = {"source": "ghiam", "rows": rows}
        if poster is post_json:
            poster(url, payload, resolve=resolve)
        else:
            poster(url, payload)
    except (ValueError, OSError, urllib.error.URLError) as exc:
        state.update(error=str(exc)[:300], at=utcnow())
        set_meta(db_path, WEBHOOK_STATE, json.dumps(state, ensure_ascii=False))
        raise
    if get_meta(db_path, WEBHOOK_URL, "") == url:
        set_meta(db_path, WEBHOOK_LAST, str(rows[-1]["id"]))
    state.update(sent=int(state.get("sent", 0)) + len(rows), error="", at=utcnow())
    set_meta(db_path, WEBHOOK_STATE, json.dumps(state, ensure_ascii=False))
    return len(rows)


def webhook_loop(db_path: Path, stop, poster=post_json) -> None:
    """Chạy trong tiến trình đọc (chỉ có một), để không gửi trùng. Lỗi thì chờ lâu dần, tối đa 10 phút."""
    wait = 15.0
    while not stop.is_set():
        try:
            sent = send_webhook_batch(db_path, poster)
            wait = 1.0 if sent >= WEBHOOK_BATCH else 15.0
        except Exception:
            wait = min(600.0, max(30.0, wait * 2))
        stop.wait(wait)


def sample_payload() -> dict[str, object]:
    return {
        "source": "ghiam",
        "test": True,
        "rows": [
            {
                "id": 0,
                "phone": "0900000000",
                "normalized": "0900000000",
                "carrier": "MobiFone",
                "name": "Dòng thử",
                "username": "@dong_thu",
                "video_id": 0,
                "device": "",
                "read_by": "VPS",
                "read_at": utcnow(),
            }
        ],
    }


def test_webhook(url: str, poster: Callable[[str, dict], None] = post_json, resolve=None) -> None:
    checked = check_public_url(url, resolve)
    if poster is post_json:
        poster(checked, sample_payload(), resolve=resolve)
    else:
        poster(checked, sample_payload())
