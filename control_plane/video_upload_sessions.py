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

from contextlib import contextmanager
from typing import Iterator

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

# 16 MiB × vài kết nối: một vòng HTTP không còn đáng kể so với Wi‑Fi iPhone.
# Cửa sổ TCP 16MB trên VPS nuốt cả mảnh vào RAM rồi mới đổ đĩa.
DEFAULT_CHUNK_SIZE = 16 * 1024 * 1024
MAX_CHUNK_SIZE = 16 * 1024 * 1024
UPLOAD_TTL_SEC = 7 * 24 * 3600
# Không nhận chunk mới trong khoảng này → coi là tải bị ngắt (Safari khóa máy…).
STALE_UPLOAD_SEC = 20 * 60
# Phiên đã có byte: giữ tới 7 ngày. iPhone có thể tắt Safari rồi mở lại để gửi tiếp.
ABANDON_UPLOAD_SEC = 7 * 24 * 3600
# Chưa có byte: đợi 15 phút. Sang app khác có thể chậm hơn 90 giây trước mảnh đầu.
ZERO_BYTE_ABANDON_SEC = 15 * 60
_MIGRATED: set[str] = set()


def upload_slots() -> int:
    """Số phiên tải đang nhận tối đa cùng lúc (nhiều iPhone × video lớn)."""
    raw = os.environ.get("CONTROL_VIDEO_UPLOAD_SLOTS", "").strip()
    if raw:
        return max(1, int(raw))
    # 50 video × nhiều iPhone: slot là phiên đã xếp, không phải số kết nối.
    # Phiên im >20 phút không tính (receiving_count) nên không kẹt slot vĩnh viễn.
    return 64


@contextmanager
def uploads_connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Kết nối DB phiên tải — timeout dài, WAL, không tranh OCR nếu DB tách riêng."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=120, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=120000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    # Map phiên nhỏ — giữ trong RAM, checkpoint thưa để PUT không chờ đĩa.
    conn.execute("PRAGMA cache_size=-65536;")
    conn.execute("PRAGMA mmap_size=268435456;")
    conn.execute("PRAGMA wal_autocheckpoint=10000;")
    try:
        yield conn
    finally:
        conn.close()


def _retry_locked(action, *, tries: int = 12, label: str = "upload-db"):
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            last = exc
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            time.sleep(min(6.0, 0.25 * (2**attempt)))
    raise sqlite3.OperationalError(f"{label}: database is locked sau {tries} lần thử") from last


def _parse_updated_ts(value: object) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def receiving_count(db_path: Path, *, include_stale: bool = False) -> int:
    """Số phiên đang nhận thật (còn sống). Mặc định bỏ phiên stale để không nghẽn slot."""
    ensure_upload_tables(db_path)
    cutoff = time.time() - STALE_UPLOAD_SEC

    def _count() -> int:
        with uploads_connect(db_path) as conn:
            rows = conn.execute(
                "SELECT updated_at FROM upload_sessions WHERE status = 'receiving'"
            ).fetchall()
        n = 0
        for row in rows:
            if include_stale:
                n += 1
                continue
            updated = _parse_updated_ts(row["updated_at"])
            if updated and updated >= cutoff:
                n += 1
        return n

    return int(_retry_locked(_count, label="receiving_count"))


def uploads_dir(video_dir: Path) -> Path:
    path = video_dir / "incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_upload_tables(db_path: Path, *, legacy_db: Path | None = None) -> None:
    def _create() -> None:
        with uploads_connect(db_path) as conn:
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

    _retry_locked(_create, label="ensure_upload_tables")
    if legacy_db is not None:
        migrate_upload_sessions(legacy_db, db_path)


