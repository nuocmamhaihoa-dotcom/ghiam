"""Đưa kết quả ra ngoài: file Excel theo bộ lọc, gửi dòng mới tới webhook hoặc Google Sheets, khoá API chỉ đọc."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from control_plane.phone_vn import phone_columns
from control_plane.video_store import (
    BUCKET_OK,
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
            """
            SELECT r.id, r.phone, r.name, r.username, r.video_id, r.created_at,
                   COALESCE(v.device, '') AS device, COALESCE(v.helper, '') AS helper
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
    lookup = resolve or _resolve
    try:
        addresses = lookup(parts.hostname)
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


def post_json(url: str, payload: dict, timeout: float = 30) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise urllib.error.HTTPError(url, response.status, "gửi không thành công", response.headers, None)


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
        poster(url, {"source": "ghiam", "rows": rows})
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
    poster(check_public_url(url, resolve), sample_payload())
