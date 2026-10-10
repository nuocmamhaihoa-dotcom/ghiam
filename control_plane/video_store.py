"""Hàng đợi video và kết quả đã đọc.

Ghi bằng SQLite WAL, synchronous=FULL: mất điện vẫn giữ các dòng đã commit.
Mỗi video chỉ vài chục dòng, nên 15 tiến trình đọc không nghẽn ở chỗ ghi.
Một dòng khoảng vài trăm byte. Một trăm triệu dòng nằm trong vài chục GB.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from control_plane.screen_table import (
    Review,
    Table,
    joined_name,
    pair_close_names,
    phone_distance,
    same_person_name,
)

BUCKET_OK = 1
BUCKET_REVIEW = 2
BUCKET_UNOPENED = 3
BUCKET_LABEL = {BUCKET_OK: "Đã lưu", BUCKET_REVIEW: "Cần xem", BUCKET_UNOPENED: "Chưa mở hồ sơ"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def worker_has_live_children(pid: int | None) -> bool:
    """Còn ffmpeg / OCR spawn → job đang chạy thật.

    Bỏ qua resource_tracker: nó vẫn sống sau khi pool OCR đã chết (treo ở 15×400 task).
    """
    if not pid or not _alive(pid):
        return False
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                ppid = int((entry / "stat").read_text().split()[3])
            except (OSError, ValueError, IndexError):
                continue
            if ppid != int(pid):
                continue
            try:
                cmd = (entry / "cmdline").read_text(errors="replace")
            except OSError:
                cmd = ""
            if "resource_tracker" in cmd:
                continue
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


_READY: set[str] = set()


def init_db(db_path: Path) -> None:
    """Tạo bảng một lần cho mỗi tiến trình.

    result_counts giữ số dòng theo loại, do trigger cập nhật trong cùng giao
    dịch, để trang thống kê không phải đếm lại cả bảng mỗi vài giây.
    """
    key = str(Path(db_path).resolve())
    if key in _READY and Path(db_path).exists():
        return
    with connect(db_path) as conn:
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
        _ensure_video_columns(conn)
        _reconcile_result_counts(conn)
    _READY.add(key)


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


def _reconcile_result_counts(conn: sqlite3.Connection) -> None:
    """Nếu bộ đếm lệch so với bảng results (hiếm) thì đếm lại ngay."""
    cached = {int(row["bucket"]): int(row["n"]) for row in conn.execute("SELECT bucket, n FROM result_counts")}
    real = {int(row["bucket"]): int(row["n"]) for row in conn.execute("SELECT bucket, COUNT(*) AS n FROM results GROUP BY bucket")}
    if cached == real:
        return
    conn.execute("DELETE FROM result_counts")
    for bucket, n in real.items():
        conn.execute("INSERT INTO result_counts (bucket, n) VALUES (?, ?)", (bucket, n))


def incoming_bytes(db_path: Path) -> int:
    """Dung lượng phiên cắt khúc đang nhận (incoming/*.part)."""
    try:
        with connect(db_path) as conn:
            # Bảng có thể chưa tạo ở DB cũ trước lần upload đầu.
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
            ).fetchone()
            if exists is None:
                return 0
            row = conn.execute(
                """
                SELECT COALESCE(SUM(size_bytes), 0) AS n
                FROM upload_sessions WHERE status = 'receiving'
                """
            ).fetchone()
        return int(row["n"])
    except sqlite3.Error:
        return 0


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


def should_pause_ocr(db_path: Path, video_dir: Path) -> bool:
    """Tạm không nhận video OCR mới khi đĩa căng hoặc đang up hàng loạt."""
    try:
        free = shutil.disk_usage(video_dir).free
    except OSError:
        return False
    if free < ocr_pause_free_bytes():
        return True
    try:
        with connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'"
            ).fetchone()
            if exists is None:
                return False
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM upload_sessions WHERE status = 'receiving'"
            ).fetchone()
        receiving = int(row["n"])
    except sqlite3.Error:
        return False
    pause_at = int(os.environ.get("CONTROL_VIDEO_OCR_PAUSE_UPLOADS", "6") or "6")
    return receiving >= max(1, pause_at)


def clean_device(device: str) -> str:
    return " ".join((device or "").split())[:80]


def begin_upload(db_path: Path, *, name: str, size_bytes: int, sha256: str, device: str = "") -> dict[str, object]:
    """Cùng một file đang chờ hoặc đang đọc thì bỏ qua. Đã đọc xong hoặc lỗi thì đọc lại bằng bộ đọc hiện tại."""
    machine = clean_device(device)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id, status, name, path FROM videos WHERE sha256 = ?", (sha256,)).fetchone()
        if existing and existing["status"] in ("done", "error"):
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
            conn.execute("COMMIT")
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
            conn.execute("COMMIT")
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
            (name, size_bytes, sha256, utcnow(), machine),
        )
        conn.execute("COMMIT")
        return {
            "id": int(cur.lastrowid),
            "status": "uploading",
            "name": name,
            "device": machine,
            "duplicate": False,
        }


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


def abort_upload(db_path: Path, video_id: int, reopened: bool = False) -> None:
    with connect(db_path) as conn:
        if reopened:
            conn.execute(
                "UPDATE videos SET status = 'error', error = ? WHERE id = ? AND status = 'uploading'",
                ("Tải lên bị gián đoạn. Tải lại video.", video_id),
            )
            return
        conn.execute("DELETE FROM videos WHERE id = ? AND status = 'uploading'", (video_id,))


def list_videos(db_path: Path, limit: int = 40) -> list[dict[str, object]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              v.id, v.name, v.size_bytes, v.status, v.error, v.created_at, v.started_at, v.finished_at,
              v.device, v.progress, v.progress_at, v.path,
              COALESCE(v.duration_sec, 0) AS duration_sec,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id), 0) AS result_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.bucket = 1), 0) AS saved_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.bucket = 2), 0) AS review_count,
              COALESCE((SELECT COUNT(*) FROM results r WHERE r.video_id = v.id AND r.bucket = 3), 0) AS unopened_count
            FROM videos v
            ORDER BY v.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        path = str(item.pop("path", "") or "")
        item["file_kept"] = bool(path) and Path(path).exists()
        try:
            item["duration_sec"] = float(item.get("duration_sec") or 0)
        except (TypeError, ValueError):
            item["duration_sec"] = 0.0
        item["issue"] = _video_issue(item)
        items.append(item)
    return items


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
    counts.update(buckets)
    counts["results"] = buckets["saved"] + buckets["review"] + buckets["unopened"]
    return counts


def claim(db_path: Path, pid: int) -> dict[str, object] | None:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, name, path, COALESCE(device, '') AS device "
            "FROM videos WHERE status = 'queued' AND path != '' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
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
        conn.execute("COMMIT")
        if cur.rowcount != 1:
            return None
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "path": row["path"],
            "device": row["device"] or "",
        }


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
    """Lưu kết quả rồi ghép ngay số + username thành hàng ngang đủ ba cột."""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in table.rows:
            _put(conn, row.phone, row.name, row.username, BUCKET_OK, "", video_id, upgrade=True)
        for item in table.unopened:
            _put(conn, item.phone, item.name, "", BUCKET_UNOPENED, "", video_id, upgrade=False)
        for item in table.review:
            _put_review(conn, item, video_id)
        _rematch_conn(conn, video_id)
        conn.execute(
            """
            UPDATE videos
            SET status = 'done', pid = NULL, finished_at = ?, error = '',
                progress = '', progress_at = NULL
            WHERE id = ?
            """,
            (utcnow(), video_id),
        )
        conn.execute("COMMIT")


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


def rematch_results(db_path: Path, video_id: int | None = None) -> int:
    """Ghép số điện thoại + username còn thiếu thành một hàng ngang.

    Chạy sau mỗi video và lúc khởi động. Không khóa meta: luôn quét lại phần còn thiếu
    để kết quả cũ và kết quả mới mãi về sau đều được ghép.
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            merged = _rematch_conn(conn, video_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return merged


def merge_close_results(db_path: Path) -> int:
    """Tương thích cũ: luôn gọi rematch toàn bộ."""
    return rematch_results(db_path)


def merge_same_name_results(db_path: Path) -> int:
    """Tương thích cũ: luôn gọi rematch toàn bộ."""
    return rematch_results(db_path)


def _rematch_conn(conn: sqlite3.Connection, video_id: int | None = None) -> int:
    merged = 0
    merged += _promote_phone_username_reviews(conn, video_id)
    merged += _absorb_ocr_phone_twins(conn, video_id)
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
    grouped: dict[int, dict[str, list[sqlite3.Row]]] = defaultdict(lambda: {"phones": [], "users": []})
    for row in rows:
        if row["phone"] and not row["username"]:
            grouped[int(row["video_id"])]["phones"].append(row)
        elif row["username"] and not row["phone"]:
            grouped[int(row["video_id"])]["users"].append(row)
    for items in grouped.values():
        merged += _merge_pair_group(conn, items["phones"], items["users"])
    if video_id is None:
        leftovers = conn.execute(
            """
            SELECT id, phone, name, username, video_id
            FROM results
            WHERE username = '' OR phone = ''
            """
        ).fetchall()
        phones = [row for row in leftovers if row["phone"] and not row["username"]]
        users = [row for row in leftovers if row["username"] and not row["phone"]]
        merged += _merge_pair_group(conn, phones, users)
    merged += _absorb_ocr_phone_twins(conn, video_id)
    merged += _promote_phone_username_reviews(conn, video_id)
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
      COALESCE(v.device, '') AS device,
      COALESCE(v.name, '') AS video
    FROM results r
    LEFT JOIN videos v ON v.id = r.video_id
"""


def _view_sql(view: str) -> str:
    """complete = đủ 3 cột; incomplete = thiếu số hoặc @; all = mọi dòng."""
    kind = (view or "complete").strip().lower()
    if kind == "incomplete":
        return "(r.phone = '' OR r.username = '')"
    if kind == "all":
        return "1=1"
    return "(r.phone != '' AND r.username != '')"


def search_results(
    db_path: Path,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    view: str = "complete",
) -> list[dict[str, object]]:
    """Tìm theo đầu số, đầu username hoặc tên video. Mặc định chỉ hàng đủ số + @."""
    text = "".join(query.split())
    if text.startswith("+84"):
        text = "0" + text[3:]
    start = max(0, int(offset))
    view_clause = _view_sql(view)
    with connect(db_path) as conn:
        if text.isdigit():
            rows = conn.execute(
                _RESULT_SELECT
                + f"""
                WHERE r.phone >= ? AND r.phone < ? AND r.phone != '' AND {view_clause}
                ORDER BY r.phone
                LIMIT ? OFFSET ?
                """,
                (text, text + ":", limit, start),
            ).fetchall()
        elif text.startswith("@"):
            rows = conn.execute(
                _RESULT_SELECT
                + f"""
                WHERE r.username >= ? AND r.username < ? AND r.username != '' AND {view_clause}
                ORDER BY r.username
                LIMIT ? OFFSET ?
                """,
                (text, text + "\U0010ffff", limit, start),
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
                    WHERE r.video_id IN ({marks}) AND {view_clause}
                    ORDER BY r.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (*video_ids, limit, start),
                ).fetchall()
            else:
                handle = "@" + text
                rows = conn.execute(
                    _RESULT_SELECT
                    + f"""
                    WHERE r.username >= ? AND r.username < ? AND r.username != '' AND {view_clause}
                    ORDER BY r.username
                    LIMIT ? OFFSET ?
                    """,
                    (handle, handle + "\U0010ffff", limit, start),
                ).fetchall()
        else:
            rows = conn.execute(
                _RESULT_SELECT
                + f"""
                WHERE {view_clause}
                ORDER BY r.id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, start),
            ).fetchall()
    return [_public_result(row) for row in rows]


def count_results(db_path: Path, query: str = "", view: str = "complete") -> int:
    """Đếm tổng dòng khớp bộ lọc để trang chủ biết còn phải tải tiếp không."""
    text = "".join(query.split())
    if text.startswith("+84"):
        text = "0" + text[3:]
    view_clause = _view_sql(view).replace("r.", "")
    with connect(db_path) as conn:
        if text.isdigit():
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM results WHERE phone >= ? AND phone < ? AND phone != '' AND {view_clause}",
                (text, text + ":"),
            ).fetchone()
        elif text.startswith("@"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM results WHERE username >= ? AND username < ? AND username != '' AND {view_clause}",
                (text, text + "\U0010ffff"),
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
                    f"SELECT COUNT(*) AS n FROM results WHERE video_id IN ({marks}) AND {view_clause}",
                    video_ids,
                ).fetchone()
            else:
                handle = "@" + text
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM results WHERE username >= ? AND username < ? AND username != '' AND {view_clause}",
                    (handle, handle + "\U0010ffff"),
                ).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM results WHERE {view_clause}").fetchone()
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


def _put(
    conn: sqlite3.Connection,
    phone: str,
    name: str,
    username: str,
    bucket: int,
    reason: str,
    video_id: int,
    upgrade: bool,
) -> None:
    if not phone:
        return
    existing = conn.execute(
        "SELECT id, bucket, username FROM results WHERE phone = ? AND phone != ''",
        (phone,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (phone, name, username, bucket, reason, video_id, utcnow()),
        )
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
    if item.phone:
        existing = conn.execute(
            "SELECT id, bucket, username FROM results WHERE phone = ? AND phone != ''",
            (item.phone,),
        ).fetchone()
        if existing is not None:
            # Số đang "chưa mở hồ sơ" mà video sau đã mở được @ → đủ 3 cột nếu đã có username.
            if int(existing["bucket"]) == BUCKET_UNOPENED and item.username and not existing["username"]:
                bucket = BUCKET_OK if item.username else BUCKET_REVIEW
                conn.execute(
                    "UPDATE results SET username = ?, bucket = ?, reason = ?, video_id = ? WHERE id = ?",
                    (
                        item.username,
                        bucket,
                        "" if bucket == BUCKET_OK else item.reason,
                        video_id,
                        existing["id"],
                    ),
                )
            return
        # Đã có số + @ thì là hàng đủ; chỉ giữ "Cần xem" khi thiếu một trong hai hoặc mở nhiều hồ sơ.
        complete = bool(item.phone and item.username and item.reason == "không đọc được tên hồ sơ")
        conn.execute(
            """
            INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.phone,
                item.contact_name or item.profile_name,
                item.username,
                BUCKET_OK if complete else BUCKET_REVIEW,
                "" if complete else item.reason,
                video_id,
                utcnow(),
            ),
        )
        return
    if not item.username:
        return
    existing = conn.execute(
        "SELECT id FROM results WHERE username = ? AND username != '' LIMIT 1",
        (item.username,),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
        VALUES ('', ?, ?, ?, ?, ?, ?)
        """,
        (item.profile_name, item.username, BUCKET_REVIEW, item.reason, video_id, utcnow()),
    )


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
    unopened = int(item.get("unopened_count") or 0)
    total = saved + review + unopened
    if total <= 0:
        return "Không có dòng kết quả"
    unopened_pct = round(100.0 * unopened / total)
    if unopened_pct > 50:
        return f"Cảnh báo: {unopened_pct}% chưa mở hồ sơ"
    if unopened > 0 and review > 0:
        return f"Còn {unopened} chưa mở · {review} cần xem"
    if unopened > 0:
        return f"Còn {unopened} chưa mở hồ sơ"
    if review > 0 and saved > 0:
        return f"Tốt — {saved} đủ, còn {review} cần xem"
    if review > 0:
        return f"Chỉ có hàng cần xem ({review})"
    return f"Tất cả đều tốt — {saved} đủ"


def _public_result(row: sqlite3.Row) -> dict[str, object]:
    keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "phone": row["phone"],
        "name": row["name"],
        "username": row["username"],
        "bucket": BUCKET_LABEL.get(int(row["bucket"]), ""),
        "reason": row["reason"],
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