def migrate_upload_sessions(legacy_db: Path, uploads_db: Path) -> int:
    """Chuyển phiên từ video.db cũ sang video_uploads.db (một lần)."""
    key = f"{legacy_db.resolve()}->{uploads_db.resolve()}"
    if key in _MIGRATED:
        return 0
    if not legacy_db.exists() or legacy_db.resolve() == uploads_db.resolve():
        _MIGRATED.add(key)
        return 0

    def _migrate() -> int:
        with uploads_connect(uploads_db) as dest:
            existing = int(dest.execute("SELECT COUNT(*) AS n FROM upload_sessions").fetchone()["n"])
            if existing > 0:
                return 0
        try:
            with connect(legacy_db) as src:
                has = src.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
                ).fetchone()
                if has is None:
                    return 0
                rows = src.execute("SELECT * FROM upload_sessions").fetchall()
        except sqlite3.OperationalError:
            return 0
        if not rows:
            return 0
        moved = 0
        with uploads_connect(uploads_db) as dest:
            for row in rows:
                dest.execute(
                    """
                    INSERT OR IGNORE INTO upload_sessions (
                      id, name, size_bytes, device, client_key, chunk_size,
                      received_map, received_bytes, path, sha256, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["name"],
                        row["size_bytes"],
                        row["device"] or "",
                        row["client_key"] or "",
                        row["chunk_size"],
                        row["received_map"] or "",
                        row["received_bytes"] or 0,
                        row["path"],
                        row["sha256"] or "",
                        row["status"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                moved += 1
        return moved

    moved = int(_retry_locked(_migrate, label="migrate_upload_sessions"))
    _MIGRATED.add(key)
    if moved:
        print(f"Đã chuyển {moved} phiên upload sang {uploads_db.name}", flush=True)
    return moved


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


def contiguous_offset(received: list[int], chunk_size: int, size_bytes: int) -> int:
    """Byte đầu tiên còn thiếu. iOS 16 gửi một file phần đuôi từ đây, không dùng resume data."""
    total = _chunks_needed(size_bytes, chunk_size)
    if chunk_size <= 0 or total <= 0:
        return 0
    have = set(received)
    done = 0
    while done < total and done in have:
        done += 1
    if done >= total:
        return size_bytes
    return done * chunk_size


def _chunks_covered(offset: int, written: int, chunk_size: int, size_bytes: int) -> list[int]:
    """Mảnh nằm trọn trong [offset, offset+written). Mảnh viết dở không tính — lần sau gửi lại từ đầu mảnh."""
    total = _chunks_needed(size_bytes, chunk_size)
    if written <= 0 or total <= 0 or chunk_size <= 0 or offset < 0:
        return []
    end = min(size_bytes, offset + written)
    if end <= offset:
        return []
    covered: list[int] = []
    for index in range(total):
        start = index * chunk_size
        stop = size_bytes if index == total - 1 else (index + 1) * chunk_size
        if start >= offset and stop <= end:
            covered.append(index)
    return covered


class TailOffsetError(ValueError):
    """Offset không phải byte liền mạch máy chủ đang chờ."""


def _session_dict(row: sqlite3.Row) -> dict[str, object]:
    total = _chunks_needed(int(row["size_bytes"]), int(row["chunk_size"]))
    received = _map_list(str(row["received_map"] or ""), total)
    chunk_size = int(row["chunk_size"])
    size_bytes = int(row["size_bytes"])
    return {
        "upload_id": row["id"],
        "name": row["name"],
        "size_bytes": size_bytes,
        "device": row["device"] or "",
        "client_key": row["client_key"] or "",
        "chunk_size": chunk_size,
        "chunks_total": total,
        "received": received,
        "received_bytes": int(row["received_bytes"] or 0),
        "contiguous_offset": contiguous_offset(received, chunk_size, size_bytes),
        "status": row["status"],
        "sha256": row["sha256"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def cleanup_stale_uploads(
    db_path: Path,
    video_dir: Path,
    ttl_sec: int = UPLOAD_TTL_SEC,
    abandon_sec: int = ABANDON_UPLOAD_SEC,
) -> int:
    """Xóa phiên receiving quá hạn / bỏ cuộc và file partial mồ côi.

    - ttl_sec: TTL tuyệt đối (mặc định 7 ngày)
    - abandon_sec: không nhận chunk mới trong khoảng này → xóa để hết nghẽn slot
    """
    ensure_upload_tables(db_path)
    now = time.time()
    # abandon_sec (mặc định 2h) hoặc TTL dài hơn — cái nào sớm hơn thì dọn.
    soft_cutoff = now - min(max(3600, ttl_sec), max(STALE_UPLOAD_SEC, abandon_sec))
    removed = 0

    def _rows():
        with uploads_connect(db_path) as conn:
            return conn.execute(
                """
                SELECT id, path, updated_at, status, received_bytes
                FROM upload_sessions WHERE status = 'receiving'
                """
            ).fetchall()

    rows = _retry_locked(_rows, label="cleanup_list")
    for row in rows:
        updated = _parse_updated_ts(row["updated_at"])
        received = int(row["received_bytes"] or 0)
        # 0 byte quá 15 phút: không có mảnh nào tới. Xóa để nhường chỗ. Trang còn file sẽ tạo phiên mới.
        if received <= 0 and (not updated or updated < now - ZERO_BYTE_ABANDON_SEC):
            pass
        elif updated and updated >= soft_cutoff:
            # Còn sống (vừa có chunk trong abandon_sec) → giữ để resume.
            continue
        path = Path(str(row["path"]))
        path.unlink(missing_ok=True)

        def _delete(rid=row["id"]) -> None:
            with uploads_connect(db_path) as conn:
                conn.execute("DELETE FROM upload_sessions WHERE id = ?", (rid,))

        _retry_locked(_delete, label="cleanup_delete")
        removed += 1
        age_min = int((now - updated) / 60) if updated else -1
        print(f"Đã dọn phiên tải dở {row['id']} (im {age_min} phút)", flush=True)
    # Dọn file mồ côi trong incoming/
    folder = uploads_dir(video_dir)

    def _known():
        with uploads_connect(db_path) as conn:
            return {str(r["path"]) for r in conn.execute("SELECT path FROM upload_sessions")}

    known = _retry_locked(_known, label="cleanup_known")
    for orphan in folder.glob("up-*.part"):
        if str(orphan) not in known:
            orphan.unlink(missing_ok=True)
            removed += 1
            print(f"Đã xóa file partial mồ côi {orphan.name}", flush=True)
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
    video_db_path: Path | None = None,
    legacy_db: Path | None = None,
) -> dict[str, object]:
    ensure_upload_tables(db_path, legacy_db=legacy_db)
    cleanup_stale_uploads(db_path, video_dir)
    if size_bytes <= 0:
        raise ValueError("size_bytes phải > 0")
    machine = clean_device(device)
    original = Path(name or "video.mp4").name
    key = " ".join((client_key or "").split())[:200]
    size = int(chunk_size or DEFAULT_CHUNK_SIZE)
    # Tối thiểu 16KiB (test); mặc định 16MiB — một vòng HTTP không còn đáng kể trên Wi‑Fi.
    size = max(16 * 1024, min(MAX_CHUNK_SIZE, size))
    video_db = video_db_path or db_path

    if key:
        def _find():
            with uploads_connect(db_path) as conn:
                return conn.execute(
                    """
                    SELECT * FROM upload_sessions
                    WHERE client_key = ? AND status = 'receiving'
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (key,),
                ).fetchone()

        existing = _retry_locked(_find, label="init_find")
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
    used = queued_bytes(video_db)
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

    def _insert() -> sqlite3.Row:
        with uploads_connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO upload_sessions (
                  id, name, size_bytes, device, client_key, chunk_size,
                  received_map, received_bytes, path, sha256, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', 0, ?, ?, 'receiving', ?, ?)
                """,
                (upload_id, original, size_bytes, machine, key, size, str(part), sha256 or "", now, now),
            )
            row = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()
            assert row is not None
            return row

    try:
        row = _retry_locked(_insert, label="init_insert")
    except Exception:
        part.unlink(missing_ok=True)
        raise
    body = _session_dict(row)
    body["resumed"] = False
    body["upload_slots"] = slots
    body["receiving"] = active + 1
    return body


def get_upload(db_path: Path, upload_id: str) -> dict[str, object] | None:
    ensure_upload_tables(db_path)

    def _get():
        with uploads_connect(db_path) as conn:
            return conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()

    row = _retry_locked(_get, label="get_upload")
    if row is None:
        return None
    return _session_dict(row)


def list_receiving_uploads(db_path: Path, limit: int = 80) -> list[dict[str, object]]:
    """Phiên đang tải / tải dở — luôn hiện trên hàng đợi dù chưa vào bảng videos."""
    ensure_upload_tables(db_path)
    start = max(1, int(limit))

    def _list():
        with uploads_connect(db_path) as conn:
            return conn.execute(
                """
                SELECT * FROM upload_sessions
                WHERE status = 'receiving'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (start,),
            ).fetchall()

    rows = _retry_locked(_list, label="list_receiving")
    now = time.time()
    items: list[dict[str, object]] = []
    for row in rows:
        body = _session_dict(row)
        size = int(body["size_bytes"] or 0)
        received = int(body["received_bytes"] or 0)
        percent = int(100.0 * received / size) if size > 0 else 0
        chunks_total = int(body["chunks_total"] or 0)
        chunks_done = len(body.get("received") or [])
        updated = _parse_updated_ts(body.get("updated_at"))
        age = (now - updated) if updated else now
        stale = age >= STALE_UPLOAD_SEC
        speed = ""
        created = _parse_updated_ts(body.get("created_at"))
        if created and received > 0:
            elapsed = max(1.0, now - created)
            kb = received / elapsed / 1024.0
            if kb >= 1024:
                speed = f" · {kb / 1024:.1f} MB/s"
            else:
                speed = f" · {kb:.0f} KB/s"
        if stale:
            issue = (
                f"Tải bị ngắt ở {percent}% — chọn lại cùng file trên Safari để tiếp tục"
                f" ({chunks_done}/{chunks_total} mảnh đã có trên VPS){speed}"
            )
        elif percent <= 0 and chunks_done <= 0:
            if age >= 12:
                issue = (
                    "Chưa có byte — trang sẽ tự gửi lại. Mở app khác vẫn được, đừng vuốt tắt Safari."
                )
            else:
                issue = "Đang gửi — mở app khác vẫn chạy, không cần chọn lại file."
        elif speed and received / max(1.0, now - (created or now)) < 200 * 1024:
            issue = (
                f"Đang tải lên {percent}% ({chunks_done}/{chunks_total} mảnh){speed}"
                " — chậm: tắt iCloud Private Relay + Chế độ dữ liệu thấp, chọn lại file trên Safari"
            )
        else:
            issue = f"Đang tải lên {percent}% ({chunks_done}/{chunks_total} mảnh){speed}"
        items.append(
            {
                "upload_id": body["upload_id"],
                "name": body["name"],
                "size_bytes": size,
                "device": body.get("device") or "",
                "status": "uploading",
                "percent": percent,
                "received_bytes": received,
                "chunks_total": chunks_total,
                "chunks_done": chunks_done,
                "stale": stale,
                "issue": issue,
                "created_at": body.get("created_at") or "",
                "updated_at": body.get("updated_at") or "",
                "duration_sec": 0,
                "file_kept": False,
                "saved_count": 0,
                "review_count": 0,
                "unopened_count": 0,
                "result_count": 0,
                "error": "",
                "progress": issue,
                "kind": "upload",
            }
        )
    return items


