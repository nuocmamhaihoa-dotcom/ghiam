"""Hàng đợi video và kết quả đã đọc.

Ghi bằng SQLite WAL, synchronous=FULL: mất điện vẫn giữ các dòng đã commit.
Mỗi video chỉ vài chục dòng, nên 15 tiến trình đọc không nghẽn ở chỗ ghi.
Một dòng khoảng vài trăm byte. Một trăm triệu dòng nằm trong vài chục GB.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from control_plane.screen_table import (
    COVER_REASON,
    HANDLE_CONF_MIN,
    REASON_LOW_CONF,
    Review,
    Table,
    _compact,
    _handles_are_same,
    _prefer_handle,
    clean_name,
    clean_username,
    combine_similar_names,
    conflicts_with_other_person,
    handle_matches_name,
    is_profile_handle,
    join_similar_leftovers,
    joined_name,
    repair_table,
    normalize_phone,
    pair_close_names,
    pair_names_alike_90,
    phone_distance,
    same_person_name,
)

# Đủ = một hàng có đủ số + tên + @. Cần xem = số có trong danh bạ nhưng thiếu tên hoặc @.
# Lỗi quét = không thành một hàng (mở hồ sơ mà không thấy số, trùng tên, một số mở nhiều hồ sơ).
BUCKET_OK = 1
BUCKET_REVIEW = 2
BUCKET_SCAN = 3
BUCKET_UNOPENED = BUCKET_SCAN
BUCKET_LABEL = {BUCKET_OK: "Đủ", BUCKET_REVIEW: "Cần xem", BUCKET_SCAN: "Lỗi quét"}
REASON_MULTI = "một số mở nhiều hồ sơ"
REASON_NO_HANDLE = "có số trong danh bạ nhưng chưa đọc được @"
REASON_NO_NAME = "có số trong danh bạ nhưng chưa đọc được tên"
REASON_NO_PHONE = "đã mở hồ sơ nhưng chưa thấy số"
_COLLAPSE_KEY = "ocr_collapse_v1"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def vn_day_bounds(now: datetime | None = None) -> tuple[str, str]:
    """Mốc đầu và cuối ngày theo giờ Việt Nam, dạng UTC giống cột thời gian trong DB."""
    vn = timezone(timedelta(hours=7))
    current = now.astimezone(vn) if now is not None else datetime.now(vn)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def _day_filter(day: str, column: str) -> tuple[str, tuple[str, ...]]:
    kind = (day or "all").strip().lower()
    if kind not in ("today", "older"):
        return "1=1", ()
    start, end = vn_day_bounds()
    if kind == "today":
        return f"{column} >= ? AND {column} < ?", (start, end)
    return f"{column} < ?", (start,)


def _video_day_sql(day: str) -> tuple[str, tuple[str, ...]]:
    """Video làm trong ngày: tải lên, bắt đầu đọc, đang đọc hoặc đọc xong trong ngày đó."""
    kind = (day or "all").strip().lower()
    if kind not in ("today", "older"):
        return "", ()
    start, end = vn_day_bounds()
    touched = """(
        (created_at >= ? AND created_at < ?)
        OR (started_at >= ? AND started_at < ?)
        OR (finished_at >= ? AND finished_at < ?)
        OR (progress_at >= ? AND progress_at < ?)
    )"""
    params = (start, end, start, end, start, end, start, end)
    if kind == "today":
        return touched, params
    return f"NOT {touched}", params


def worker_count() -> int:
    """Số tiến trình OCR tổng. Mặc định dùng hết CPU."""
    raw = os.environ.get("CONTROL_VIDEO_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    return max(1, os.cpu_count() or 2)


def feeder_count() -> int:
    """Số video đọc cùng lúc.

    Mỗi video một nhóm tiến trình riêng. Mặc định 2 khi máy ≥8 CPU và ≥16GB RAM
    (tận dụng VPS); đặt CONTROL_VIDEO_FEEDERS=1 để xếp hàng tuần tự.
    """
    raw = os.environ.get("CONTROL_VIDEO_FEEDERS", "").strip()
    if raw:
        return max(1, int(raw))
    cpus = os.cpu_count() or 2
    try:
        # MemAvailable (kB) trên Linux; thiếu thì coi như đủ nếu không đọc được.
        mem_kb = 0
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                mem_kb = int(line.split()[1])
                break
        mem_gb = mem_kb / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        mem_gb = 32.0
    if cpus >= 8 and mem_gb >= 16:
        return 2
    return 1


def keep_video_count() -> int:
    """Số video mới nhất giữ lại trên đĩa để bấm Đọc lại.

    Video 800MB–1.5GB: mặc định 8 (không giữ 50×1.5GB = 75GB).
    """
    raw = os.environ.get("CONTROL_VIDEO_KEEP", "").strip()
    if raw:
        return max(0, int(raw))
    return 8


def keep_video_gb() -> float:
    """Trần dung lượng file đã đọc xong còn giữ (GB). Mặc định 12GB."""
    raw = os.environ.get("CONTROL_VIDEO_KEEP_GB", "").strip()
    if raw:
        return max(0.0, float(raw))
    return 12.0


def free_reserve_bytes() -> int:
    """Luôn chừa chỗ trống tối thiểu trên đĩa (để nhiều máy up song song)."""
    raw = os.environ.get("CONTROL_VIDEO_FREE_RESERVE_GB", "").strip()
    gb = float(raw) if raw else 20.0
    return int(max(4.0, gb) * 1024 * 1024 * 1024)


def ocr_pause_free_bytes() -> int:
    """Còn ít trống hơn mức này thì tạm dừng nhận job OCR mới (nhường upload)."""
    raw = os.environ.get("CONTROL_VIDEO_OCR_PAUSE_FREE_GB", "").strip()
    gb = float(raw) if raw else 25.0
    return int(max(8.0, gb) * 1024 * 1024 * 1024)


def stale_quiet_sec() -> float:
    """Không có tiến độ mới và không còn tiến trình con thì mới coi là nghẽn."""
    raw = os.environ.get("CONTROL_VIDEO_STALE_SEC", "").strip()
    if raw:
        return max(60.0, float(raw))
    # OCR chết im (vd treo ở 6000 khung) cần được phát hiện sớm hơn video đang ffmpeg dài.
    return 900.0


def stale_max_sec() -> float:
    """Trần tuyệt đối; video rất dài (hàng chục nghìn khung) vẫn được đọc hết một lần."""
    raw = os.environ.get("CONTROL_VIDEO_MAX_SEC", "").strip()
    if raw:
        return max(stale_quiet_sec(), float(raw))
    return 24 * 3600.0


def _stat_after_comm(stat_text: str) -> list[str] | None:
    """Phần sau tên trong /proc/pid/stat. Tên có thể chứa dấu cách."""
    end = stat_text.rfind(")")
    if end < 0:
        return None
    parts = stat_text[end + 2 :].split()
    return parts or None


def _child_is_working(stat_text: str, cmdline: str) -> bool:
    """ffmpeg, hoặc tiến trình đang chạy/chờ đĩa.

    Pool OCR nằm ngủ không tính. Pool luôn sống dù video đã kẹt — nếu tính cả tiến trình ngủ
    thì video im tiến độ không bao giờ được xếp lại.
    """
    if "resource_tracker" in cmdline:
        return False
    parts = _stat_after_comm(stat_text)
    if not parts:
        return False
    if "ffmpeg" in cmdline:
        return True
    return parts[0] in {"R", "D"}


def worker_has_live_children(pid: int | None) -> bool:
    """Còn ffmpeg hoặc OCR đang chạy thật — không phải pool nằm chờ việc."""
    if not pid or not _alive(pid):
        return False
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
                cmd = (entry / "cmdline").read_text(errors="replace")
            except OSError:
                continue
            parts = _stat_after_comm(stat)
            if parts is None or len(parts) < 2:
                continue
            try:
                ppid = int(parts[1])
            except ValueError:
                continue
            if ppid != int(pid):
                continue
            if _child_is_working(stat, cmd):
                return True
    except OSError:
        return False
    return False


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-65536;")
    try:
        yield conn
    finally:
        conn.close()


def _is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _with_immediate(db_path: Path, action, *, tries: int = 20):
    """Chạy action trong BEGIN IMMEDIATE ngắn; retry khi DB bận."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with connect(db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = action(conn)
                    conn.execute("COMMIT")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
            return result
        except sqlite3.OperationalError as exc:
            last = exc
            if not _is_lock_error(exc):
                raise
            time.sleep(min(4.0, 0.05 * (2 ** min(attempt, 6))))
    if last is not None:
        raise last
    raise sqlite3.OperationalError("database is locked")


_READY: set[str] = set()
_MERGE_BATCH = 40


