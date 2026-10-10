"""Phiên tải video cắt khúc — resume khi mất mạng / khóa máy.

Luồng: init → PUT từng chunk → complete → vào hàng đợi OCR như upload thường.
client_key (máy + tên + size + mtime) để tìm lại phiên dở.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from control_plane.video_store import (
    abort_upload,
    begin_upload,
    can_accept,
    clean_device,
    commit_upload,
    connect,
    queued_bytes,
    utcnow,
)
from control_plane.video_validate import probe_duration_sec, validate_media_file

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB — ổn định trên Wi‑Fi iPhone
MAX_CHUNK_SIZE = 16 * 1024 * 1024
UPLOAD_TTL_SEC = 7 * 24 * 3600


def upload_slots() -> int:
    """Số phiên tải đang nhận tối đa cùng lúc (nhiều iPhone × video lớn)."""
    raw = os.environ.get("CONTROL_VIDEO_UPLOAD_SLOTS", "").strip()
    if raw:
        return max(1, int(raw))
    return 16


def receiving_count(db_path: Path) -> int:
    ensure_upload_tables(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM upload_sessions WHERE status = 'receiving'"
        ).fetchone()
    return int(row["n"])


def uploads_dir(video_dir: Path) -> Path:
    path = video_dir / "incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_upload_tables(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_sessions (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              device TEXT NOT NULL DEFAULT '',
              client_key TEXT NOT NULL DEFAULT '',
              chunk_size INTEGER NOT NULL,
              received_map TEXT NOT NULL DEFAULT '',
              received_bytes INTEGER NOT NULL DEFAULT 0,
              path TEXT NOT NULL,
              sha256 TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'receiving',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_upload_sessions_client
              ON upload_sessions(client_key, status);
            CREATE INDEX IF NOT EXISTS idx_upload_sessions_status
              ON upload_sessions(status, updated_at);
            """
        )


def _chunks_needed(size_bytes: int, chunk_size: int) -> int:
    if size_bytes <= 0:
        return 0
    return (size_bytes + chunk_size - 1) // chunk_size


def _map_list(received_map: str, total: int) -> list[int]:
    if not received_map:
        return []
    try:
        data = json.loads(received_map)
        if isinstance(data, list):
            return sorted({int(x) for x in data if 0 <= int(x) < total})
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return []


def _map_dump(indices: list[int]) -> str:
    return json.dumps(sorted(set(indices)), separators=(",", ":"))