def put_chunk(
    db_path: Path,
    upload_id: str,
    index: int,
    data: bytes,
) -> dict[str, object]:
    ensure_upload_tables(db_path)
    if index < 0:
        raise ValueError("chunk index không hợp lệ")

    def _load():
        with uploads_connect(db_path) as conn:
            return conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()

    row = _retry_locked(_load, label="put_chunk_load")
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
    # pwrite: nhiều mảnh song song không tranh con trỏ file. Không fsync —
    # kernel giữ trong RAM (dirty cache) rồi trả 200 ngay, đĩa bắt kịp sau.
    fd = os.open(path, os.O_RDWR)
    try:
        view = memoryview(data)
        offset = index * chunk_size
        written = 0
        while written < len(view):
            n = os.pwrite(fd, view[written:], offset + written)
            if n <= 0:
                raise OSError("pwrite không ghi được")
            written += n
    finally:
        os.close(fd)

    now = utcnow()

    def _update() -> int:
        with uploads_connect(db_path) as conn:
            # BEGIN IMMEDIATE: nhiều PUT cùng phiên không được ghi đè received_map của nhau.
            conn.execute("BEGIN IMMEDIATE")
            try:
                fresh = conn.execute(
                    "SELECT received_map, status FROM upload_sessions WHERE id = ?",
                    (upload_id,),
                ).fetchone()
                if fresh is None or fresh["status"] != "receiving":
                    raise LookupError("Không thấy phiên tải hoặc đã xong.")
                merged = _map_list(str(fresh["received_map"] or ""), total)
                if index not in merged:
                    merged.append(index)
                bytes_now = 0
                for i in merged:
                    bytes_now += chunk_size if i < total - 1 else size_bytes - i * chunk_size
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET received_map = ?, received_bytes = ?, updated_at = ?
                    WHERE id = ? AND status = 'receiving'
                    """,
                    (_map_dump(merged), bytes_now, now, upload_id),
                )
                conn.execute("COMMIT")
                return bytes_now
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    received_bytes = int(_retry_locked(_update, label="put_chunk_update"))
    # Không đọc lại cả phiên — client chỉ cần 200 để gửi mảnh kế.
    return {"ok": True, "upload_id": upload_id, "index": index, "received_bytes": received_bytes}


def pwrite_at(path: Path, offset: int, data: bytes) -> int:
    """Ghi một khúc vào file partial. Không giữ cả body 1.5GB trong RAM."""
    if offset < 0:
        raise ValueError("offset không hợp lệ")
    if not data:
        return 0
    fd = os.open(str(path), os.O_RDWR)
    try:
        view = memoryview(data)
        written = 0
        while written < len(view):
            n = os.pwrite(fd, view[written:], offset + written)
            if n <= 0:
                raise OSError("pwrite không ghi được")
            written += n
        return written
    finally:
        os.close(fd)


def open_tail(db_path: Path, upload_id: str, offset: int) -> Path:
    """Mở file partial khi offset đúng byte liền mạch. Lệch thì TailOffsetError (HTTP 409)."""
    ensure_upload_tables(db_path)

    def _load():
        with uploads_connect(db_path) as conn:
            return conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()

    row = _retry_locked(_load, label="open_tail")
    if row is None or row["status"] != "receiving":
        raise LookupError("Không thấy phiên tải hoặc đã xong.")
    chunk_size = int(row["chunk_size"])
    size_bytes = int(row["size_bytes"])
    total = _chunks_needed(size_bytes, chunk_size)
    received = _map_list(str(row["received_map"] or ""), total)
    expected = contiguous_offset(received, chunk_size, size_bytes)
    if int(offset) != expected:
        raise TailOffsetError(f"offset {offset} không khớp byte liền {expected}")
    path = Path(str(row["path"]))
    if not path.exists():
        raise LookupError("File partial đã mất — khởi tạo lại phiên tải.")
    return path


def note_tail(db_path: Path, upload_id: str, offset: int, written: int) -> dict[str, object]:
    """Đánh dấu các mảnh nằm trọn trong đoạn vừa ghi. Không ghi byte — caller đã pwrite."""
    ensure_upload_tables(db_path)

    def _load():
        with uploads_connect(db_path) as conn:
            return conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()

    row = _retry_locked(_load, label="note_tail_load")
    if row is None or row["status"] != "receiving":
        raise LookupError("Không thấy phiên tải hoặc đã xong.")
    chunk_size = int(row["chunk_size"])
    size_bytes = int(row["size_bytes"])
    total = _chunks_needed(size_bytes, chunk_size)
    covered = _chunks_covered(int(offset), int(written), chunk_size, size_bytes)
    now = utcnow()

    def _update() -> None:
        with uploads_connect(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                fresh = conn.execute(
                    "SELECT received_map, status FROM upload_sessions WHERE id = ?",
                    (upload_id,),
                ).fetchone()
                if fresh is None or fresh["status"] != "receiving":
                    raise LookupError("Không thấy phiên tải hoặc đã xong.")
                merged = _map_list(str(fresh["received_map"] or ""), total)
                for index in covered:
                    if index not in merged:
                        merged.append(index)
                bytes_now = 0
                for index in merged:
                    bytes_now += chunk_size if index < total - 1 else size_bytes - index * chunk_size
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET received_map = ?, received_bytes = ?, updated_at = ?
                    WHERE id = ? AND status = 'receiving'
                    """,
                    (_map_dump(merged), bytes_now, now, upload_id),
                )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    _retry_locked(_update, label="note_tail_update")
    body = get_upload(db_path, upload_id)
    if body is None:
        raise LookupError("Không thấy phiên tải hoặc đã xong.")
    return body