def init_db(db_path: Path) -> None:
    """Tạo bảng một lần cho mỗi tiến trình.

    result_counts giữ số dòng theo loại, do trigger cập nhật trong cùng giao
    dịch, để trang thống kê không phải đếm lại cả bảng mỗi vài giây.

    DB đã có thì chỉ migrate cột — tránh BEGIN IMMEDIATE dài (dễ lock hub/worker
    khi đang upload/OCR).
    """
    key = str(Path(db_path).resolve())
    if key in _READY and Path(db_path).exists():
        return
    last_exc: Exception | None = None
    for attempt in range(12):
        try:
            with connect(db_path) as conn:
                has_videos = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'videos'"
                ).fetchone()
                if has_videos is None:
                    conn.executescript(
                        """
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS videos (
                          id INTEGER PRIMARY KEY,
                          name TEXT NOT NULL,
                          size_bytes INTEGER NOT NULL,
                          sha256 TEXT NOT NULL,
                          path TEXT NOT NULL DEFAULT '',
                          status TEXT NOT NULL,
                          error TEXT NOT NULL DEFAULT '',
                          pid INTEGER,
                          created_at TEXT NOT NULL,
                          started_at TEXT,
                          finished_at TEXT,
                          device TEXT NOT NULL DEFAULT '',
                          progress TEXT NOT NULL DEFAULT '',
                          progress_at TEXT,
                          duration_sec REAL NOT NULL DEFAULT 0
                        );
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_sha ON videos(sha256);
                        CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status, id);
                        CREATE TABLE IF NOT EXISTS results (
                          id INTEGER PRIMARY KEY,
                          phone TEXT NOT NULL DEFAULT '',
                          name TEXT NOT NULL DEFAULT '',
                          username TEXT NOT NULL DEFAULT '',
                          bucket INTEGER NOT NULL,
                          reason TEXT NOT NULL DEFAULT '',
                          video_id INTEGER NOT NULL,
                          created_at TEXT NOT NULL
                        );
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_results_phone ON results(phone) WHERE phone != '';
                        CREATE INDEX IF NOT EXISTS idx_results_bucket ON results(bucket, id);
                        CREATE INDEX IF NOT EXISTS idx_results_username ON results(username) WHERE username != '';
                        CREATE INDEX IF NOT EXISTS idx_results_video ON results(video_id, id);
                        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS result_counts (bucket INTEGER PRIMARY KEY, n INTEGER NOT NULL);
                        INSERT INTO result_counts (bucket, n)
                          SELECT bucket, COUNT(*) FROM results
                          WHERE NOT EXISTS (SELECT 1 FROM result_counts)
                          GROUP BY bucket;
                        CREATE TRIGGER IF NOT EXISTS results_count_insert AFTER INSERT ON results BEGIN
                          INSERT INTO result_counts (bucket, n) VALUES (NEW.bucket, 1)
                            ON CONFLICT(bucket) DO UPDATE SET n = n + 1;
                        END;
                        CREATE TRIGGER IF NOT EXISTS results_count_delete AFTER DELETE ON results BEGIN
                          UPDATE result_counts SET n = n - 1 WHERE bucket = OLD.bucket;
                        END;
                        CREATE TRIGGER IF NOT EXISTS results_count_update AFTER UPDATE OF bucket ON results
                        WHEN OLD.bucket != NEW.bucket BEGIN
                          UPDATE result_counts SET n = n - 1 WHERE bucket = OLD.bucket;
                          INSERT INTO result_counts (bucket, n) VALUES (NEW.bucket, 1)
                            ON CONFLICT(bucket) DO UPDATE SET n = n + 1;
                        END;
                        COMMIT;
                        """
                    )
                else:
                    _ensure_schema_objects(conn)
                _ensure_video_columns(conn)
                _reclassify_results(conn)
                _collapse_ocr_fragments(conn)
                _reconcile_result_counts(conn)
            _READY.add(key)
            return
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(min(8.0, 0.4 * (attempt + 1)))
    if last_exc is not None:
        raise last_exc


def _ensure_schema_objects(conn: sqlite3.Connection) -> None:
    """Bổ sung index/trigger/bảng phụ trên DB đã có — không khóa IMMEDIATE cả khối."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS result_counts (bucket INTEGER PRIMARY KEY, n INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_sha ON videos(sha256);
        CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status, id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_results_phone ON results(phone) WHERE phone != '';
        CREATE INDEX IF NOT EXISTS idx_results_bucket ON results(bucket, id);
        CREATE INDEX IF NOT EXISTS idx_results_username ON results(username) WHERE username != '';
        CREATE INDEX IF NOT EXISTS idx_results_video ON results(video_id, id);
        CREATE TRIGGER IF NOT EXISTS results_count_insert AFTER INSERT ON results BEGIN
          INSERT INTO result_counts (bucket, n) VALUES (NEW.bucket, 1)
            ON CONFLICT(bucket) DO UPDATE SET n = n + 1;
        END;
        CREATE TRIGGER IF NOT EXISTS results_count_delete AFTER DELETE ON results BEGIN
          UPDATE result_counts SET n = n - 1 WHERE bucket = OLD.bucket;
        END;
        CREATE TRIGGER IF NOT EXISTS results_count_update AFTER UPDATE OF bucket ON results
        WHEN OLD.bucket != NEW.bucket BEGIN
          UPDATE result_counts SET n = n - 1 WHERE bucket = OLD.bucket;
          INSERT INTO result_counts (bucket, n) VALUES (NEW.bucket, 1)
            ON CONFLICT(bucket) DO UPDATE SET n = n + 1;
        END;
        """
    )


def _ensure_video_columns(conn: sqlite3.Connection) -> None:
    """Thêm cột mới cho DB cũ. An toàn khi nhiều tiến trình khởi động cùng lúc."""
    video_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(videos)")}
    for name, ddl in (
        ("device", "ALTER TABLE videos ADD COLUMN device TEXT NOT NULL DEFAULT ''"),
        ("progress", "ALTER TABLE videos ADD COLUMN progress TEXT NOT NULL DEFAULT ''"),
        ("progress_at", "ALTER TABLE videos ADD COLUMN progress_at TEXT"),
        ("duration_sec", "ALTER TABLE videos ADD COLUMN duration_sec REAL NOT NULL DEFAULT 0"),
    ):
        if name in video_cols:
            continue
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    result_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(results)")}
    for name, ddl in (
        ("phone_mask", "ALTER TABLE results ADD COLUMN phone_mask TEXT NOT NULL DEFAULT ''"),
        ("shots", "ALTER TABLE results ADD COLUMN shots TEXT NOT NULL DEFAULT ''"),
    ):
        if name in result_cols:
            continue
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def classify_result(
    phone: str,
    name: str,
    username: str,
    reason: str = "",
    *,
    handle_confidence: float = -1.0,
) -> tuple[int, str]:
    """Xếp một hàng theo đúng 3 cột. Không đoán thêm.

    Đủ: số, tên và @ đều có, và không phải một số mở nhiều hồ sơ.
    Cần xem: có số trong danh bạ nhưng thiếu tên hoặc @, hoặc @ điểm thấp.
    Lỗi quét: không có số, hoặc một số gắn với nhiều hồ sơ.
    """
    phone = (phone or "").strip()
    name = (name or "").strip()
    username = (username or "").strip()
    reason = (reason or "").strip()
    if reason == REASON_MULTI:
        return BUCKET_SCAN, REASON_MULTI
    if phone and name and username:
        if reason == REASON_LOW_CONF or 0 <= handle_confidence < HANDLE_CONF_MIN:
            return BUCKET_REVIEW, REASON_LOW_CONF
        return BUCKET_OK, ""
    if not phone and reason == COVER_REASON:
        return BUCKET_REVIEW, COVER_REASON
    if phone:
        if not reason:
            reason = REASON_NO_HANDLE if not username else REASON_NO_NAME
        return BUCKET_REVIEW, reason
    if not reason:
        reason = REASON_NO_PHONE
    return BUCKET_SCAN, reason


def _reclassify_results(conn: sqlite3.Connection) -> None:
    """Sửa hàng cũ cho khớp định nghĩa đủ / cần xem / lỗi quét. Chạy lại thì không đổi hàng đã đúng."""
    conn.execute(
        "UPDATE results SET bucket = ? WHERE reason = ? AND bucket != ?",
        (BUCKET_SCAN, REASON_MULTI, BUCKET_SCAN),
    )
    conn.execute(
        """
        UPDATE results
        SET bucket = ?, reason = ''
        WHERE phone != '' AND name != '' AND username != ''
          AND reason != ?
          AND reason != ?
          AND (bucket != ? OR reason != '')
        """,
        (BUCKET_OK, REASON_MULTI, REASON_LOW_CONF, BUCKET_OK),
    )
    conn.execute(
        """
        UPDATE results
        SET bucket = ?,
            reason = CASE
              WHEN reason != '' THEN reason
              WHEN username = '' THEN ?
              ELSE ?
            END
        WHERE phone != '' AND (name = '' OR username = '')
          AND (bucket != ? OR reason = '')
        """,
        (BUCKET_REVIEW, REASON_NO_HANDLE, REASON_NO_NAME, BUCKET_REVIEW),
    )
    conn.execute(
        """
        UPDATE results
        SET bucket = ?,
            reason = CASE WHEN reason != '' THEN reason ELSE ? END
        WHERE phone = '' AND reason != ? AND (bucket != ? OR reason = '')
        """,
        (BUCKET_SCAN, REASON_NO_PHONE, COVER_REASON, BUCKET_SCAN),
    )
    # @ đã đọc mà không gắn được số thì giữ, để hàng kết quả còn ảnh dải chữ.
    # Không ghép @ đó sang video khác.



def _person_key(name: str) -> str:
    """Khóa gom OCR lệch của một người. Tên quá ngắn thì không gom."""
    compact = _compact(name)
    if len(compact) >= 4:
        return compact
    return ""


def _best_orphan(group: list[sqlite3.Row]) -> sqlite3.Row:
    name = max((str(row["name"] or "") for row in group), key=len)
    handles = [str(row["username"]) for row in group if row["username"]]
    counts: dict[str, int] = {}
    for handle in handles:
        counts[handle] = counts.get(handle, 0) + 1
    best_handle = _prefer_handle(handles, name, counts)
    candidates = [row for row in group if str(row["username"] or "") == best_handle] or list(group)
    return min(candidates, key=lambda row: int(row["id"]))