def _session_dict(row: sqlite3.Row) -> dict[str, object]:
    total = _chunks_needed(int(row["size_bytes"]), int(row["chunk_size"]))
    received = _map_list(str(row["received_map"] or ""), total)
    return {
        "upload_id": row["id"],
        "name": row["name"],
        "size_bytes": int(row["size_bytes"]),
        "device": row["device"] or "",
        "client_key": row["client_key"] or "",
        "chunk_size": int(row["chunk_size"]),
        "chunks_total": total,
        "received": received,
        "received_bytes": int(row["received_bytes"] or 0),
        "status": row["status"],
        "sha256": row["sha256"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def cleanup_stale_uploads(db_path: Path, video_dir: Path, ttl_sec: int = UPLOAD_TTL_SEC) -> int:
    """Xóa phiên receiving quá hạn và file partial."""
    ensure_upload_tables(db_path)
    cutoff = time.time() - max(3600, ttl_sec)
    removed = 0
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, path, updated_at, status FROM upload_sessions WHERE status = 'receiving'"
        ).fetchall()
    for row in rows:
        try:
            text = str(row["updated_at"]).replace("Z", "+00:00")
            updated = datetime.fromisoformat(text).timestamp()
        except (ValueError, TypeError):
            updated = 0.0
        if updated and updated > cutoff:
            continue
        path = Path(str(row["path"]))
        path.unlink(missing_ok=True)
        with connect(db_path) as conn:
            conn.execute("DELETE FROM upload_sessions WHERE id = ?", (row["id"],))
        removed += 1
    # Dọn file mồ côi trong incoming/
    folder = uploads_dir(video_dir)
    with connect(db_path) as conn:
        known = {str(r["path"]) for r in conn.execute("SELECT path FROM upload_sessions")}
    for orphan in folder.glob("up-*.part"):
        if str(orphan) not in known:
            orphan.unlink(missing_ok=True)
            removed += 1
    return removed


def init_upload(
    db_path: Path,
    video_dir: Path,
    *,
    name: str,
    size_bytes: int,
    device: str = "",
    client_key: str = "",
    chunk_size: int | None = None,
    sha256: str = "",
    disk_limit_bytes: int,
) -> dict[str, object]:
    ensure_upload_tables(db_path)
    cleanup_stale_uploads(db_path, video_dir)
    if size_bytes <= 0:
        raise ValueError("size_bytes phải > 0")
    machine = clean_device(device)
    original = Path(name or "video.mp4").name
    key = " ".join((client_key or "").split())[:200]
    size = int(chunk_size or DEFAULT_CHUNK_SIZE)
    # Tối thiểu 16KiB (test / mạng yếu); mặc định 4MiB cho iPhone.
    size = max(16 * 1024, min(MAX_CHUNK_SIZE, size))

    if key:
        with connect(db_path) as conn:
            existing = conn.execute(
                """
                SELECT * FROM upload_sessions
                WHERE client_key = ? AND status = 'receiving'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (key,),
            ).fetchone()
        if existing is not None and int(existing["size_bytes"]) == size_bytes and Path(str(existing["path"])).exists():
            body = _session_dict(existing)
            body["resumed"] = True
            body["upload_slots"] = upload_slots()
            body["receiving"] = receiving_count(db_path)
            return body

    slots = upload_slots()
    active = receiving_count(db_path)
    if active >= slots:
        raise MemoryError(
            f"Đang nhận tối đa {slots} video cùng lúc. Đợi máy khác gửi xong rồi thử lại (tự resume)."
        )

    free = shutil.disk_usage(video_dir).free
    used = queued_bytes(db_path)
    if not can_accept(used, size_bytes, disk_limit_bytes, free):
        raise MemoryError(
            "Đĩa/hàng đợi gần đầy. Đợi máy chủ đọc xong bớt video rồi tải tiếp — app sẽ tự thử lại."
        )

    upload_id = secrets.token_hex(16)
    part = uploads_dir(video_dir) / f"up-{upload_id}.part"
    # Sparse truncate trên ext4 — không chiếm đủ GB ngay, vẫn seek/chunk ổn định.
    with part.open("wb") as handle:
        handle.truncate(size_bytes)

    now = utcnow()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO upload_sessions (
              id, name, size_bytes, device, client_key, chunk_size,
              received_map, received_bytes, path, sha256, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', 0, ?, ?, 'receiving', ?, ?)
            """,
            (upload_id, original, size_bytes, machine, key, size, str(part), sha256 or "", now, now),
        )
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
    body = _session_dict(row)
    body["resumed"] = False
    body["upload_slots"] = slots
    body["receiving"] = active + 1
    return body


def get_upload(db_path: Path, upload_id: str) -> dict[str, object] | None:
    ensure_upload_tables(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        return None
    return _session_dict(row)


def put_chunk(
    db_path: Path,
    upload_id: str,
    index: int,
    data: bytes,
) -> dict[str, object]:
    ensure_upload_tables(db_path)
    if index < 0:
        raise ValueError("chunk index không hợp lệ")
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
    if row is None or row["status"] != "receiving":
        raise LookupError("Không thấy phiên tải hoặc đã xong.")
    chunk_size = int(row["chunk_size"])
    size_bytes = int(row["size_bytes"])
    total = _chunks_needed(size_bytes, chunk_size)
    if index >= total:
        raise ValueError("chunk index vượt quá file")
    expected = chunk_size if index < total - 1 else size_bytes - index * chunk_size
    if len(data) != expected:
        raise ValueError(f"Chunk {index} cần đúng {expected} byte, nhận {len(data)}.")

    path = Path(str(row["path"]))
    if not path.exists():
        raise LookupError("File partial đã mất — khởi tạo lại phiên tải.")
    with path.open("r+b") as handle:
        handle.seek(index * chunk_size)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())

    received = _map_list(str(row["received_map"] or ""), total)
    if index not in received:
        received.append(index)
    received_bytes = 0
    for i in received:
        received_bytes += chunk_size if i < total - 1 else size_bytes - i * chunk_size

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE upload_sessions
            SET received_map = ?, received_bytes = ?, updated_at = ?
            WHERE id = ? AND status = 'receiving'
            """,
            (_map_dump(received), received_bytes, utcnow(), upload_id),
        )
    body = get_upload(db_path, upload_id)
    assert body is not None
    return body


def complete_upload(
    db_path: Path,
    video_dir: Path,
    upload_id: str,
    *,
    disk_limit_bytes: int,
) -> dict[str, object]:
    ensure_upload_tables(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise LookupError("Không thấy phiên tải.")
    if row["status"] == "completed":
        return {
            "ok": True,
            "already": True,
            "upload_id": upload_id,
            "status": "completed",
            "sha256": row["sha256"] or "",
        }
    if row["status"] != "receiving":
        raise LookupError("Phiên tải không còn nhận dữ liệu.")

    chunk_size = int(row["chunk_size"])
    size_bytes = int(row["size_bytes"])
    total = _chunks_needed(size_bytes, chunk_size)
    received = _map_list(str(row["received_map"] or ""), total)
    if len(received) != total:
        missing = [i for i in range(total) if i not in set(received)]
        raise ValueError(f"Thiếu {len(missing)} chunk: {missing[:12]}")

    path = Path(str(row["path"]))
    if not path.exists() or path.stat().st_size != size_bytes:
        raise ValueError("File partial không đủ kích thước.")

    # Chặn file giả / tải dở (moov atom not found) trước khi vào hàng OCR.
    try:
        validate_media_file(path, original_name=str(row["name"]))
    except ValueError:
        path.unlink(missing_ok=True)
        with connect(db_path) as conn:
            conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))
        raise

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
    digest = hasher.hexdigest()
    expected = str(row["sha256"] or "").strip().lower()
    if expected and expected != digest:
        raise ValueError("SHA-256 không khớp.")

    free = shutil.disk_usage(video_dir).free
    used = queued_bytes(db_path)
    if not can_accept(used, size_bytes, disk_limit_bytes, free):
        raise MemoryError("Hàng đợi đã đầy. Đợi bớt video rồi thêm tiếp.")

    created = begin_upload(
        db_path,
        name=str(row["name"]),
        size_bytes=size_bytes,
        sha256=digest,
        device=str(row["device"] or ""),
    )
    if created.get("duplicate"):
        path.unlink(missing_ok=True)
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE upload_sessions
                SET status = 'completed', sha256 = ?, received_bytes = ?, updated_at = ?
                WHERE id = ?
                """,
                (digest, size_bytes, utcnow(), upload_id),
            )
        return {
            "ok": True,
            "duplicate": True,
            "upload_id": upload_id,
            "id": created["id"],
            "status": created["status"],
            "name": created["name"],
            "device": created.get("device") or "",
            "sha256": digest,
        }

    video_id = int(created["id"])
    suffix = Path(str(row["name"])).suffix.lower() or ".mp4"
    final = video_dir / f"{video_id}{suffix}"
    os.replace(path, final)
    old_path = str(created.pop("old_path", "") or "")
    if old_path and old_path != str(final):
        Path(old_path).unlink(missing_ok=True)
    duration = probe_duration_sec(final)
    try:
        commit_upload(db_path, video_id, str(final), duration)
    except Exception:
        abort_upload(db_path, video_id, bool(created.get("reopened")))
        final.unlink(missing_ok=True)
        raise

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE upload_sessions
            SET status = 'completed', sha256 = ?, path = ?, received_bytes = ?, updated_at = ?
            WHERE id = ?
            """,
            (digest, str(final), size_bytes, utcnow(), upload_id),
        )
    return {
        "ok": True,
        "duplicate": False,
        "upload_id": upload_id,
        "id": video_id,
        "status": "queued",
        "name": created["name"],
        "device": created.get("device") or "",
        "sha256": digest,
        "duration_sec": duration,
        "reopened": bool(created.get("reopened")),
    }


def abort_session(db_path: Path, upload_id: str) -> bool:
    ensure_upload_tables(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT path, status FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))
    Path(str(row["path"])).unlink(missing_ok=True)
    return True