_VISION_MAX_BYTES = 2 * 1024 * 1024


def vision_partial_path(partial: Path, upload_id: str) -> Path:
    return partial.with_name(f"{upload_id}.vision.json")


def relocate_vision_sidecar(sidecar: Path, final: Path) -> None:
    """Đưa chữ iPhone đọc sang cạnh file video đã vào hàng: {id}.vision.json."""
    if not sidecar.is_file():
        return
    os.replace(sidecar, final.with_suffix(".vision.json"))


def save_vision_payload(db_path: Path, upload_id: str, payload: bytes) -> dict[str, object]:
    """Lưu JSON Vision cạnh file partial. Không thay lần quét 30/60 của máy chủ."""
    ensure_upload_tables(db_path)
    if len(payload) > _VISION_MAX_BYTES:
        raise ValueError("Sidecar Vision quá 2MB")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Sidecar không phải JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        raise ValueError("Sidecar thiếu danh sách frames")
    frames = data["frames"]
    if len(frames) > 120:
        raise ValueError("Sidecar quá nhiều khung")

    def _load():
        with uploads_connect(db_path) as conn:
            return conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()

    row = _retry_locked(_load, label="save_vision")
    if row is None or row["status"] != "receiving":
        raise LookupError("Không thấy phiên tải hoặc đã xong.")
    path = vision_partial_path(Path(str(row["path"])), upload_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)
    return {"ok": True, "bytes": len(payload), "frames": len(frames)}