def _collapse_video_fragments(conn: sqlite3.Connection, video_id: int) -> None:
    """Trong một video: gom @ lệch cùng tên, gắn vào đúng một số, xóa bản đọc trùng."""
    rows = list(
        conn.execute(
            "SELECT id, phone, name, username, bucket, reason FROM results WHERE video_id = ?",
            (video_id,),
        )
    )
    if not rows:
        return
    phones = [row for row in rows if str(row["phone"] or "").strip()]
    orphans = [
        row
        for row in rows
        if not str(row["phone"] or "").strip() and str(row["username"] or "").strip()
    ]
    groups: dict[str, list[sqlite3.Row]] = {}
    solo: list[sqlite3.Row] = []
    for row in orphans:
        key = _person_key(str(row["name"] or ""))
        if not key:
            solo.append(row)
            continue
        groups.setdefault(key, []).append(row)

    delete_ids: set[int] = set()
    kept: list[sqlite3.Row] = []
    for group in groups.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        best = _best_orphan(group)
        kept.append(best)
        for row in group:
            if int(row["id"]) != int(best["id"]):
                delete_ids.add(int(row["id"]))
    kept.extend(solo)

    phones_by_key: dict[str, list[sqlite3.Row]] = {}
    for row in phones:
        key = _person_key(str(row["name"] or ""))
        if key:
            phones_by_key.setdefault(key, []).append(row)
    orphans_by_key: dict[str, list[sqlite3.Row]] = {}
    for row in kept:
        key = _person_key(str(row["name"] or ""))
        if key:
            orphans_by_key.setdefault(key, []).append(row)

    updates: list[tuple[str, str, int, str, int]] = []
    updated_phones: set[int] = set()

    def attach(phone: sqlite3.Row, orphan: sqlite3.Row, username: str) -> None:
        display = joined_name(str(phone["name"] or ""), str(orphan["name"] or ""))
        display = display or str(phone["name"] or "") or str(orphan["name"] or "")
        bucket, reason = classify_result(str(phone["phone"] or ""), display, username, "")
        updates.append((display, username, bucket, reason, int(phone["id"])))
        updated_phones.add(int(phone["id"]))
        delete_ids.add(int(orphan["id"]))

    for key, orphan_rows in orphans_by_key.items():
        phone_rows = phones_by_key.get(key, [])
        if len(phone_rows) != 1 or len(orphan_rows) != 1:
            continue
        phone = phone_rows[0]
        orphan = orphan_rows[0]
        if int(orphan["id"]) in delete_ids:
            continue
        phone_user = str(phone["username"] or "").strip()
        orphan_user = str(orphan["username"] or "").strip()
        if not orphan_user:
            continue
        if not phone_user:
            attach(phone, orphan, orphan_user)
            continue
        if _handles_are_same(phone_user, orphan_user):
            delete_ids.add(int(orphan["id"]))
            continue
        phone_name = str(phone["name"] or "")
        if handle_matches_name(orphan_user, phone_name) and not handle_matches_name(phone_user, phone_name):
            attach(phone, orphan, orphan_user)

    remaining_phones = [
        row
        for row in phones
        if not str(row["username"] or "").strip() and int(row["id"]) not in updated_phones
    ]
    remaining_orphans = [row for row in kept if int(row["id"]) not in delete_ids]
    phone_hits: dict[int, list[sqlite3.Row]] = {}
    orphan_hits: dict[int, list[sqlite3.Row]] = {}
    for phone in remaining_phones:
        for orphan in remaining_orphans:
            if handle_matches_name(str(orphan["username"] or ""), str(phone["name"] or "")):
                phone_hits.setdefault(int(phone["id"]), []).append(orphan)
                orphan_hits.setdefault(int(orphan["id"]), []).append(phone)
    for phone in remaining_phones:
        hits = phone_hits.get(int(phone["id"]), [])
        if len(hits) != 1:
            continue
        orphan = hits[0]
        if len(orphan_hits.get(int(orphan["id"]), [])) != 1:
            continue
        if int(orphan["id"]) in delete_ids:
            continue
        attach(phone, orphan, str(orphan["username"] or ""))

    phoned_users: list[str] = []
    for row in phones:
        user = str(row["username"] or "").strip()
        if user and int(row["id"]) not in updated_phones:
            phoned_users.append(user)
    for _name, username, _bucket, _reason, _rid in updates:
        if username:
            phoned_users.append(username)
    for orphan in kept:
        oid = int(orphan["id"])
        if oid in delete_ids:
            continue
        user = str(orphan["username"] or "").strip()
        if user and any(_handles_are_same(user, other) for other in phoned_users):
            delete_ids.add(oid)

    for name, username, bucket, reason, rid in updates:
        conn.execute(
            "UPDATE results SET name = ?, username = ?, bucket = ?, reason = ? WHERE id = ?",
            (name, username, bucket, reason, rid),
        )
    if delete_ids:
        conn.executemany(
            "DELETE FROM results WHERE id = ? AND phone = ''",
            [(item,) for item in delete_ids],
        )


def _collapse_ocr_fragments(conn: sqlite3.Connection) -> None:
    """Gom dòng lỗi do OCR đọc lệch một hồ sơ. Chạy một lần; gọi lại thì không đổi."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        done = conn.execute("SELECT 1 FROM meta WHERE key = ?", (_COLLAPSE_KEY,)).fetchone()
        if done:
            conn.execute("COMMIT")
            return
        conn.execute(
            """
            UPDATE results
            SET bucket = ?, reason = ''
            WHERE reason = ? AND phone != '' AND name != '' AND username != ''
            """,
            (BUCKET_OK, REASON_MULTI),
        )
        video_ids = [int(row[0]) for row in conn.execute("SELECT DISTINCT video_id FROM results")]
        for video_id in video_ids:
            _collapse_video_fragments(conn, video_id)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            (_COLLAPSE_KEY, utcnow()),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _reconcile_result_counts(conn: sqlite3.Connection) -> None:
    """Nếu bộ đếm lệch so với bảng results (hiếm) thì đếm lại ngay."""
    cached = {int(row["bucket"]): int(row["n"]) for row in conn.execute("SELECT bucket, n FROM result_counts")}
    real = {int(row["bucket"]): int(row["n"]) for row in conn.execute("SELECT bucket, COUNT(*) AS n FROM results GROUP BY bucket")}
    if cached == real:
        return
    conn.execute("DELETE FROM result_counts")
    for bucket, n in real.items():
        conn.execute("INSERT INTO result_counts (bucket, n) VALUES (?, ?)", (bucket, n))


def _incoming_bytes_from(db_path: Path) -> int:
    try:
        with connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
            ).fetchone()
            if exists is None:
                return 0
            row = conn.execute(
                """
                SELECT COALESCE(SUM(received_bytes), 0) AS n
                FROM upload_sessions WHERE status = 'receiving'
                """
            ).fetchone()
        return int(row["n"])
    except sqlite3.Error:
        return 0


def _receiving_count_from(db_path: Path, *, fresh_sec: int = 20 * 60) -> int:
    """Đếm phiên receiving còn cập nhật trong fresh_sec giây."""
    try:
        with connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
            ).fetchone()
            if exists is None:
                return 0
            rows = conn.execute(
                "SELECT updated_at FROM upload_sessions WHERE status = 'receiving'"
            ).fetchall()
        cutoff = time.time() - max(30, fresh_sec)
        n = 0
        for row in rows:
            text = str(row["updated_at"] or "").replace("Z", "+00:00")
            try:
                updated = datetime.fromisoformat(text).timestamp()
            except ValueError:
                updated = 0.0
            if updated and updated >= cutoff:
                n += 1
        return n
    except sqlite3.Error:
        return 0


def _uploads_db_paths(video_db: Path) -> list[Path]:
    """video.db cũ + video_uploads.db mới (nếu tách)."""
    paths = [Path(video_db)]
    try:
        from control_plane.settings import settings

        uploads = Path(settings.video_uploads_db_path)
        if uploads.resolve() != Path(video_db).resolve():
            paths.append(uploads)
    except Exception:
        pass
    return paths


def incoming_bytes(db_path: Path) -> int:
    """Dung lượng phiên cắt khúc đang nhận (incoming/*.part)."""
    return sum(_incoming_bytes_from(path) for path in _uploads_db_paths(db_path))


def queued_bytes(db_path: Path) -> int:
    """Dung lượng video còn nằm trên đĩa (hàng đợi + giữ + đang tải cắt khúc)."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0) AS n FROM videos
            WHERE status IN ('uploading', 'queued', 'running')
               OR (status IN ('done', 'error') AND path != '')
            """
        ).fetchone()
    return int(row["n"]) + incoming_bytes(db_path)


def can_accept(used_bytes: int, new_bytes: int, limit_bytes: int, free_bytes: int) -> bool:
    """Chấp nhận video mới khi còn hạn mức và còn chừa chỗ trống an toàn."""
    if new_bytes <= 0:
        return False
    if new_bytes > limit_bytes or used_bytes + new_bytes > limit_bytes:
        return False
    # Chừa reserve + chính file mới (nhiều iPhone up song song không đụng trần đĩa).
    return free_bytes > new_bytes + free_reserve_bytes()


def uploads_are_flowing(db_path: Path, *, fresh_sec: int = 45) -> bool:
    """Có phiên vừa nhận byte. Dùng để nhường CPU/đĩa cho đường tải."""
    return upload_io_mode(db_path, flow_sec=fresh_sec) == "flow"


def upload_session_mode(db_path: Path, *, flow_sec: int = 45, arm_sec: int = 25) -> str:
    """Một DB phiên: off | arm (mới mở, chưa có byte) | flow (đang có byte)."""
    try:
        with connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
            ).fetchone()
            if exists is None:
                return "off"
            rows = conn.execute(
                """
                SELECT received_bytes, created_at, updated_at
                FROM upload_sessions WHERE status = 'receiving'
                """
            ).fetchall()
    except sqlite3.Error:
        return "off"
    now = time.time()
    mode = "off"
    for row in rows:
        received = int(row["received_bytes"] or 0)
        updated = _parse_ts(row["updated_at"]) or 0.0
        created = _parse_ts(row["created_at"]) or updated
        if received > 0 and updated >= now - max(5, flow_sec):
            return "flow"
        if received <= 0 and created >= now - max(5, arm_sec):
            mode = "arm"
    return mode


def upload_io_mode(db_path: Path, *, flow_sec: int = 45, arm_sec: int = 25) -> str:
    """off khi không tải. arm khi phiên vừa tạo mà chưa có byte. flow khi đang nhận byte.

    arm chỉ kéo dài vài chục giây để mảnh đầu không kẹt sau OCR. Hết giờ mà vẫn
    0 byte thì đọc lấy lại hết máy — không nhường mãi cho phiên ma.
    """
    mode = "off"
    for path in _uploads_db_paths(db_path):
        kind = upload_session_mode(path, flow_sec=flow_sec, arm_sec=arm_sec)
        if kind == "flow":
            return "flow"
        if kind == "arm":
            mode = "arm"
    return mode


def should_pause_ocr(db_path: Path, video_dir: Path) -> bool:
    """Tạm không nhận video OCR mới khi đĩa căng hoặc đang có upload."""
    try:
        free = shutil.disk_usage(video_dir).free
    except OSError:
        return False
    if free < ocr_pause_free_bytes():
        return True
    # Mặc định 0: không dừng hàng đọc chỉ vì đang tải. Upload được dành vài nhân
    # (xem video_worker). Đặt CONTROL_VIDEO_OCR_PAUSE_UPLOADS=N để dừng nhận job mới
    # khi có từ N phiên đang nhận byte.
    pause_at = int(os.environ.get("CONTROL_VIDEO_OCR_PAUSE_UPLOADS", "0") or "0")
    if pause_at <= 0:
        return False
    receiving = sum(_receiving_count_from(path, fresh_sec=120) for path in _uploads_db_paths(db_path))
    return receiving >= pause_at


def clean_device(device: str) -> str:
    return " ".join((device or "").split())[:80]


def begin_upload(
    db_path: Path,
    *,
    name: str,
    size_bytes: int,
    sha256: str,
    device: str = "",
    allow_repeat: bool = False,
) -> dict[str, object]:
    """Cùng một file đang chờ, đang đọc, hoặc đã xong thì bỏ qua.

    File lỗi được nhận lại để đọc lại. File đã xong giữ nguyên kết quả —
    muốn đọc lại thì bấm Đọc lại, không tải lại cả file.
    allow_repeat: bảng đọc bản mới nhận lại đúng file đó như video mới.
    """
    machine = clean_device(device)
    stored_sha = sha256
    if allow_repeat:
        stored_sha = f"{sha256}#fresh#{secrets.token_hex(4)}"

    def _begin(conn: sqlite3.Connection) -> dict[str, object]:
        existing = None
        if not allow_repeat:
            existing = conn.execute(
                "SELECT id, status, name, path FROM videos WHERE sha256 = ?", (sha256,)
            ).fetchone()
        if existing and existing["status"] == "error":
            conn.execute(
                """
                UPDATE videos
                SET name = ?, size_bytes = ?, path = '', status = 'uploading', error = '',
                    pid = NULL, started_at = NULL, finished_at = NULL, created_at = ?, device = ?,
                    progress = '', progress_at = NULL, duration_sec = 0
                WHERE id = ?
                """,
                (name, size_bytes, utcnow(), machine, existing["id"]),
            )
            return {
                "id": int(existing["id"]),
                "status": "uploading",
                "name": name,
                "device": machine,
                "duplicate": False,
                "reopened": True,
                "old_path": existing["path"],
            }
        if existing:
            return {
                "id": int(existing["id"]),
                "status": existing["status"],
                "name": existing["name"],
                "device": machine,
                "duplicate": True,
            }
        cur = conn.execute(
            """
            INSERT INTO videos (name, size_bytes, sha256, path, status, created_at, device)
            VALUES (?, ?, ?, '', 'uploading', ?, ?)
            """,
            (name, size_bytes, stored_sha, utcnow(), machine),
        )
        return {
            "id": int(cur.lastrowid),
            "status": "uploading",
            "name": name,
            "device": machine,
            "duplicate": False,
        }

    return _with_immediate(db_path, _begin)


def commit_upload(db_path: Path, video_id: int, path: str, duration_sec: float = 0.0) -> None:
    seconds = float(duration_sec or 0.0)
    if seconds < 0 or seconds != seconds:
        seconds = 0.0
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE videos
            SET path = ?, status = 'queued', duration_sec = ?
            WHERE id = ? AND status = 'uploading'
            """,
            (path, seconds, video_id),
        )


def set_duration(db_path: Path, video_id: int, duration_sec: float) -> None:
    """Ghi độ dài video (giây) — dùng khi upload hoặc worker đo lại."""
    seconds = float(duration_sec or 0.0)
    if seconds <= 0 or seconds != seconds:
        return
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET duration_sec = ? WHERE id = ? AND (duration_sec IS NULL OR duration_sec <= 0)",
            (seconds, video_id),
        )


def rescue_stuck_uploads(db_path: Path, video_dir: Path, *, min_age_sec: float = 600) -> int:
    """Video kẹt ở trạng thái đang tải: có file thì đưa vào hàng đọc, không file thì bỏ để nhường chỗ.

    Lúc complete bình thường chỉ kẹt vài giây. Quá min_age_sec mà vẫn uploading là lần trước chết
    giữa chừng — không để dòng đó chiếm hạn mức đĩa mãi.
    """
    video_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, path FROM videos WHERE status = 'uploading'"
        ).fetchall()
    fixed = 0
    for row in rows:
        created = _parse_ts(row["created_at"])
        if created is None or now - created < min_age_sec:
            continue
        video_id = int(row["id"])
        candidate = Path(str(row["path"] or ""))
        if not candidate.is_file():
            suffix = Path(str(row["name"] or "")).suffix.lower() or ".mp4"
            guess = video_dir / f"{video_id}{suffix}"
            candidate = guess if guess.is_file() else Path()
        if candidate.is_file() and candidate.stat().st_size > 0:
            commit_upload(db_path, video_id, str(candidate))
            fixed += 1
            continue
        with connect(db_path) as conn:
            conn.execute(
                "DELETE FROM videos WHERE id = ? AND status = 'uploading' AND (path = '' OR path IS NULL)",
                (video_id,),
            )
        fixed += 1
    return fixed


def abort_upload(db_path: Path, video_id: int, reopened: bool = False) -> None:
    with connect(db_path) as conn:
        if reopened:
            conn.execute(
                "UPDATE videos SET status = 'error', error = ? WHERE id = ? AND status = 'uploading'",
                ("Tải lên bị gián đoạn. Tải lại video.", video_id),
            )
            return
        conn.execute("DELETE FROM videos WHERE id = ? AND status = 'uploading'", (video_id,))


_VIDEO_LIST_SQL = """
            SELECT
              v.id, v.name, v.size_bytes, v.status, v.error, v.created_at, v.started_at, v.finished_at,
              v.device, v.progress, v.progress_at, v.path,
              COALESCE(v.duration_sec, 0) AS duration_sec,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id), 0) AS result_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.bucket = 1), 0) AS saved_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.bucket = 2), 0) AS review_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.bucket = 3), 0) AS unopened_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.phone != ''), 0) AS phone_count
            FROM videos v
"""


def list_videos(
    db_path: Path,
    limit: int = 40,
    day: str = "all",
    offset: int = 0,
) -> list[dict[str, object]]:
    """Video mới hơn đứng trên. day=today chỉ video làm trong ngày, day=older là kho.

    Hàng chờ vẫn được đọc theo id tăng (vào trước đọc trước). Chỉ thứ tự hiện trên trang đổi.
    """
    day_sql, day_params = _video_day_sql(day)
    with connect(db_path) as conn:
        if day_sql:
            ids = [
                int(row["id"])
                for row in conn.execute(
                    f"""
                    SELECT id FROM videos
                    WHERE {day_sql}
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (*day_params, limit, max(0, int(offset))),
                )
            ]
        else:
            active_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM videos
                    WHERE status IN ('uploading', 'queued', 'running')
                    """
                )
            ]
            recent_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM videos
                    WHERE status IN ('done', 'error')
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ]
            seen = set(active_ids)
            ids = active_ids + [item for item in recent_ids if item not in seen]
            ids.sort(reverse=True)
        if not ids:
            rows = []
        else:
            marks = ",".join("?" for _ in ids)
            found = conn.execute(_VIDEO_LIST_SQL + f" WHERE v.id IN ({marks})", ids).fetchall()
            order = {video_id: index for index, video_id in enumerate(ids)}
            rows = sorted(found, key=lambda row: order[int(row["id"])])
    items: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        path = str(item.pop("path", "") or "")
        kept = False
        if path:
            try:
                st = Path(path).stat()
                # st_blocks==0 + size nhỏ = stub/sparse rỗng sau prune — không coi còn file.
                kept = Path(path).is_file() and st.st_size > 0 and (
                    st.st_size >= 64 * 1024 or st.st_blocks > 0
                )
            except OSError:
                kept = False
        item["file_kept"] = kept
        try:
            item["duration_sec"] = float(item.get("duration_sec") or 0)
        except (TypeError, ValueError):
            item["duration_sec"] = 0.0
        item["issue"] = _video_issue(item)
        item["error_summary"] = ""
        items.append(item)
    _attach_scan_errors(db_path, items)
    return items


def _attach_scan_errors(db_path: Path, items: list[dict[str, object]]) -> None:
    """Gắn tối đa 3 lý do lỗi quét của từng video để cột Kết quả nói rõ lỗi gì."""
    ids = [int(item["id"]) for item in items if item.get("id")]
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    grouped: dict[int, list[tuple[int, str]]] = {}
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT video_id, reason, COUNT(*) AS n
            FROM results
            WHERE video_id IN ({marks}) AND bucket = ? AND reason != ''
            GROUP BY video_id, reason
            """,
            (*ids, BUCKET_SCAN),
        ).fetchall()
    for row in rows:
        grouped.setdefault(int(row["video_id"]), []).append((int(row["n"]), str(row["reason"])))
    for item in items:
        reasons = grouped.get(int(item.get("id") or 0), [])
        reasons.sort(key=lambda pair: pair[0], reverse=True)
        item["error_summary"] = " · ".join(f"{reason} ×{count}" for count, reason in reasons[:3])


def count_videos(db_path: Path, day: str = "all") -> int:
    day_sql, params = _video_day_sql(day)
    with connect(db_path) as conn:
        if not day_sql:
            row = conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()
        else:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM videos WHERE {day_sql}",
                params,
            ).fetchone()
    return int(row["n"]) if row is not None else 0


def stats(db_path: Path) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "done": 0, "error": 0, "uploading": 0}
    buckets = {"saved": 0, "review": 0, "unopened": 0}
    with connect(db_path) as conn:
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM videos GROUP BY status"):
            if row["status"] in counts:
                counts[row["status"]] = int(row["n"])
        for row in conn.execute("SELECT bucket, n FROM result_counts"):
            if row["bucket"] == BUCKET_OK:
                buckets["saved"] = int(row["n"])
            elif row["bucket"] == BUCKET_REVIEW:
                buckets["review"] = int(row["n"])
            elif row["bucket"] == BUCKET_UNOPENED:
                buckets["unopened"] = int(row["n"])
        phones = conn.execute("SELECT COUNT(*) AS n FROM results WHERE phone != ''").fetchone()
        start, end = vn_day_bounds()
        today_saved = conn.execute(
            "SELECT COUNT(*) AS n FROM results WHERE bucket = ? AND created_at >= ? AND created_at < ?",
            (BUCKET_OK, start, end),
        ).fetchone()
        today_phones = conn.execute(
            "SELECT COUNT(*) AS n FROM results WHERE phone != '' AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()
        older_saved = conn.execute(
            "SELECT COUNT(*) AS n FROM results WHERE bucket = ? AND created_at < ?",
            (BUCKET_OK, start),
        ).fetchone()
        older_phones = conn.execute(
            "SELECT COUNT(*) AS n FROM results WHERE phone != '' AND created_at < ?",
            (start,),
        ).fetchone()
    counts.update(buckets)
    counts["phones"] = int(phones["n"]) if phones is not None else 0
    counts["results"] = buckets["saved"] + buckets["review"] + buckets["unopened"]
    counts["today_saved"] = int(today_saved["n"]) if today_saved is not None else 0
    counts["today_phones"] = int(today_phones["n"]) if today_phones is not None else 0
    counts["older_saved"] = int(older_saved["n"]) if older_saved is not None else 0
    counts["older_phones"] = int(older_phones["n"]) if older_phones is not None else 0
    return counts


def claim(db_path: Path, pid: int) -> dict[str, object] | None:
    """Nhận video tiếp theo. Retry khi DB bận — không để feeder chết vì locked."""

    def _claim(conn: sqlite3.Connection) -> dict[str, object] | None:
        row = conn.execute(
            "SELECT id, name, path, COALESCE(device, '') AS device "
            "FROM videos WHERE status = 'queued' AND path != '' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        now = utcnow()
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'running', pid = ?, started_at = ?, error = '',
                progress = 'Bắt đầu đọc', progress_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (pid, now, now, row["id"]),
        )
        if cur.rowcount != 1:
            return None
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "path": row["path"],
            "device": row["device"] or "",
        }

    return _with_immediate(db_path, _claim)


def set_progress(db_path: Path, video_id: int, message: str) -> None:
    text = " ".join((message or "").split())[:200]
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE videos
            SET progress = ?, progress_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (text, utcnow(), video_id),
        )


def requeue(db_path: Path, video_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE videos
            SET status = 'queued', pid = NULL, started_at = NULL, progress = '', progress_at = NULL
            WHERE id = ? AND status = 'running'
            """,
            (video_id,),
        )


def requeue_running(db_path: Path, keep_pid: int) -> int:
    """Lúc bộ đọc khởi động, mọi video đang đọc dở thuộc tiến trình cũ đã chết. Không dựa vào pid còn sống,
    vì sau khi khởi động lại máy, pid cũ có thể đã thuộc về chương trình khác."""
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'queued', pid = NULL, started_at = NULL, progress = '', progress_at = NULL
            WHERE status = 'running' AND (pid IS NULL OR pid != ?)
            """,
            (keep_pid,),
        )
        return cur.rowcount


def recover_dead(db_path: Path) -> int:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, pid FROM videos WHERE status = 'running'").fetchall()
        recovered = 0
        for row in rows:
            if _alive(row["pid"]):
                continue
            conn.execute(
                """
                UPDATE videos
                SET status = 'queued', pid = NULL, started_at = NULL, progress = '', progress_at = NULL
                WHERE id = ? AND status = 'running'
                """,
                (row["id"],),
            )
            recovered += 1
        return recovered


def stale_running(
    db_path: Path,
    *,
    quiet_sec: float | None = None,
    max_sec: float | None = None,
    now: float | None = None,
) -> list[int]:
    """Chỉ coi nghẽn khi im tiến độ VÀ không còn tiến trình con (ffmpeg/OCR).

    Trước đây restart khi im progress_at — video lớn đang tách hàng chục nghìn khung
    bị giết giữa chừng rồi đọc lại từ đầu mãi không xong.
    """
    quiet = stale_quiet_sec() if quiet_sec is None else quiet_sec
    limit = stale_max_sec() if max_sec is None else max_sec
    moment = time.time() if now is None else now
    stuck: list[int] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, pid, started_at, progress_at FROM videos WHERE status = 'running'"
        ).fetchall()
    for row in rows:
        pid = row["pid"]
        # Đang có ffmpeg / pool con → chắc chắn vẫn đọc, bỏ qua.
        if worker_has_live_children(int(pid) if pid is not None else None):
            continue
        started = _parse_ts(row["started_at"])
        progressed = _parse_ts(row["progress_at"]) or started
        if started is not None and moment - started >= limit:
            stuck.append(int(row["id"]))
            continue
        if progressed is not None and moment - progressed >= quiet:
            stuck.append(int(row["id"]))
    return stuck


def _parse_ts(value: object) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def finish(db_path: Path, video_id: int, table: Table) -> None:
    """Lưu kết quả rồi ghép số + @ còn thiếu trong cùng video.

    Bảng thường: tên giống từ 90% thì gộp vào một hàng trước khi ghi.
    Cách 3: hàng khớp tên đã chốt, cách 2 đã điền sót — không ghép lỏng 90%.
    Không ghép sang video khác.
    """
    repair_table(table)
    if not table.strict:
        join_similar_leftovers(table)

    def _write(conn: sqlite3.Connection) -> None:
        for row in table.rows:
            bucket, reason = classify_result(
                row.phone,
                row.name,
                row.username,
                "",
                handle_confidence=row.handle_confidence,
            )
            _put(
                conn,
                row.phone,
                row.name,
                row.username,
                bucket,
                reason,
                video_id,
                upgrade=True,
                phone_mask=row.phone_mask,
                shots=_shot_map(row),
            )
        for item in table.unopened:
            # Có số trong danh bạ, chưa có @ — cần xem, không phải lỗi "chưa mở".
            bucket, reason = classify_result(item.phone, item.name, "", "")
            _put(
                conn,
                item.phone,
                item.name,
                "",
                bucket,
                reason,
                video_id,
                upgrade=False,
                shots=_shot_map(item),
            )
        for item in table.review:
            _put_review(conn, item, video_id)
        conn.execute(
            """
            UPDATE videos
            SET status = 'done', pid = NULL, finished_at = ?, error = '',
                progress = '', progress_at = NULL
            WHERE id = ?
            """,
            (utcnow(), video_id),
        )

    _with_immediate(db_path, _write)
    if not table.strict:
        rematch_results(db_path, video_id)


def fail(db_path: Path, video_id: int, message: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE videos
            SET status = 'error', pid = NULL, finished_at = ?, error = ?,
                progress = '', progress_at = NULL
            WHERE id = ?
            """,
            (utcnow(), message[:500], video_id),
        )


def retry(db_path: Path, video_id: int) -> bool:
    """Xếp lại hàng khi file còn trên đĩa — lỗi hoặc đã xong (Đọc lại)."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT path, status FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None or row["status"] not in ("error", "done"):
            return False
        if not row["path"] or not Path(row["path"]).exists():
            return False
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'queued', error = '', finished_at = NULL, pid = NULL,
                progress = '', progress_at = NULL, started_at = NULL
            WHERE id = ? AND status IN ('error', 'done')
            """,
            (video_id,),
        )
        return cur.rowcount == 1


def clear_missing_video_paths(db_path: Path) -> int:
    """Xóa path trong DB khi file đã mất (lượt trước unlink sau khi đọc xong)."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, path, status FROM videos WHERE path != '' AND status IN ('done', 'error')"
        ).fetchall()
    cleared = 0
    for row in rows:
        if Path(str(row["path"])).exists():
            continue
        with connect(db_path) as conn:
            cur = conn.execute(
                "UPDATE videos SET path = '' WHERE id = ? AND status IN ('done', 'error') AND path != ''",
                (int(row["id"]),),
            )
            if cur.rowcount == 1:
                cleared += 1
    return cleared


def prune_old_videos(db_path: Path, keep: int | None = None, keep_gb: float | None = None) -> int:
    """Giữ N video mới nhất và/hoặc tối đa keep_gb GB file đã xong.

    Không đụng video đang tải / chờ / đang đọc. Kết quả DB giữ nguyên.
    """
    cleared = clear_missing_video_paths(db_path)
    limit = keep_video_count() if keep is None else max(0, keep)
    budget = int((keep_video_gb() if keep_gb is None else max(0.0, keep_gb)) * 1024 * 1024 * 1024)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, path, status, size_bytes FROM videos
            WHERE path != '' ORDER BY id DESC
            """
        ).fetchall()
    removed = 0
    kept_bytes = 0
    for index, row in enumerate(rows):
        status = row["status"]
        if status in ("uploading", "queued", "running"):
            # Vẫn tính vào budget? Không — đang xử lý thì không xóa.
            continue
        size = int(row["size_bytes"] or 0)
        over_count = index >= limit
        over_budget = kept_bytes + size > budget if budget >= 0 else False
        if not over_count and not over_budget:
            kept_bytes += size
            continue
        path = Path(str(row["path"]))
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"Không xóa được video cũ {row['id']}: {exc}", flush=True)
            continue
        with connect(db_path) as conn:
            cur = conn.execute(
                """
                UPDATE videos SET path = ''
                WHERE id = ? AND status IN ('done', 'error') AND path != ''
                """,
                (int(row["id"]),),
            )
            if cur.rowcount == 1:
                removed += 1
    return removed + cleared


def rematch_results(
    db_path: Path,
    video_id: int | None = None,
    *,
    cross_video: bool = True,
) -> int:
    """Ghép số điện thoại + username còn thiếu thành một hàng ngang.

    Chạy sau mỗi video và (tuỳ chọn) nền lúc worker khởi động.
    Tính cặp tên ngoài khóa; chỉ giữ BEGIN IMMEDIATE khi ghi từng lô ngắn —
    tránh nghẽn upload/claim khi còn hàng nghìn dòng thiếu.

    cross_video=False: chỉ ghép trong từng video (an toàn cho rematch nền —
    tránh O(phones×users) toàn DB chiếm CPU hàng giờ).
    """
    merged = 0
    merged += int(_with_immediate(db_path, lambda c: _promote_phone_username_reviews(c, video_id)))

    # Cùng một video: tên giống ≥90% thì ghép đúng một số với đúng một @.
    # Không ghép sang video khác, không đoán khi còn hai số hoặc hai @.
    target_videos = _rematch_video_ids(db_path, video_id)
    for vid in target_videos:
        merged += int(_with_immediate(db_path, lambda c, v=vid: _join_similar_names(c, v)))
        merged += int(_with_immediate(db_path, lambda c, v=vid: _absorb_ocr_phone_twins(c, v)))

    if video_id is None and cross_video:
        for vid in _rematch_video_ids(db_path, None):
            merged += int(_with_immediate(db_path, lambda c, v=vid: _absorb_ocr_phone_twins(c, v)))

    merged += int(_with_immediate(db_path, lambda c: _promote_phone_username_reviews(c, video_id)))
    return merged


def _rematch_video_ids(db_path: Path, video_id: int | None) -> list[int]:
    if video_id is not None:
        return [int(video_id)]
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT video_id FROM results
            WHERE username = '' OR phone = ''
            ORDER BY video_id
            """
        ).fetchall()
    return [int(row["video_id"]) for row in rows]


def merge_close_results(db_path: Path) -> int:
    """Tương thích cũ: luôn gọi rematch toàn bộ."""
    return rematch_results(db_path)


def merge_same_name_results(db_path: Path) -> int:
    """Tương thích cũ: luôn gọi rematch toàn bộ."""
    return rematch_results(db_path)


def _load_incomplete_results(db_path: Path, video_id: int | None) -> list[dict[str, object]]:
    """Đọc dòng thiếu — không khóa exclusive (chỉ đọc WAL)."""
    with connect(db_path) as conn:
        if video_id is None:
            rows = conn.execute(
                """
                SELECT id, phone, name, username, video_id
                FROM results
                WHERE username = '' OR phone = ''
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, phone, name, username, video_id
                FROM results
                WHERE video_id = ? AND (username = '' OR phone = '')
                """,
                (video_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def _compute_merge_pairs(
    phones: list[dict[str, object]],
    users: list[dict[str, object]],
) -> list[tuple[dict[str, object], dict[str, object]]]:
    if not phones or not users:
        return []
    pairs = pair_close_names(
        [(str(row["id"]), str(row["name"])) for row in phones],
        [(str(row["id"]), str(row["name"])) for row in users],
        handles={str(row["id"]): str(row["username"]) for row in users},
    )
    phone_by = {str(row["id"]): row for row in phones}
    user_by = {str(row["id"]): row for row in users}
    out: list[tuple[dict[str, object], dict[str, object]]] = []
    for phone_id, user_id in pairs:
        phone = phone_by.get(phone_id)
        user = user_by.get(user_id)
        if phone is not None and user is not None:
            out.append((phone, user))
    return out


def _apply_merge_pairs(
    db_path: Path,
    pairs: list[tuple[dict[str, object], dict[str, object]]],
) -> int:
    if not pairs:
        return 0
    applied = 0
    for offset in range(0, len(pairs), _MERGE_BATCH):
        chunk = pairs[offset : offset + _MERGE_BATCH]

        def _write(conn: sqlite3.Connection, batch=chunk) -> int:
            n = 0
            for phone, user in batch:
                updated = conn.execute(
                    """
                    UPDATE results
                    SET name = ?, username = ?, bucket = ?, reason = '', video_id = ?
                    WHERE id = ? AND username = ''
                    """,
                    (
                        joined_name(str(phone["name"]), str(user["name"])),
                        user["username"],
                        BUCKET_OK,
                        phone["video_id"],
                        phone["id"],
                    ),
                )
                if updated.rowcount != 1:
                    continue
                conn.execute("DELETE FROM results WHERE id = ? AND phone = ''", (user["id"],))
                n += 1
            return n

        applied += int(_with_immediate(db_path, _write))
    return applied


def _rematch_conn(conn: sqlite3.Connection, video_id: int | None = None) -> int:
    """Dọn số OCR lệch, ghép tên giống ≥90% trong từng video, nâng hàng đã đủ số + @."""
    merged = 0
    merged += _promote_phone_username_reviews(conn, video_id)
    if video_id is None:
        ids = [int(row[0]) for row in conn.execute("SELECT DISTINCT video_id FROM results")]
        for vid in ids:
            merged += _join_similar_names(conn, vid)
    else:
        merged += _join_similar_names(conn, video_id)
    merged += _absorb_ocr_phone_twins(conn, video_id)
    merged += _promote_phone_username_reviews(conn, video_id)
    return merged


def _join_similar_names(conn: sqlite3.Connection, video_id: int) -> int:
    """Trong một video: tên giống ≥90% thì gắn @ vào đúng một số và ghi mọi cách viết.

    Không đụng video khác. Hai số cùng khớp một @, hoặc một số khớp hai @, thì để nguyên.
    """
    rows = list(
        conn.execute(
            "SELECT id, phone, name, username FROM results WHERE video_id = ?",
            (video_id,),
        )
    )
    if not rows:
        return 0
    taken_handles = [
        str(row["username"])
        for row in rows
        if str(row["phone"] or "").strip() and str(row["username"] or "").strip()
    ]
    phones: list[tuple[str, str, int]] = []
    orphans: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        phone = str(row["phone"] or "").strip()
        username = str(row["username"] or "").strip()
        name = str(row["name"] or "").strip()
        if phone and not username and name:
            phones.append((phone, name, int(row["id"])))
            continue
        if phone or not username or not name:
            continue
        if any(_handles_are_same(username, other) for other in taken_handles):
            continue
        orphans.setdefault(username, []).append(row)
    if not phones or not orphans:
        return 0
    user_name = {
        username: str(group[0]["name"] or "")
        for username, group in orphans.items()
    }
    phone_id = {phone: rid for phone, _name, rid in phones}
    phone_name = {phone: name for phone, name, _rid in phones}
    owned = [
        (str(row["name"] or ""), str(row["username"] or ""))
        for row in rows
        if str(row["phone"] or "").strip() and str(row["username"] or "").strip()
    ]
    pairs = pair_names_alike_90(
        [(phone, name) for phone, name, _rid in phones],
        [(username, user_name[username]) for username in orphans],
    )
    pairs = [
        item
        for item in pairs
        if not conflicts_with_other_person(item[1], phone_name.get(item[0], ""), owned)
    ]
    merged = 0
    for phone, username, group in pairs:
        variants = [phone_name.get(phone, "")]
        delete_ids: list[int] = []
        for handle in group:
            for row in orphans.get(handle, []):
                variants.append(str(row["name"] or ""))
                delete_ids.append(int(row["id"]))
        display = combine_similar_names(variants) or phone_name.get(phone, "")
        if not display or not username:
            continue
        updated = conn.execute(
            """
            UPDATE results
            SET name = ?, username = ?, bucket = ?, reason = ''
            WHERE id = ? AND username = '' AND phone != ''
            """,
            (display, username, BUCKET_OK, phone_id[phone]),
        )
        if updated.rowcount != 1:
            continue
        for rid in delete_ids:
            conn.execute("DELETE FROM results WHERE id = ? AND phone = ''", (rid,))
        merged += 1
    return merged


def _promote_phone_username_reviews(conn: sqlite3.Connection, video_id: int | None = None) -> int:
    """Hàng đã có số + @ nhưng từng bị xếp 'Cần xem' vì OCR không đọc tên hồ sơ → đủ 3 cột."""
    if video_id is None:
        cur = conn.execute(
            """
            UPDATE results
            SET bucket = ?, reason = ''
            WHERE bucket = ?
              AND phone != ''
              AND username != ''
              AND reason = 'không đọc được tên hồ sơ'
            """,
            (BUCKET_OK, BUCKET_REVIEW),
        )
    else:
        cur = conn.execute(
            """
            UPDATE results
            SET bucket = ?, reason = ''
            WHERE video_id = ?
              AND bucket = ?
              AND phone != ''
              AND username != ''
              AND reason = 'không đọc được tên hồ sơ'
            """,
            (BUCKET_OK, video_id, BUCKET_REVIEW),
        )
    return cur.rowcount


def _absorb_ocr_phone_twins(conn: sqlite3.Connection, video_id: int | None = None) -> int:
    """Số ma OCR: cùng video, cùng tên, lệch ≤2 chữ số với một hàng đã đủ @ → xóa bản thiếu.

    Không đụng hai số thật cùng tên khi cả hai đều chưa có @ (vd Tranhungchef).
    """
    if video_id is None:
        complete = conn.execute(
            """
            SELECT id, phone, name, username, video_id
            FROM results
            WHERE phone != '' AND username != ''
            """
        ).fetchall()
        incomplete = conn.execute(
            """
            SELECT id, phone, name, username, video_id
            FROM results
            WHERE phone != '' AND username = ''
            """
        ).fetchall()
    else:
        complete = conn.execute(
            """
            SELECT id, phone, name, username, video_id
            FROM results
            WHERE video_id = ? AND phone != '' AND username != ''
            """,
            (video_id,),
        ).fetchall()
        incomplete = conn.execute(
            """
            SELECT id, phone, name, username, video_id
            FROM results
            WHERE video_id = ? AND phone != '' AND username = ''
            """,
            (video_id,),
        ).fetchall()
    by_video: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in complete:
        by_video[int(row["video_id"])].append(row)
    removed = 0
    for weak in incomplete:
        for strong in by_video.get(int(weak["video_id"]), []):
            if phone_distance(str(weak["phone"]), str(strong["phone"])) > 2:
                continue
            if not same_person_name(str(weak["name"]), str(strong["name"])):
                continue
            cur = conn.execute(
                "DELETE FROM results WHERE id = ? AND phone != '' AND username = ''",
                (weak["id"],),
            )
            if cur.rowcount == 1:
                removed += 1
            break
    return removed


def _merge_pair_group(
    conn: sqlite3.Connection,
    phones: list[sqlite3.Row],
    users: list[sqlite3.Row],
) -> int:
    if not phones or not users:
        return 0
    pairs = pair_close_names(
        [(str(row["id"]), row["name"]) for row in phones],
        [(str(row["id"]), row["name"]) for row in users],
        handles={str(row["id"]): str(row["username"]) for row in users},
    )
    phone_by = {str(row["id"]): row for row in phones}
    user_by = {str(row["id"]): row for row in users}
    merged = 0
    for phone_id, user_id in pairs:
        phone = phone_by[phone_id]
        user = user_by[user_id]
        updated = conn.execute(
            """
            UPDATE results
            SET name = ?, username = ?, bucket = ?, reason = '', video_id = ?
            WHERE id = ? AND username = ''
            """,
            (
                joined_name(phone["name"], user["name"]),
                user["username"],
                BUCKET_OK,
                phone["video_id"],
                phone["id"],
            ),
        )
        if updated.rowcount != 1:
            continue
        conn.execute("DELETE FROM results WHERE id = ? AND phone = ''", (user["id"],))
        merged += 1
    return merged


_RESULT_SELECT = """
    SELECT
      r.id, r.phone, r.name, r.username, r.bucket, r.reason, r.created_at AS scanned_at,
      COALESCE(r.phone_mask, '') AS phone_mask,
      COALESCE(r.shots, '') AS shots,
      COALESCE(v.device, '') AS device,
      COALESCE(v.name, '') AS video
    FROM results r
    LEFT JOIN videos v ON v.id = r.video_id
"""


def _view_sql(view: str) -> str:
    """complete = đủ 3 cột; incomplete = cần xem hoặc lỗi quét; all = mọi dòng."""
    kind = (view or "complete").strip().lower()
    if kind == "incomplete":
        return f"r.bucket != {BUCKET_OK}"
    if kind == "all":
        return "1=1"
    return f"r.bucket = {BUCKET_OK}"


def search_results(
    db_path: Path,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    view: str = "complete",
    day: str = "all",
) -> list[dict[str, object]]:
    """Tìm theo đầu số, đầu username hoặc tên video. Mặc định chỉ hàng đủ số + @."""
    text = "".join(query.split())
    if text.startswith("+84"):
        text = "0" + text[3:]
    start_at = max(0, int(offset))
    view_clause = _view_sql(view)
    day_sql, day_params = _day_filter(day, "r.created_at")
    with connect(db_path) as conn:
        if text.isdigit():
            rows = conn.execute(
                _RESULT_SELECT
                + f"""
                WHERE r.phone >= ? AND r.phone < ? AND r.phone != '' AND {view_clause} AND {day_sql}
                ORDER BY r.phone
                LIMIT ? OFFSET ?
                """,
                (text, text + ":", *day_params, limit, start_at),
            ).fetchall()
        elif text.startswith("@"):
            rows = conn.execute(
                _RESULT_SELECT
                + f"""
                WHERE r.username >= ? AND r.username < ? AND r.username != '' AND {view_clause} AND {day_sql}
                ORDER BY r.username
                LIMIT ? OFFSET ?
                """,
                (text, text + "\U0010ffff", *day_params, limit, start_at),
            ).fetchall()
        elif text:
            video_ids = [
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM videos WHERE name LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT 20",
                    (f"%{text}%",),
                ).fetchall()
            ]
            if video_ids:
                marks = ",".join("?" for _ in video_ids)
                rows = conn.execute(
                    _RESULT_SELECT
                    + f"""
                    WHERE r.video_id IN ({marks}) AND {view_clause} AND {day_sql}
                    ORDER BY r.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (*video_ids, *day_params, limit, start_at),
                ).fetchall()
            else:
                handle = "@" + text
                rows = conn.execute(
                    _RESULT_SELECT
                    + f"""
                    WHERE r.username >= ? AND r.username < ? AND r.username != '' AND {view_clause} AND {day_sql}
                    ORDER BY r.username
                    LIMIT ? OFFSET ?
                    """,
                    (handle, handle + "\U0010ffff", *day_params, limit, start_at),
                ).fetchall()
        else:
            rows = conn.execute(
                _RESULT_SELECT
                + f"""
                WHERE {view_clause} AND {day_sql}
                ORDER BY r.id DESC
                LIMIT ? OFFSET ?
                """,
                (*day_params, limit, start_at),
            ).fetchall()
    return [_public_result(row) for row in rows]


def count_results(db_path: Path, query: str = "", view: str = "complete", day: str = "all") -> int:
    """Đếm tổng dòng khớp bộ lọc để trang chủ biết còn phải tải tiếp không."""
    text = "".join(query.split())
    if text.startswith("+84"):
        text = "0" + text[3:]
    view_clause = _view_sql(view).replace("r.", "")
    day_sql, day_params = _day_filter(day, "created_at")
    with connect(db_path) as conn:
        if text.isdigit():
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM results WHERE phone >= ? AND phone < ? AND phone != '' AND {view_clause} AND {day_sql}",
                (text, text + ":", *day_params),
            ).fetchone()
        elif text.startswith("@"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM results WHERE username >= ? AND username < ? AND username != '' AND {view_clause} AND {day_sql}",
                (text, text + "\U0010ffff", *day_params),
            ).fetchone()
        elif text:
            video_ids = [
                int(item["id"])
                for item in conn.execute(
                    "SELECT id FROM videos WHERE name LIKE ? COLLATE NOCASE ORDER BY id DESC LIMIT 20",
                    (f"%{text}%",),
                ).fetchall()
            ]
            if video_ids:
                marks = ",".join("?" for _ in video_ids)
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM results WHERE video_id IN ({marks}) AND {view_clause} AND {day_sql}",
                    (*video_ids, *day_params),
                ).fetchone()
            else:
                handle = "@" + text
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM results WHERE username >= ? AND username < ? AND username != '' AND {view_clause} AND {day_sql}",
                    (handle, handle + "\U0010ffff", *day_params),
                ).fetchone()
        else:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM results WHERE {view_clause} AND {day_sql}",
                day_params,
            ).fetchone()
    return int(row["n"])


def iter_backup(db_path: Path) -> Iterator[str]:
    """Ảnh chụp nhất quán các dòng đã commit. Người gọi giữ kết nối đến hết vòng lặp."""
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    conn.execute("BEGIN")
    try:
        yield "Số điện thoại,Tên,Username,Thời gian quét,Tên máy,Video\n"
        cursor = conn.execute(
            """
            SELECT
              r.phone, r.name, r.username, r.created_at AS scanned_at,
              COALESCE(v.device, '') AS device,
              COALESCE(v.name, '') AS video
            FROM results r
            LEFT JOIN videos v ON v.id = r.video_id
            ORDER BY r.id DESC
            """
        )
        for row in cursor:
            yield _csv_line(
                [
                    row["phone"],
                    row["name"],
                    row["username"],
                    row["scanned_at"],
                    row["device"],
                    row["video"],
                ]
            )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def _shot_map(item: object) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for key, attr in (("phone", "phone_shot"), ("name", "name_shot"), ("user", "user_shot")):
        blob = getattr(item, attr, b"") or b""
        if isinstance(blob, bytes) and blob:
            found[key] = blob
    return found


def shots_root(db_path: Path) -> Path:
    return db_path.parent / "shots"


def result_shot_path(db_path: Path, result_id: int, field: str) -> Path | None:
    if field not in {"phone", "name", "user"}:
        return None
    with connect(db_path) as conn:
        row = conn.execute("SELECT shots FROM results WHERE id = ?", (int(result_id),)).fetchone()
    if row is None or not row["shots"]:
        return None
    try:
        saved = json.loads(str(row["shots"]))
    except json.JSONDecodeError:
        return None
    if not isinstance(saved, dict):
        return None
    return shot_file(db_path, str(saved.get(field) or ""))


def shot_file(db_path: Path, relative: str) -> Path | None:
    text = str(relative or "")
    if not text or text.startswith("/") or ".." in text.split("/"):
        return None
    root = shots_root(db_path).resolve()
    path = (root / text).resolve()
    if path != root and root not in path.parents:
        return None
    if not path.is_file():
        return None
    return path


def _stamp_evidence(
    conn: sqlite3.Connection,
    result_id: int,
    video_id: int,
    phone_mask: str,
    shots: dict[str, bytes],
) -> None:
    mask = " ".join(str(phone_mask or "").split())
    if mask:
        conn.execute("UPDATE results SET phone_mask = ? WHERE id = ?", (mask[:40], int(result_id)))
    if not shots:
        return
    root = shots_root(Path(conn.execute("PRAGMA database_list").fetchone()[2]))
    saved: dict[str, str] = {}
    for field, blob in shots.items():
        if field not in {"phone", "name", "user"} or not blob:
            continue
        rel = f"{int(video_id)}_{int(result_id)}_{field}.jpg"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        saved[field] = rel
    if saved:
        conn.execute("UPDATE results SET shots = ? WHERE id = ?", (json.dumps(saved), int(result_id)))


def _put(
    conn: sqlite3.Connection,
    phone: str,
    name: str,
    username: str,
    bucket: int,
    reason: str,
    video_id: int,
    upgrade: bool,
    phone_mask: str = "",
    shots: dict[str, bytes] | None = None,
) -> None:
    if not phone:
        return
    existing = conn.execute(
        "SELECT id, bucket, username FROM results WHERE phone = ? AND phone != ''",
        (phone,),
    ).fetchone()
    if existing is None:
        cur = conn.execute(
            """
            INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (phone, name, username, bucket, reason, video_id, utcnow()),
        )
        _stamp_evidence(conn, int(cur.lastrowid), video_id, phone_mask, shots or {})
        _drop_lone_username(conn, username, bucket)
        return
    if not upgrade:
        return
    if int(existing["bucket"]) == BUCKET_OK and existing["username"]:
        return
    if bucket != BUCKET_OK:
        return
    conn.execute(
        "UPDATE results SET name = ?, username = ?, bucket = ?, reason = '', video_id = ? WHERE id = ?",
        (name, username, BUCKET_OK, video_id, existing["id"]),
    )
    _drop_lone_username(conn, username, bucket)


def _drop_lone_username(conn: sqlite3.Connection, username: str, bucket: int) -> None:
    """Username đã có số thì dòng username đứng riêng từ video trước là thừa."""
    if bucket != BUCKET_OK or not username:
        return
    conn.execute(
        "DELETE FROM results WHERE username = ? AND username != '' AND phone = ''",
        (username,),
    )


def _put_review(conn: sqlite3.Connection, item: Review, video_id: int) -> None:
    name = item.contact_name or item.profile_name
    if item.phone:
        existing = conn.execute(
            "SELECT id, bucket, name, username FROM results WHERE phone = ? AND phone != ''",
            (item.phone,),
        ).fetchone()
        if existing is not None:
            # Hàng đã đủ 3 cột thì không hạ xuống. Hàng thiếu @ được nâng khi video sau đọc được.
            if int(existing["bucket"]) == BUCKET_OK and existing["username"]:
                return
            merged_name = name or str(existing["name"] or "")
            merged_user = item.username or str(existing["username"] or "")
            bucket, reason = classify_result(item.phone, merged_name, merged_user, item.reason)
            if bucket != BUCKET_OK:
                return
            conn.execute(
                "UPDATE results SET name = ?, username = ?, bucket = ?, reason = '', video_id = ? WHERE id = ?",
                (merged_name, merged_user, BUCKET_OK, video_id, existing["id"]),
            )
            _drop_lone_username(conn, merged_user, BUCKET_OK)
            return
        bucket, reason = classify_result(item.phone, name, item.username, item.reason)
        cur = conn.execute(
            """
            INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (item.phone, name, item.username, bucket, reason, video_id, utcnow()),
        )
        _stamp_evidence(conn, int(cur.lastrowid), video_id, item.phone_mask, _shot_map(item))
        if bucket == BUCKET_OK:
            _drop_lone_username(conn, item.username, bucket)
        return
    if not item.username:
        if (item.reason or "").strip() == COVER_REASON:
            existing = conn.execute(
                "SELECT id FROM results WHERE phone = '' AND reason = ? AND name = ? LIMIT 1",
                (COVER_REASON, item.contact_name),
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    """
                    INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
                    VALUES ('', ?, '', ?, ?, ?, ?)
                    """,
                    (item.contact_name, BUCKET_REVIEW, COVER_REASON, video_id, utcnow()),
                )
                _stamp_evidence(conn, int(cur.lastrowid), video_id, item.phone_mask, _shot_map(item))
        return
    # @ không có số: giữ lại để xem, không ghép sang video khác.
    # Trùng tên và một số mở nhiều hồ sơ vẫn giữ.
    if not (item.reason or "").strip():
        return
    existing = conn.execute(
        "SELECT id FROM results WHERE username = ? AND username != '' LIMIT 1",
        (item.username,),
    ).fetchone()
    if existing is not None:
        return
    bucket, reason = classify_result("", item.profile_name, item.username, item.reason)
    cur = conn.execute(
        """
        INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
        VALUES ('', ?, ?, ?, ?, ?, ?)
        """,
        (item.profile_name, item.username, bucket, reason, video_id, utcnow()),
    )
    _stamp_evidence(conn, int(cur.lastrowid), video_id, item.phone_mask, _shot_map(item))


def _video_issue(item: dict[str, object]) -> str:
    """Mô tả vấn đề / trạng thái cụ thể cho cột hàng đợi (thay tên máy)."""
    status = str(item.get("status") or "")
    error = " ".join(str(item.get("error") or "").split())
    if status == "error":
        return error[:160] if error else "Lỗi khi đọc video"
    if status == "uploading":
        return "Đang tải lên"
    if status == "queued":
        return "Chờ đọc"
    if status == "running":
        progress = " ".join(str(item.get("progress") or "").split())
        return progress[:160] if progress else "Đang đọc"
    if status != "done":
        return status or "—"

    saved = int(item.get("saved_count") or 0)
    review = int(item.get("review_count") or 0)
    scan_errors = int(item.get("unopened_count") or 0)
    total = saved + review + scan_errors
    if total <= 0:
        return "Không có dòng kết quả"
    parts = [f"{saved} đủ"]
    if review:
        parts.append(f"{review} cần xem (có số, thiếu tên hoặc @)")
    if scan_errors:
        parts.append(f"{scan_errors} lỗi quét")
    if saved == total:
        return f"Tất cả đều tốt — {saved} đủ"
    return " · ".join(parts)


def correct_result(
    db_path: Path,
    result_id: int,
    phone: str,
    name: str,
    username: str,
) -> dict[str, object]:
    """Sửa một dòng đã lưu. Người gõ những gì đã nhìn thấy — không đoán chữ dưới chấm.

    Hàng đủ số + tên + @ sau khi sửa tính là Đủ và bỏ lý do điểm thấp.
    Số đã thuộc dòng khác thì từ chối.
    """
    shown = clean_name(name)
    typed = "".join(str(username or "").split())
    if typed and not typed.startswith("@"):
        typed = "@" + typed
    handle = clean_username(typed) if typed else ""
    if typed and not is_profile_handle(handle):
        raise ValueError("@ không đúng dạng hồ sơ. Không đoán chữ dưới chấm.")
    number = normalize_phone(phone)
    if not number:
        raise ValueError("Cần số điện thoại đã nhìn thấy. Không đoán chữ dưới chấm.")
    bucket, reason = classify_result(number, shown, handle, "")

    def _update(conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT id FROM results WHERE id = ?", (int(result_id),)).fetchone()
        if row is None:
            raise LookupError("Không thấy dòng kết quả.")
        other = conn.execute(
            "SELECT id FROM results WHERE phone = ? AND id != ?",
            (number, int(result_id)),
        ).fetchone()
        if other is not None:
            raise ValueError("Số này đã thuộc một dòng khác.")
        conn.execute(
            """
            UPDATE results
            SET phone = ?, name = ?, username = ?, bucket = ?, reason = ?
            WHERE id = ?
            """,
            (number, shown, handle, bucket, reason, int(result_id)),
        )

    try:
        _with_immediate(db_path, _update)
    except sqlite3.IntegrityError as exc:
        raise ValueError("Số này đã thuộc một dòng khác.") from exc
    with connect(db_path) as conn:
        saved = conn.execute(_RESULT_SELECT + " WHERE r.id = ?", (int(result_id),)).fetchone()
    if saved is None:
        raise LookupError("Không thấy dòng kết quả.")
    return _public_result(saved)


def export_contact_rows(db_path: Path) -> list[tuple[str, str, str]]:
    """Một số điện thoại một dòng. Không đổi phần trăm đọc."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT phone, name, username
            FROM results
            WHERE phone != ''
            ORDER BY phone
            """
        ).fetchall()
    return [(str(row["phone"]), str(row["name"] or ""), str(row["username"] or "")) for row in rows]


def _vcard_text(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def render_vcf(rows: list[tuple[str, str, str]]) -> str:
    """vCard 3.0. NOTE giữ @. Mỗi số một thẻ."""
    blocks: list[str] = []
    for phone, name, username in rows:
        if not phone:
            continue
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"FN:{_vcard_text(name or phone)}",
            f"TEL;TYPE=CELL:{phone}",
        ]
        if username:
            lines.append(f"NOTE:{_vcard_text(username)}")
        lines.append("END:VCARD")
        blocks.append("\r\n".join(lines))
    if not blocks:
        return ""
    return "\r\n".join(blocks) + "\r\n"


def render_csv(rows: list[tuple[str, str, str]]) -> str:
    lines = [_csv_line(["phone", "name", "username"])]
    for phone, name, username in rows:
        lines.append(_csv_line([phone, name, username]))
    return "".join(lines)


def _public_result(row: sqlite3.Row) -> dict[str, object]:
    keys = set(row.keys())
    result_id = int(row["id"])
    shots: dict[str, str] = {}
    raw = row["shots"] if "shots" in keys else ""
    try:
        saved = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        saved = {}
    if isinstance(saved, dict):
        for field in ("phone", "name", "user"):
            if saved.get(field):
                shots[field] = f"/v1/results/{result_id}/shot/{field}"
    return {
        "id": result_id,
        "phone": row["phone"],
        "phone_mask": row["phone_mask"] if "phone_mask" in keys else "",
        "name": row["name"],
        "username": row["username"],
        "bucket": BUCKET_LABEL.get(int(row["bucket"]), ""),
        "reason": row["reason"],
        "shots": shots,
        "scanned_at": row["scanned_at"] if "scanned_at" in keys else row["created_at"] if "created_at" in keys else "",
        "device": row["device"] if "device" in keys else "",
        "video": row["video"] if "video" in keys else "",
    }


def _csv_line(fields: list[str]) -> str:
    escaped = []
    for field in fields:
        text = str(field or "")
        if any(ch in text for ch in ",\"\n\r"):
            text = '"' + text.replace('"', '""') + '"'
        escaped.append(text)
    return ",".join(escaped) + "\n"


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def touch_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


def reader_alive(path: Path, max_age: float = 20.0) -> bool:
    try:
        seen = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return (time.time() - seen) < max_age