def complete_upload(
    db_path: Path,
    video_dir: Path,
    upload_id: str,
    *,
    disk_limit_bytes: int,
    video_db_path: Path | None = None,
    allow_repeat: bool = False,
) -> dict[str, object]:
    ensure_upload_tables(db_path)
    video_db = video_db_path or db_path

    def _load():
        with uploads_connect(db_path) as conn:
            return conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,)).fetchone()

    row = _retry_locked(_load, label="complete_load")
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
        vision_partial_path(path, upload_id).unlink(missing_ok=True)

        def _delete() -> None:
            with uploads_connect(db_path) as conn:
                conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))

        _retry_locked(_delete, label="complete_reject_delete")
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
    used = queued_bytes(video_db)
    if not can_accept(used, size_bytes, disk_limit_bytes, free):
        raise MemoryError("Hàng đợi đã đầy. Đợi bớt video rồi thêm tiếp.")

    created = begin_upload(
        video_db,
        name=str(row["name"]),
        size_bytes=size_bytes,
        sha256=digest,
        device=str(row["device"] or ""),
        allow_repeat=allow_repeat,
    )
    partial = Path(str(row["path"]))
    sidecar = vision_partial_path(partial, upload_id)
    if created.get("duplicate"):
        path.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)

        def _mark_dup() -> None:
            with uploads_connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE upload_sessions
                    SET status = 'completed', sha256 = ?, received_bytes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (digest, size_bytes, utcnow(), upload_id),
                )

        _retry_locked(_mark_dup, label="complete_dup")
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
        commit_upload(video_db, video_id, str(final), duration)
    except Exception:
        abort_upload(video_db, video_id, bool(created.get("reopened")))
        final.unlink(missing_ok=True)
        raise
    try:
        relocate_vision_sidecar(sidecar, final)
    except OSError as exc:
        print(f"vision sidecar: {exc}", flush=True)

    def _mark_done() -> None:
        with uploads_connect(db_path) as conn:
            conn.execute(
                """
                UPDATE upload_sessions
                SET status = 'completed', sha256 = ?, path = ?, received_bytes = ?, updated_at = ?
                WHERE id = ?
                """,
                (digest, str(final), size_bytes, utcnow(), upload_id),
            )

    _retry_locked(_mark_done, label="complete_done")
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

    def _abort():
        with uploads_connect(db_path) as conn:
            row = conn.execute(
                "SELECT path, status FROM upload_sessions WHERE id = ?", (upload_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))
            return row

    row = _retry_locked(_abort, label="abort_session")
    if row is None:
        return False
    # Phiên đã xong: path là file trong hàng đọc. Hủy phiên không được xóa video đó.
    if row["status"] == "receiving":
        Path(str(row["path"])).unlink(missing_ok=True)
    return True
