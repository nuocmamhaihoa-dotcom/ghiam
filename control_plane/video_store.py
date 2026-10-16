"""Hàng đợi video và kết quả đã đọc.

Ghi bằng SQLite WAL, synchronous=FULL: mất điện vẫn giữ các dòng đã commit.
Mỗi video chỉ vài chục dòng, nên 15 tiến trình đọc không nghẽn ở chỗ ghi.
Một dòng khoảng vài trăm byte. Một trăm triệu dòng nằm trong vài chục GB.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
import time
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from doc_video.text import handle_matches_name, lookalike_handles

from control_plane.phone_vn import carrier, phone_columns
from control_plane.screen_table import Review, Table, joined_name, pair_close_names

BUCKET_OK = 1
BUCKET_REVIEW = 2
BUCKET_UNOPENED = 3
BUCKET_LABEL = {BUCKET_OK: "Đã lưu", BUCKET_REVIEW: "Cần xem", BUCKET_UNOPENED: "Chưa mở hồ sơ"}
# PC giữ video ở trạng thái pc, không phải running. Bộ đọc trên máy chủ chỉ nhận queued
# và chỉ thu hồi running, nên khởi động lại bộ đọc không cướp video PC đang đọc.
# Không có nhịp trong khoảng này thì video trở lại hàng chờ.
PC_LEASE_SECONDS = 900
# PC hỏng chừng này video liền nhau thì không nhận video nữa, cho đến khi trang bấm cho đọc lại.
PC_FAIL_LIMIT = 3
# Video hỏng trên chừng này PC thì chỉ để máy chủ đọc.
PC_VIDEO_FAIL_LIMIT = 2


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def clean_device(raw: str) -> str:
    """Tên điện thoại gắn trên video: ngắn, một dòng, đúng chữ người dùng chọn."""
    text = " ".join(str(raw or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:40]


def vn_day_bounds(now: datetime | None = None) -> tuple[str, str, str]:
    """Ngày theo giờ Việt Nam và hai mốc UTC để so với created_at, finished_at."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    start_local = moment.astimezone(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.date().isoformat(),
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )


def worker_count() -> int:
    """Số tiến trình đọc khung. Mặc định bằng mọi nhân của máy."""
    raw = os.environ.get("CONTROL_VIDEO_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    return max(1, os.cpu_count() or 1)


def feeder_count() -> int:
    """Số video đọc cùng lúc. Mặc định một video: các video còn lại nằm trong hàng chờ.

    Một video đã dùng hết các nhân để đọc khung. Đọc thêm video cùng lúc chỉ tranh nhân,
    và một video hỏng làm phải khởi động lại cả nhóm.
    """
    raw = os.environ.get("CONTROL_VIDEO_FEEDERS", "").strip()
    if raw:
        return max(1, int(raw))
    return 1


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
              phone_count INTEGER,
              full_count INTEGER,
              device TEXT NOT NULL DEFAULT ''
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
        _ensure_video_count_columns(conn)
        _ensure_video_device_column(conn)
        _ensure_pc_columns(conn)
        _ensure_check_columns(conn)
        _ensure_fleet_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL DEFAULT '',
              prefix TEXT NOT NULL,
              key_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              revoked_at TEXT,
              last_used_at TEXT
            )
            """
        )
    _READY.add(key)


def _ensure_video_count_columns(conn: sqlite3.Connection) -> None:
    """Cột thống kê thêm sau. CREATE IF NOT EXISTS không sửa bảng đã có."""
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(videos)")}
    for name in ("phone_count", "full_count"):
        if name in columns:
            continue
        try:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {name} INTEGER")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def _ensure_video_device_column(conn: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(videos)")}
    if "device" not in columns:
        conn.execute("ALTER TABLE videos ADD COLUMN device TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_device ON videos(device, created_at)")


def _ensure_pc_columns(conn: sqlite3.Connection) -> None:
    """Cột giữ video cho PC. Bộ đọc trên máy chủ không đụng trạng thái pc."""
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(videos)")}
    if "helper" not in columns:
        conn.execute("ALTER TABLE videos ADD COLUMN helper TEXT NOT NULL DEFAULT ''")
    if "lease" not in columns:
        conn.execute("ALTER TABLE videos ADD COLUMN lease TEXT NOT NULL DEFAULT ''")
    if "lease_at" not in columns:
        conn.execute("ALTER TABLE videos ADD COLUMN lease_at TEXT")


def _ensure_check_columns(conn: sqlite3.Connection) -> None:
    """Lần kiểm đọc lại dòng đủ ba ô. check_pass=1 là video đang được đối chiếu, không phải lần đọc mới."""
    videos = {str(row["name"]) for row in conn.execute("PRAGMA table_info(videos)")}
    if "check_pass" not in videos:
        conn.execute("ALTER TABLE videos ADD COLUMN check_pass INTEGER NOT NULL DEFAULT 0")
    results = {str(row["name"]) for row in conn.execute("PRAGMA table_info(results)")}
    if "checked" not in results:
        conn.execute("ALTER TABLE results ADD COLUMN checked TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_checked ON results(checked) WHERE checked != ''")


def _add_column(conn: sqlite3.Connection, table: str, name: str, kind: str) -> None:
    """Hai tiến trình web cùng khởi động có thể cùng thêm một cột. Cột đã có thì bỏ qua."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _ensure_fleet_columns(conn: sqlite3.Connection) -> None:
    """Bảng PC, mã mời và cột tiến độ. PC bản cũ không gửi tiến độ thì cột để trống."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pcs (
          name TEXT PRIMARY KEY,
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT '',
          message TEXT NOT NULL DEFAULT '',
          version TEXT NOT NULL DEFAULT '',
          invite TEXT NOT NULL DEFAULT '',
          video_id INTEGER,
          paused INTEGER NOT NULL DEFAULT 0,
          schedule TEXT NOT NULL DEFAULT '',
          autostart INTEGER NOT NULL DEFAULT 0,
          fail_streak INTEGER NOT NULL DEFAULT 0,
          excluded INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT '',
          last_error_at TEXT,
          ok_count INTEGER NOT NULL DEFAULT 0,
          fail_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pc_invites (
          id INTEGER PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          label TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          revoked_at TEXT
        )
        """
    )
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(videos)")}
    for name, kind in (
        ("progress_stage", "TEXT NOT NULL DEFAULT ''"),
        ("progress_done", "INTEGER NOT NULL DEFAULT 0"),
        ("progress_total", "INTEGER NOT NULL DEFAULT 0"),
        ("progress_at", "TEXT"),
        ("pc_download_s", "REAL"),
        ("pc_read_s", "REAL"),
        ("pc_fails", "INTEGER NOT NULL DEFAULT 0"),
        ("pc_last_fail", "TEXT NOT NULL DEFAULT ''"),
        ("priority", "INTEGER NOT NULL DEFAULT 0"),
        ("file_sha256", "TEXT NOT NULL DEFAULT ''"),
        ("shrunk", "INTEGER NOT NULL DEFAULT 0"),
        ("upload_bytes", "INTEGER"),
    ):
        if name not in columns:
            _add_column(conn, "videos", name, kind)


def clean_note(raw: object, limit: int = 300) -> str:
    """Lời nhắn hiện trên trang PC: một dòng, ngắn, không ký tự điều khiển."""
    text = " ".join(str(raw or "").split())
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def _pc_seen(conn: sqlite3.Connection, name: str, now: str, **fields: object) -> None:
    """Ghi PC vừa liên lạc. Tên cột chỉ đến từ mã chương trình, không từ dữ liệu PC gửi lên."""
    conn.execute(
        """
        INSERT INTO pcs (name, first_seen, last_seen) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen
        """,
        (name, now, now),
    )
    if fields:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE pcs SET {assignments} WHERE name = ?", (*fields.values(), name))


def queued_bytes(db_path: Path) -> int:
    """Dung lượng video còn file trên đĩa: đang tải, đang chờ, đang đọc, đã xong và lỗi."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0) AS n FROM videos
            WHERE status IN ('uploading', 'queued', 'running', 'pc')
               OR (status IN ('done', 'error') AND path != '')
            """
        ).fetchone()
    return int(row["n"])


def can_accept(used_bytes: int, new_bytes: int, limit_bytes: int, free_bytes: int) -> bool:
    if new_bytes > limit_bytes or used_bytes + new_bytes > limit_bytes:
        return False
    return free_bytes > new_bytes + (2 * 1024 * 1024 * 1024)


def disk_limit_bytes(total_bytes: int, percent: int) -> int:
    """Phần dung lượng ổ đĩa được dùng cho video. Luôn chừa ít nhất 10% cho hệ thống."""
    bounded = min(90, max(10, percent))
    return total_bytes * bounded // 100


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return True


def prune_old_videos(
    db_path: Path,
    video_dir: Path,
    *,
    percent: int = 80,
    protect_newest: bool = True,
    measure=None,
) -> int:
    """Xóa file video cũ nhất khi ổ đĩa vượt mức phần trăm.

    Video đang tải, đang chờ và đang đọc không bị xóa. Dòng kết quả vẫn giữ.
    Lần tải video mới truyền protect_newest=False vì file đang tải mới hơn mọi file đã xong.
    Nếu xóa hết video cũ vẫn không đủ chỗ thì giữ nguyên file đó: lần tải bị từ chối.
    Đường dẫn trỏ tới file đã mất được gỡ, và không được tính là dung lượng vừa giải phóng.
    """
    root = video_dir.resolve()
    read_disk = measure or (lambda: shutil.disk_usage(root))
    usage = read_disk()
    limit = disk_limit_bytes(int(usage.total), percent)
    if int(usage.used) <= limit:
        return 0
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id, path, size_bytes, created_at FROM videos
            WHERE status IN ('done', 'error') AND path != ''
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        if protect_newest and rows:
            newest = max(rows, key=lambda row: (str(row["created_at"]), int(row["id"])))
            rows = [row for row in rows if int(row["id"]) != int(newest["id"])]
        candidates: list[tuple[int, str, int]] = []
        for row in rows:
            path = Path(str(row["path"]))
            if not _path_inside(path, root):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                # File đã mất từ trước. Gỡ đường dẫn, nhưng không tính là đã giải phóng đĩa.
                size = 0
            if size < 0:
                size = 0
            candidates.append((int(row["id"]), str(path), size))
        freeable = sum(size for _, _, size in candidates)
        used = int(usage.used)
        # Đường dẫn mồ côi luôn gỡ được. File thật chỉ xóa khi xóa hết nhóm đó thì đĩa xuống dưới mức.
        chosen = [item for item in candidates if item[2] == 0]
        if used - freeable <= limit:
            projected = used
            for item in candidates:
                if item[2] == 0 or projected <= limit:
                    continue
                chosen.append(item)
                projected -= item[2]
        removed: list[str] = []
        for video_id, path, _size in chosen:
            cur = conn.execute(
                """
                UPDATE videos SET path = ''
                WHERE id = ? AND status IN ('done', 'error') AND path = ?
                """,
                (video_id, path),
            )
            if cur.rowcount == 1 and _size > 0:
                removed.append(path)
        conn.execute("COMMIT")
    for path in removed:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            continue
    return len(removed)


def begin_upload(
    db_path: Path, *, name: str, size_bytes: int, sha256: str, device: str = ""
) -> dict[str, object]:
    """Cùng một file đang chờ hoặc đang đọc thì bỏ qua. Đã đọc xong hoặc lỗi thì đọc lại bằng bộ đọc hiện tại.

    device là tên điện thoại người dùng đã chọn trên trang tải. File đang chờ giữ tên máy đã tải trước.
    Tải lại file đã xong thì gắn máy đang tải lần này; tải lại mà không gửi tên thì giữ tên cũ.
    """
    device = clean_device(device)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, status, name, path, device FROM videos WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if existing and existing["status"] in ("done", "error"):
            kept = device or str(existing["device"] or "")
            conn.execute(
                """
                UPDATE videos
                SET name = ?, size_bytes = ?, path = '', status = 'uploading', error = '',
                    pid = NULL, started_at = NULL, finished_at = NULL, created_at = ?,
                    phone_count = NULL, full_count = NULL, device = ?,
                    file_sha256 = '', shrunk = 0, upload_bytes = NULL,
                    pc_fails = 0, pc_last_fail = '', priority = 0
                WHERE id = ?
                """,
                (name, size_bytes, utcnow(), kept, existing["id"]),
            )
            conn.execute("COMMIT")
            return {
                "id": int(existing["id"]),
                "status": "uploading",
                "name": name,
                "device": kept,
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
                "device": str(existing["device"] or ""),
                "duplicate": True,
            }
        cur = conn.execute(
            """
            INSERT INTO videos (name, size_bytes, sha256, path, status, created_at, device)
            VALUES (?, ?, ?, '', 'uploading', ?, ?)
            """,
            (name, size_bytes, sha256, utcnow(), device),
        )
        conn.execute("COMMIT")
        return {
            "id": int(cur.lastrowid),
            "status": "uploading",
            "name": name,
            "device": device,
            "duplicate": False,
        }


def commit_upload(db_path: Path, video_id: int, path: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET path = ?, status = 'queued' WHERE id = ? AND status = 'uploading'",
            (path, video_id),
        )


def get_meta(db_path: Path, key: str, default: str = "") -> str:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else default


def set_meta(db_path: Path, key: str, value: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# Không thu nhỏ vài video đầu hàng chờ: máy sắp nhận chúng, thu nhỏ xong cũng không kịp dùng.
SHRINK_SKIP = 2
SMALL_SUFFIX = ".small.mp4"


def next_to_shrink(db_path: Path, skip: int = SHRINK_SKIP) -> dict[str, object] | None:
    """Video đang chờ chưa thu nhỏ, theo thứ tự đọc, bỏ qua vài video đầu hàng."""
    with connect(db_path) as conn:
        rows = conn.execute(_waiting_sql(first_only=False)).fetchall()
    for index, row in enumerate(rows):
        if index >= skip and int(row["shrunk"]) == 0:
            return {"id": int(row["id"]), "path": str(row["path"]), "bytes": int(row["size_bytes"] or 0)}
    return None


def adopt_shrunk(db_path: Path, video_id: int, old_path: str, new_path: Path, new_sha: str) -> bool:
    """Đổi file chờ đọc sang bản thu nhỏ, chỉ khi video vẫn đang chờ và chưa máy nào nhận.

    Mã băm lúc tải lên giữ nguyên để nhận ra lần tải lại cùng file. PC kiểm file tải về bằng mã băm mới.
    Bản thu nhỏ không nhỏ hơn thì giữ file gốc.
    """
    size = new_path.stat().st_size
    final = new_path.with_name(f"{video_id}{SMALL_SUFFIX}")
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, path, size_bytes FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None or row["status"] != "queued" or str(row["path"]) != old_path or old_path == str(final):
            conn.execute("ROLLBACK")
            new_path.unlink(missing_ok=True)
            return False
        if size >= int(row["size_bytes"] or 0):
            conn.execute("UPDATE videos SET shrunk = 2 WHERE id = ?", (video_id,))
            conn.execute("COMMIT")
            new_path.unlink(missing_ok=True)
            return False
        try:
            os.replace(new_path, final)
            conn.execute(
                """
                UPDATE videos
                SET path = ?, size_bytes = ?, file_sha256 = ?, shrunk = 1,
                    upload_bytes = COALESCE(upload_bytes, size_bytes)
                WHERE id = ?
                """,
                (str(final), size, new_sha, video_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            final.unlink(missing_ok=True)
            raise
    Path(old_path).unlink(missing_ok=True)
    return True


def drop_orphan_small(db_path: Path, video_dir: Path) -> int:
    """Bản thu nhỏ còn sót khi tiến trình đọc tắt giữa chừng, không video nào trỏ tới."""
    if not video_dir.is_dir():
        return 0
    with connect(db_path) as conn:
        used = {str(row["path"]) for row in conn.execute("SELECT path FROM videos WHERE path LIKE ?", (f"%{SMALL_SUFFIX}",))}
    removed = 0
    for path in video_dir.glob(f"*{SMALL_SUFFIX}"):
        if str(path) not in used:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def shrink_failed(db_path: Path, video_id: int) -> None:
    """Thu nhỏ hỏng thì giữ file gốc và không thử lại video đó."""
    with connect(db_path) as conn:
        conn.execute("UPDATE videos SET shrunk = 2 WHERE id = ? AND shrunk = 0", (video_id,))


def shrink_totals(db_path: Path) -> dict[str, int]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(upload_bytes - size_bytes), 0) AS saved
            FROM videos WHERE shrunk = 1 AND upload_bytes IS NOT NULL
            """
        ).fetchone()
    return {"shrunk": int(row["n"]), "saved_bytes": int(row["saved"])}


def drop_stale_uploads(db_path: Path, older_than_seconds: float = 1800) -> int:
    """Tải lên dở hơn 30 phút là trình duyệt đã đóng giữa chừng. Xóa dòng đó để nó không chiếm chỗ hàng đợi."""
    cutoff = time.time() - older_than_seconds
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, created_at FROM videos WHERE status = 'uploading'").fetchall()
        dropped = 0
        for row in rows:
            try:
                created = datetime.fromisoformat(str(row["created_at"])).timestamp()
            except ValueError:
                created = 0
            if created > cutoff:
                continue
            conn.execute("DELETE FROM videos WHERE id = ? AND status = 'uploading'", (row["id"],))
            dropped += 1
        return dropped


def abort_upload(db_path: Path, video_id: int, reopened: bool = False) -> None:
    with connect(db_path) as conn:
        if reopened:
            conn.execute(
                "UPDATE videos SET status = 'error', error = ? WHERE id = ? AND status = 'uploading'",
                ("Tải lên bị gián đoạn. Tải lại video.", video_id),
            )
            return
        conn.execute("DELETE FROM videos WHERE id = ? AND status = 'uploading'", (video_id,))


def list_videos(db_path: Path, limit: int | None = None, device: str | None = None) -> list[dict[str, object]]:
    """Mọi video, mới nhất trước. limit=None là không cắt. device=None là mọi máy.

    Chuỗi device rỗng là các video tải trước khi có tên điện thoại.
    """
    sql = """
        SELECT id, name, size_bytes, status, error, created_at, started_at, finished_at,
               phone_count, full_count, path, device, helper, check_pass,
               progress_stage, progress_done, progress_total, priority
        FROM videos
    """
    params: list[object] = []
    if device is not None:
        sql += " WHERE device = ?"
        params.append(device)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        order = {int(row["id"]): index + 1 for index, row in enumerate(conn.execute(_waiting_sql(first_only=False)))}
    items = []
    for row in rows:
        item = dict(row)
        path = str(item.pop("path") or "")
        item["has_file"] = bool(path) and Path(path).is_file()
        item["helper"] = str(item.get("helper") or "")
        item["check_pass"] = int(item.get("check_pass") or 0)
        item["priority"] = int(item.get("priority") or 0)
        item["position"] = order.get(int(item["id"]))
        busy = item["status"] in ("pc", "running")
        item["progress_stage"] = str(item.get("progress_stage") or "") if busy else ""
        item["progress_done"] = int(item.get("progress_done") or 0) if busy else 0
        item["progress_total"] = int(item.get("progress_total") or 0) if busy else 0
        items.append(item)
    return items


def stats(db_path: Path) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "done": 0, "error": 0, "uploading": 0, "pc": 0}
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
        matched = conn.execute("SELECT COUNT(*) AS n FROM results WHERE checked = 'ok'").fetchone()
        differed = conn.execute("SELECT COUNT(*) AS n FROM results WHERE checked = 'diff'").fetchone()
        checking = conn.execute(
            """
            SELECT COUNT(*) AS n FROM videos
            WHERE COALESCE(check_pass, 0) = 1 AND status IN ('queued', 'running', 'pc')
            """
        ).fetchone()
    counts.update(buckets)
    counts["matched"] = int(matched["n"])
    counts["differed"] = int(differed["n"])
    counts["checking"] = int(checking["n"])
    counts["results"] = buckets["saved"] + buckets["review"] + buckets["unopened"]
    return counts


def device_day(db_path: Path, now: datetime | None = None) -> dict[str, object]:
    """Số video mỗi điện thoại đã tải lên trong ngày hôm nay, và số video lỗi trong ngày đó.

    Ngày tính theo giờ Việt Nam. Đã tải là file đã vào hàng chờ hôm nay, kể cả đang đọc hoặc đã xong.
    Lỗi là video đọc hỏng hoặc tải lại bị đứt trong ngày, dù file đó lên từ hôm trước.
    Video đang tải dở chưa tính là đã tải.
    """
    day, start, end = vn_day_bounds(now)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT device,
              COALESCE(SUM(CASE
                WHEN status != 'uploading' AND created_at >= ? AND created_at < ? THEN 1 ELSE 0 END), 0) AS uploaded,
              COALESCE(SUM(CASE
                WHEN status = 'error' AND (
                  (finished_at >= ? AND finished_at < ?)
                  OR ((finished_at IS NULL OR finished_at = '') AND created_at >= ? AND created_at < ?)
                ) THEN 1 ELSE 0 END), 0) AS errors,
              COALESCE(SUM(CASE
                WHEN status = 'done' AND finished_at >= ? AND finished_at < ? THEN 1 ELSE 0 END), 0) AS done,
              COALESCE(SUM(CASE
                WHEN status IN ('queued', 'running', 'uploading', 'pc') THEN 1 ELSE 0 END), 0) AS waiting
            FROM videos
            GROUP BY device
            ORDER BY uploaded DESC, waiting DESC, device ASC
            """,
            (start, end, start, end, start, end, start, end),
        ).fetchall()
    items = []
    known: list[str] = []
    for row in rows:
        device = str(row["device"] or "")
        item = {
            "device": device,
            "uploaded": int(row["uploaded"]),
            "errors": int(row["errors"]),
            "done": int(row["done"]),
            "waiting": int(row["waiting"]),
        }
        if device:
            known.append(device)
        if device or item["uploaded"] or item["errors"] or item["done"] or item["waiting"]:
            items.append(item)
    return {"day": day, "known": known, "items": items}


def _waiting_sql(where: str = "", first_only: bool = True) -> str:
    """Thứ tự đọc chung cho máy chủ và PC.

    Video gấp trước. Rồi video mới trước video của lần kiểm. Rồi xoay vòng theo điện thoại:
    video thứ k của mỗi máy đứng trước video thứ k+1 của bất kỳ máy nào, nên một đợt lớn
    của một máy không chặn video của máy khác. Cùng lượt thì video vào trước đọc trước.
    """
    return f"""
        WITH waiting AS (
          SELECT id, name, path, size_bytes, sha256, COALESCE(file_sha256, '') AS file_sha256,
                 COALESCE(shrunk, 0) AS shrunk,
                 COALESCE(check_pass, 0) AS check_pass,
                 COALESCE(priority, 0) AS priority,
                 COALESCE(pc_fails, 0) AS pc_fails,
                 COALESCE(pc_last_fail, '') AS pc_last_fail,
                 ROW_NUMBER() OVER (PARTITION BY device, COALESCE(check_pass, 0) ORDER BY id) AS turn
          FROM videos
          WHERE status = 'queued' AND path != ''
        )
        SELECT id, name, path, size_bytes, sha256, file_sha256, shrunk, check_pass, priority FROM waiting
        {where}
        ORDER BY priority DESC, check_pass, turn, id
        {"LIMIT 1" if first_only else ""}
    """


def queue_order(db_path: Path) -> list[int]:
    """Video đang chờ, theo đúng thứ tự sẽ được đọc."""
    with connect(db_path) as conn:
        return [int(row["id"]) for row in conn.execute(_waiting_sql(first_only=False))]


def set_priority(db_path: Path, video_id: int, urgent: bool) -> bool:
    """Đọc gấp một video đang chờ hoặc đang tải lên. Đọc xong thì cờ gấp tự bỏ."""
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE videos SET priority = ? WHERE id = ? AND status IN ('queued', 'uploading')",
            (1 if urgent else 0, video_id),
        )
        return cur.rowcount == 1


def claim(db_path: Path, pid: int) -> dict[str, object] | None:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(_waiting_sql()).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        lease = secrets.token_hex(16)
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'running', pid = ?, started_at = ?, error = '',
                helper = '', lease = ?, lease_at = NULL,
                progress_stage = '', progress_done = 0, progress_total = 0, progress_at = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (pid, utcnow(), lease, row["id"]),
        )
        conn.execute("COMMIT")
        if cur.rowcount != 1:
            return None
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "path": row["path"],
            "check": int(row["check_pass"]),
            "lease": lease,
        }


def _release_stale_pc(conn: sqlite3.Connection, seconds: int = PC_LEASE_SECONDS) -> int:
    """Gọi trong một giao dịch đang mở, để video chọn ra đúng là video được trả về."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    stale = conn.execute(
        """
        SELECT id, helper FROM videos
        WHERE status = 'pc' AND (lease_at IS NULL OR lease_at = '' OR lease_at < ?)
        """,
        (cutoff,),
    ).fetchall()
    if not stale:
        return 0
    cur = conn.execute(
        """
        UPDATE videos
        SET status = 'queued', pid = NULL, started_at = NULL, error = '',
            lease = '', helper = '', lease_at = NULL,
            progress_stage = '', progress_done = 0, progress_total = 0
        WHERE status = 'pc' AND (lease_at IS NULL OR lease_at = '' OR lease_at < ?)
        """,
        (cutoff,),
    )
    now = utcnow()
    for row in stale:
        helper = str(row["helper"] or "")
        if not helper:
            continue
        # PC tắt cửa sổ giữa chừng không phải lỗi đọc, nên không tính vào chuỗi lỗi.
        conn.execute(
            """
            UPDATE pcs SET last_error = ?, last_error_at = ?, video_id = NULL
            WHERE name = ? AND (video_id IS NULL OR video_id = ?)
            """,
            (f"Mất nhịp khi đọc video {int(row['id'])}. Video đã trả về hàng chờ.", now, helper, int(row["id"])),
        )
    return cur.rowcount


def release_stale_pc(db_path: Path, seconds: int = PC_LEASE_SECONDS) -> int:
    """Video PC đã nhận nhưng không còn nhịp thì trở lại hàng chờ cho máy chủ hoặc PC khác."""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        released = _release_stale_pc(conn, seconds)
        conn.execute("COMMIT")
        return released


def claim_pc(db_path: Path, helper: str) -> dict[str, object] | None:
    """Đưa một video đang chờ cho PC. Trạng thái pc, bộ đọc trên máy chủ không nhận.

    PC không nhận lại video nó vừa đọc hỏng. Video đã hỏng trên hai PC thì chỉ để máy chủ
    đọc: lỗi có thể nằm ở video chứ không ở PC.
    """
    name = clean_device(helper) or "PC"
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _release_stale_pc(conn)
        for _ in range(20):
            row = conn.execute(
                _waiting_sql(f"WHERE pc_fails < {PC_VIDEO_FAIL_LIMIT} AND pc_last_fail != ?"),
                (name,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            if not Path(str(row["path"])).is_file():
                conn.execute(
                    """
                    UPDATE videos
                    SET status = 'error', error = ?, finished_at = ?, pid = NULL, check_pass = 0
                    WHERE id = ? AND status = 'queued'
                    """,
                    ("Không còn file video.", utcnow(), row["id"]),
                )
                continue
            lease = secrets.token_hex(16)
            now = utcnow()
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'pc', pid = NULL, started_at = ?, error = '',
                    helper = ?, lease = ?, lease_at = ?,
                    progress_stage = 'download', progress_done = 0, progress_total = ?, progress_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, name, lease, now, int(row["size_bytes"] or 0), now, row["id"]),
            )
            if cur.rowcount != 1:
                continue
            _pc_seen(conn, name, now, video_id=int(row["id"]), state="reading", message="")
            conn.execute("COMMIT")
            return {
                "id": int(row["id"]),
                "bytes": int(row["size_bytes"]),
                "sha256": str(row["file_sha256"] or row["sha256"]),
                "lease": lease,
            }
        conn.execute("COMMIT")
        return None


PROGRESS_STAGES = {"download", "extract", "scan", "read", "send"}


def touch_pc(db_path: Path, video_id: int, lease: str, stage: str = "", done: int = 0, total: int = 0) -> bool:
    """Nhịp của PC đang giữ video, kèm bước đang làm và tiến độ. PC bản cũ chỉ gửi phiếu."""
    if not lease:
        return False
    now = utcnow()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT helper FROM videos WHERE id = ? AND status = 'pc' AND lease = ?",
            (video_id, lease),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return False
        if stage in PROGRESS_STAGES:
            conn.execute(
                """
                UPDATE videos
                SET lease_at = ?, progress_stage = ?, progress_done = ?, progress_total = ?, progress_at = ?
                WHERE id = ?
                """,
                (now, stage, max(0, int(done)), max(0, int(total)), now, video_id),
            )
        else:
            conn.execute("UPDATE videos SET lease_at = ? WHERE id = ?", (now, video_id))
        helper = str(row["helper"] or "")
        if helper:
            _pc_seen(conn, helper, now, video_id=video_id, state="reading")
        conn.execute("COMMIT")
        return True


def release_pc(db_path: Path, video_id: int, lease: str, reason: str = "fail", error: str = "") -> bool:
    """PC đọc hỏng thì trả video về hàng chờ. Phiếu không khớp thì không đụng video của máy khác.

    reason="disk" là PC thiếu chỗ, không phải lỗi đọc: không tính vào lỗi của PC hay của video.
    Một PC hỏng PC_FAIL_LIMIT video liền nhau thì bị loại, cho đến khi trang bấm cho đọc lại.
    Video đã hỏng trên PC khác thì lần hỏng này không tính vào chuỗi lỗi của PC.
    """
    if not lease:
        return False
    counted = reason != "disk"
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT helper, COALESCE(pc_fails, 0) AS pc_fails FROM videos WHERE id = ? AND status = 'pc' AND lease = ?",
            (video_id, lease),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return False
        helper = str(row["helper"] or "")
        conn.execute(
            """
            UPDATE videos
            SET status = 'queued', pid = NULL, started_at = NULL, error = '',
                lease = '', helper = '', lease_at = NULL,
                progress_stage = '', progress_done = 0, progress_total = 0,
                pc_fails = COALESCE(pc_fails, 0) + ?,
                pc_last_fail = CASE WHEN ? THEN ? ELSE COALESCE(pc_last_fail, '') END
            WHERE id = ?
            """,
            (1 if counted else 0, 1 if counted else 0, helper, video_id),
        )
        if helper:
            now = utcnow()
            if counted:
                note = clean_note(error) or f"Không đọc xong video {video_id}."
                streak = 1 if int(row["pc_fails"]) == 0 else 0
                conn.execute(
                    """
                    UPDATE pcs
                    SET fail_count = fail_count + 1, fail_streak = fail_streak + ?,
                        last_error = ?, last_error_at = ?, video_id = NULL, last_seen = ?, state = 'idle'
                    WHERE name = ?
                    """,
                    (streak, f"Video {video_id}: {note}", now, now, helper),
                )
                conn.execute(
                    "UPDATE pcs SET excluded = 1 WHERE name = ? AND fail_streak >= ?",
                    (helper, PC_FAIL_LIMIT),
                )
            else:
                conn.execute(
                    "UPDATE pcs SET video_id = NULL, last_seen = ?, state = 'wait-disk' WHERE name = ?",
                    (now, helper),
                )
        conn.execute("COMMIT")
        return True


def report_progress(
    db_path: Path, video_id: int, stage: str, done: int, total: int, lease: str | None = None
) -> None:
    """Tiến độ video máy chủ đang đọc, để trang và giờ dự kiến biết video đang tới đâu.

    Có phiếu thì chỉ ghi khi video vẫn thuộc đúng lượt đọc đó.
    """
    if stage not in PROGRESS_STAGES:
        return
    now = utcnow()
    where = "WHERE id = ? AND status = 'running'"
    params: list[object] = [stage, max(0, int(done)), max(0, int(total)), now, video_id]
    if lease:
        where += " AND lease = ?"
        params.append(lease)
    with connect(db_path) as conn:
        conn.execute(
            f"""
            UPDATE videos SET progress_stage = ?, progress_done = ?, progress_total = ?, progress_at = ?
            {where}
            """,
            params,
        )


def record_pc_result(
    db_path: Path, video_id: int, download_s: float | None = None, read_s: float | None = None
) -> None:
    """Sau khi lưu kết quả PC gửi: ghi thời gian tải và thời gian đọc, xoá chuỗi lỗi của PC đó."""
    def seconds(value: float | None) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 1) if 0 <= number < 7 * 24 * 3600 else None

    now = utcnow()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT helper FROM videos WHERE id = ?", (video_id,)).fetchone()
        conn.execute(
            """
            UPDATE videos
            SET pc_download_s = ?, pc_read_s = ?, progress_stage = '', progress_done = 0, progress_total = 0
            WHERE id = ?
            """,
            (seconds(download_s), seconds(read_s), video_id),
        )
        helper = str(row["helper"] or "") if row is not None else ""
        if helper and helper != "VPS":
            _pc_seen(conn, helper, now)
            conn.execute(
                """
                UPDATE pcs SET ok_count = ok_count + 1, fail_streak = 0, video_id = NULL, state = 'idle'
                WHERE name = ?
                """,
                (helper,),
            )
        conn.execute("COMMIT")


def pc_video_path(db_path: Path, video_id: int, lease: str, root: Path) -> Path | None:
    """File video chỉ khi PC còn giữ đúng phiếu, và file nằm trong thư mục video."""
    if not lease:
        return None
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT path FROM videos WHERE id = ? AND status = 'pc' AND lease = ?",
            (video_id, lease),
        ).fetchone()
    if row is None:
        return None
    path = Path(str(row["path"] or ""))
    if not path.is_file() or not _path_inside(path, root):
        return None
    return path


def requeue(db_path: Path, video_id: int, lease: str | None = None) -> bool:
    """Đưa video đang đọc về hàng chờ. Có phiếu thì không đụng lượt đọc khác đã nhận video này."""
    where = "WHERE id = ? AND status = 'running'"
    params: list[object] = [video_id]
    if lease is not None:
        where += " AND lease = ?"
        params.append(lease)
    with connect(db_path) as conn:
        cur = conn.execute(
            f"""
            UPDATE videos
            SET status = 'queued', pid = NULL, started_at = NULL,
                lease = '', lease_at = NULL, helper = '',
                progress_stage = '', progress_done = 0, progress_total = 0, progress_at = NULL
            {where}
            """,
            params,
        )
        return cur.rowcount == 1


def requeue_running(db_path: Path, keep_pid: int) -> int:
    """Lúc bộ đọc khởi động, mọi video đang đọc dở thuộc tiến trình cũ đã chết. Không dựa vào pid còn sống,
    vì sau khi khởi động lại máy, pid cũ có thể đã thuộc về chương trình khác."""
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'queued', pid = NULL, started_at = NULL,
                lease = '', lease_at = NULL,
                progress_stage = '', progress_done = 0, progress_total = 0, progress_at = NULL
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
                SET status = 'queued', pid = NULL, started_at = NULL,
                    lease = '', lease_at = NULL,
                    progress_stage = '', progress_done = 0, progress_total = 0, progress_at = NULL
                WHERE id = ? AND status = 'running'
                """,
                (row["id"],),
            )
            recovered += 1
        return recovered


def video_counts(table: Table) -> tuple[int, int]:
    """Số điện thoại gặp trong video, và số hàng chắc chắn đủ số, tên, id.

    Hàng cần xem không tính là đủ ba ô. Cùng một số ở nhiều chỗ chỉ tính một lần.
    """
    phones: set[str] = set()
    complete: set[str] = set()
    for row in table.rows:
        if not row.phone:
            continue
        phones.add(row.phone)
        if row.name and row.username:
            complete.add(row.phone)
    for item in table.unopened:
        if item.phone:
            phones.add(item.phone)
    for item in table.review:
        if item.phone:
            phones.add(item.phone)
    return len(phones), len(complete)


def _cell(value: str) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _confirm_saved_rows(conn: sqlite3.Connection, video_id: int, table: Table) -> None:
    """Đối chiếu lần đọc mới với dòng đã đủ ba ô của đúng video này.

    Khớp thì ghi đã kiểm. Lệch thì giữ nguyên số, tên và id đã lưu, chỉ ghi lần
    đọc sau vào lý do. Không thấy lại thì bỏ dấu khớp cũ, không xóa dòng.
    Dòng của video khác và dòng chưa đủ ba ô không bị đụng tới.
    """
    fresh: dict[str, tuple[str, str]] = {}
    for row in table.rows:
        phone = _cell(row.phone)
        name = _cell(row.name)
        username = _cell(row.username)
        if phone and name and username:
            fresh[phone] = (name, username)
    saved = conn.execute(
        """
        SELECT id, phone, name, username FROM results
        WHERE video_id = ? AND bucket = ? AND phone != '' AND name != '' AND username != ''
        """,
        (video_id, BUCKET_OK),
    ).fetchall()
    for item in saved:
        found = fresh.get(_cell(str(item["phone"])))
        if found is None:
            conn.execute("UPDATE results SET checked = '' WHERE id = ?", (int(item["id"]),))
            continue
        if found == (_cell(str(item["name"])), _cell(str(item["username"]))):
            conn.execute(
                "UPDATE results SET checked = 'ok', reason = '' WHERE id = ?",
                (int(item["id"]),),
            )
            continue
        note = f"Lần đọc sau: {found[0]} {found[1]}"
        conn.execute(
            "UPDATE results SET checked = 'diff', reason = ? WHERE id = ?",
            (note[:500], int(item["id"])),
        )


def _mark_video_done(
    conn: sqlite3.Connection,
    video_id: int,
    lease: str | None,
    counts: tuple[int, int] | None,
) -> bool:
    where = "WHERE id = ?"
    helper_sql = "helper = helper" if lease is not None else "helper = 'VPS'"
    if counts is None:
        params: list[object] = [utcnow(), video_id]
        count_sql = ""
    else:
        params = [utcnow(), counts[0], counts[1], video_id]
        count_sql = "phone_count = ?, full_count = ?,"
    if lease is not None:
        where += " AND lease = ? AND status IN ('pc', 'running')"
        params.append(lease)
        helper_sql = "helper = CASE WHEN status = 'pc' THEN helper ELSE 'VPS' END"
    cur = conn.execute(
        f"""
        UPDATE videos
        SET status = 'done', pid = NULL, finished_at = ?, error = '',
            {count_sql}
            lease = '', {helper_sql}, lease_at = NULL, check_pass = 0, priority = 0,
            progress_stage = '', progress_done = 0, progress_total = 0, progress_at = NULL
        {where}
        """,
        params,
    )
    return cur.rowcount == 1


def finish(db_path: Path, video_id: int, table: Table, lease: str | None = None) -> bool:
    """Ghi kết quả một lần đọc video.

    Đọc lại một video thì bỏ các dòng cần xem và chưa mở hồ sơ mà chính video đó
    để lại từ lần đọc trước, rồi ghi kết quả mới; nếu không, dòng sai của bộ đọc
    cũ nằm lại mãi. Dòng đã lưu của video đó được thay bằng dòng đã lưu mới.
    Dòng do video khác tạo ra không bị đụng tới.

    Video đang ở lần kiểm thì không ghi đè. Chỉ đối chiếu các dòng đã đủ ba ô.

    Có phiếu thì chỉ ghi khi video vẫn thuộc đúng lượt đó: máy chủ đang đọc,
    hoặc PC đang giữ. Phiếu sai, hoặc video đã sang lượt khác, thì không đổi
    dòng nào.
    """
    phones, full = video_counts(table)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if lease is not None:
            owned = conn.execute(
                """
                SELECT id FROM videos
                WHERE id = ? AND lease = ? AND lease != '' AND status IN ('pc', 'running')
                """,
                (video_id, lease),
            ).fetchone()
            if owned is None:
                conn.execute("ROLLBACK")
                return False
        mode = conn.execute(
            "SELECT COALESCE(check_pass, 0) AS check_pass FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if mode is not None and int(mode["check_pass"]) == 1:
            _confirm_saved_rows(conn, video_id, table)
            saved = _mark_video_done(conn, video_id, lease, None)
        else:
            conn.execute("DELETE FROM results WHERE video_id = ? AND bucket != ?", (video_id, BUCKET_OK))
            for row in table.rows:
                _put(conn, row.phone, row.name, row.username, BUCKET_OK, "", video_id, upgrade=True)
            for item in table.unopened:
                _put(conn, item.phone, item.name, "", BUCKET_UNOPENED, "", video_id, upgrade=False)
            for item in table.review:
                _put_review(conn, item, video_id)
            saved = _mark_video_done(conn, video_id, lease, (phones, full))
        if not saved:
            conn.execute("ROLLBACK")
            return False
        conn.execute("COMMIT")
    return True


def start_full_check(db_path: Path) -> dict[str, int]:
    """Đưa vào hàng chờ mọi video còn file và đã có ít nhất một dòng đủ ba ô.

    Video đang tải, đang chờ, đang đọc hoặc PC đang giữ thì không đụng tới.
    Dòng đã khớp từ lần kiểm trước được giữ cho đến khi video đó được đọc lại.
    """
    queued = 0
    rows = 0
    missing = 0
    busy = 0
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        found = conn.execute(
            """
            SELECT v.id, v.status, v.path, COUNT(r.id) AS n
            FROM videos v
            JOIN results r ON r.video_id = v.id
            WHERE r.bucket = ? AND r.phone != '' AND r.name != '' AND r.username != ''
            GROUP BY v.id
            """,
            (BUCKET_OK,),
        ).fetchall()
        for row in found:
            status = str(row["status"])
            if status in ("queued", "running", "uploading", "pc"):
                busy += 1
                continue
            if status not in ("done", "error"):
                continue
            path = str(row["path"] or "")
            if not path or not Path(path).is_file():
                missing += 1
                continue
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'queued', error = '', pid = NULL,
                    check_pass = 1, lease = '', lease_at = NULL
                WHERE id = ? AND status IN ('done', 'error')
                """,
                (int(row["id"]),),
            )
            if cur.rowcount != 1:
                busy += 1
                continue
            queued += 1
            rows += int(row["n"])
        conn.execute("COMMIT")
    return {"videos": queued, "rows": rows, "missing": missing, "busy": busy}


def stop_full_check(db_path: Path) -> dict[str, int]:
    """Dừng các video kiểm chưa bắt đầu đọc. Video đang đọc vẫn kiểm nốt."""
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'done', check_pass = 0, pid = NULL, error = '',
                lease = '', lease_at = NULL
            WHERE status = 'queued' AND COALESCE(check_pass, 0) = 1
            """
        )
        reading = conn.execute(
            """
            SELECT COUNT(*) AS n FROM videos
            WHERE COALESCE(check_pass, 0) = 1 AND status IN ('running', 'pc')
            """
        ).fetchone()
    return {"stopped": cur.rowcount, "reading": int(reading["n"])}


def backfill_video_counts(db_path: Path) -> int:
    """Điền thống kê cho video đã xong từ trước, khi còn dòng trống.

    Chỉ bộ đọc gọi hàm này, trong lần quét định kỳ. Trang tải video không gọi.
    Quét bảng kết quả nhiều nhất một lần: sau đó mọi video đã xong đều có số.
    """
    with connect(db_path) as conn:
        pending = conn.execute(
            "SELECT 1 FROM videos WHERE status = 'done' AND phone_count IS NULL LIMIT 1"
        ).fetchone()
        if pending is None:
            return 0
        grouped = conn.execute(
            """
            SELECT video_id,
                   COUNT(DISTINCT phone) AS phones,
                   SUM(CASE
                     WHEN bucket = ? AND name != '' AND username != '' THEN 1
                     ELSE 0
                   END) AS fulls
            FROM results
            WHERE phone != ''
            GROUP BY video_id
            """,
            (BUCKET_OK,),
        ).fetchall()
        filled = 0
        for row in grouped:
            cur = conn.execute(
                """
                UPDATE videos
                SET phone_count = ?, full_count = ?
                WHERE id = ? AND status = 'done' AND phone_count IS NULL
                """,
                (int(row["phones"]), int(row["fulls"] or 0), int(row["video_id"])),
            )
            filled += cur.rowcount
        cur = conn.execute(
            """
            UPDATE videos
            SET phone_count = 0, full_count = 0
            WHERE status = 'done' AND phone_count IS NULL
            """
        )
        filled += cur.rowcount
        return filled


def confirm_open_profiles(db_path: Path) -> int:
    """Đưa vào đã lưu những dòng đã mở hồ sơ mà @id khớp tên danh bạ.

    Chạy một lần trong lần quét của bộ đọc. Không đọc lại video và không chặn trang tải lên.
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        done = conn.execute("SELECT value FROM meta WHERE key = 'confirm_profiles'").fetchone()
        if done is not None:
            conn.execute("COMMIT")
            return 0
        changed = _confirm_open_profiles(conn)
        conn.execute("INSERT INTO meta (key, value) VALUES ('confirm_profiles', ?)", (utcnow(),))
        conn.execute("COMMIT")
    return changed


def _confirm_open_profiles(conn: sqlite3.Connection) -> int:
    changed = 0
    reviews = conn.execute(
        "SELECT id, phone, name, username, reason FROM results WHERE bucket = ?",
        (BUCKET_REVIEW,),
    ).fetchall()
    for row in reviews:
        phone = str(row["phone"])
        username = str(row["username"])
        if not phone or not username or row["reason"] != "tên gần giống, cần xem":
            continue
        if not handle_matches_name(username, str(row["name"])):
            continue
        conn.execute(
            "UPDATE results SET bucket = ?, reason = '' WHERE id = ?",
            (BUCKET_OK, row["id"]),
        )
        changed += 1

    orphans = conn.execute(
        "SELECT id, username FROM results WHERE bucket = ? AND phone = '' AND username != ''",
        (BUCKET_REVIEW,),
    ).fetchall()
    unopened = conn.execute(
        "SELECT id, phone, name FROM results WHERE bucket = ? AND phone != '' AND username = ''",
        (BUCKET_UNOPENED,),
    ).fetchall()
    free = {int(row["id"]): row for row in unopened}
    for orphan in orphans:
        username = str(orphan["username"])
        if conn.execute(
            "SELECT id FROM results WHERE username = ? AND phone != ''",
            (username,),
        ).fetchone():
            continue
        hits = [row for row in free.values() if handle_matches_name(username, str(row["name"]))]
        if len(hits) != 1:
            continue
        target = hits[0]
        conn.execute(
            "UPDATE results SET username = ?, bucket = ?, reason = '' WHERE id = ?",
            (username, BUCKET_OK, target["id"]),
        )
        conn.execute("DELETE FROM results WHERE id = ?", (orphan["id"],))
        del free[int(target["id"])]
        changed += 1
    return changed


def fail(db_path: Path, video_id: int, message: str, lease: str | None = None) -> bool:
    """Đánh video là lỗi. Có phiếu thì chỉ đánh khi video vẫn thuộc đúng lượt đọc đang chạy."""
    where = "WHERE id = ?"
    params: list[object] = [utcnow(), message[:500], video_id]
    if lease is not None:
        where += " AND status = 'running' AND lease = ?"
        params.append(lease)
    with connect(db_path) as conn:
        cur = conn.execute(
            f"""
            UPDATE videos
            SET status = 'error', pid = NULL, finished_at = ?, error = ?,
                helper = CASE WHEN helper IS NULL OR helper = '' THEN 'VPS' ELSE helper END,
                lease = '', lease_at = NULL, check_pass = 0, priority = 0,
                progress_stage = '', progress_done = 0, progress_total = 0
            {where}
            """,
            params,
        )
        return cur.rowcount == 1


def retry(db_path: Path, video_id: int) -> bool:
    """Đưa video lỗi hoặc đã đọc xong về hàng chờ, nếu file còn trên máy chủ.

    Đọc lại dùng bộ đọc hiện tại. Dòng đã lưu của video khác giữ nguyên.
    Dòng đã lưu của chính video này được thay bằng lần đọc mới.
    """
    with connect(db_path) as conn:
        row = conn.execute("SELECT path, status FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None or row["status"] not in ("error", "done") or not row["path"] or not Path(row["path"]).exists():
            return False
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'queued', error = '', finished_at = NULL, pid = NULL,
                phone_count = NULL, full_count = NULL,
                helper = '', lease = '', lease_at = NULL, check_pass = 0
            WHERE id = ? AND status IN ('error', 'done')
            """,
            (video_id,),
        )
        return cur.rowcount == 1


def merge_close_results(db_path: Path) -> int:
    """Ghép số và username cùng một video khi tên gần giống và chỉ có một cặp.

    Video mới đã được ghép như vậy trước khi lưu, nên việc quét cả bảng này chỉ chạy một lần cho dữ liệu cũ.
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            done = conn.execute("SELECT value FROM meta WHERE key = 'close_merge'").fetchone()
            if done is not None:
                conn.execute("COMMIT")
                return 0
            rows = conn.execute(
                """
                SELECT id, phone, name, username, video_id
                FROM results
                WHERE username = '' OR phone = ''
                """
            ).fetchall()
            grouped: dict[int, dict[str, list[sqlite3.Row]]] = defaultdict(lambda: {"phones": [], "users": []})
            for row in rows:
                if row["phone"] and not row["username"]:
                    grouped[int(row["video_id"])]["phones"].append(row)
                elif row["username"] and not row["phone"]:
                    grouped[int(row["video_id"])]["users"].append(row)
            merged = 0
            for items in grouped.values():
                phones = items["phones"]
                users = items["users"]
                pairs = pair_close_names(
                    [(str(row["id"]), row["name"]) for row in phones],
                    [(str(row["id"]), row["name"]) for row in users],
                )
                phone_by = {str(row["id"]): row for row in phones}
                user_by = {str(row["id"]): row for row in users}
                for phone_id, user_id in pairs:
                    phone = phone_by[phone_id]
                    user = user_by[user_id]
                    updated = conn.execute(
                        """
                        UPDATE results
                        SET name = ?, username = ?, bucket = ?, reason = ''
                        WHERE id = ? AND username = ''
                        """,
                        (joined_name(phone["name"], user["name"]), user["username"], BUCKET_OK, phone["id"]),
                    )
                    if updated.rowcount != 1:
                        continue
                    conn.execute("DELETE FROM results WHERE id = ? AND phone = ''", (user["id"],))
                    merged += 1
            conn.execute("INSERT INTO meta (key, value) VALUES ('close_merge', ?)", (utcnow(),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return merged


@dataclass(frozen=True)
class ResultFilter:
    """Bộ lọc kết quả. Ngày theo giờ Việt Nam, dạng YYYY-MM-DD, tính theo lúc dòng được đọc ra."""

    query: str = ""
    day_from: str = ""
    day_to: str = ""
    device: str | None = None
    video_id: int = 0
    bucket: int = 0
    checked: str = ""

    def empty(self) -> bool:
        return self == ResultFilter()


def fold_text(text: str) -> str:
    """Chữ thường, bỏ dấu, đ thành d: gõ "nguyen van" vẫn tìm ra "Nguyễn Văn"."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return bare.replace("đ", "d").replace("Đ", "D").lower()


def _day_start(day: str) -> str:
    try:
        moment = datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=VN_TZ)
    except ValueError as exc:
        raise ValueError(f"Ngày không đúng dạng: {day}") from exc
    return vn_day_bounds(moment)[1]


def _result_where(filters: ResultFilter) -> tuple[str, list[object]]:
    """Điều kiện SQL trên results r và videos v. Chuỗi rỗng là mọi dòng."""
    parts: list[str] = []
    params: list[object] = []
    text = "".join(filters.query.split())
    if text.startswith("+84"):
        text = "0" + text[3:]
    if text.isdigit():
        parts.append("r.phone >= ? AND r.phone < ? AND r.phone != ''")
        params += [text, text + ":"]
    elif text.startswith("@"):
        parts.append("r.username >= ? AND r.username < ? AND r.username != ''")
        params += [text, text + "\U0010ffff"]
    elif text:
        handle = "@" + text
        words = " ".join(filters.query.split())
        pattern = "%" + fold_text(words).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parts.append("((r.username >= ? AND r.username < ? AND r.username != '') OR fold(r.name) LIKE ? ESCAPE '\\')")
        params += [handle, handle + "\U0010ffff", pattern]
    if filters.day_from:
        parts.append("r.created_at >= ?")
        params.append(_day_start(filters.day_from))
    if filters.day_to:
        parts.append("r.created_at < ?")
        try:
            moment = datetime.strptime(filters.day_to, "%Y-%m-%d").replace(hour=12, tzinfo=VN_TZ)
        except ValueError as exc:
            raise ValueError(f"Ngày không đúng dạng: {filters.day_to}") from exc
        params.append(vn_day_bounds(moment)[2])
    if filters.device is not None:
        parts.append("COALESCE(v.device, '') = ?")
        params.append(filters.device)
    if filters.video_id:
        parts.append("r.video_id = ?")
        params.append(int(filters.video_id))
    if filters.bucket:
        parts.append("r.bucket = ?")
        params.append(int(filters.bucket))
    if filters.checked in ("ok", "diff"):
        parts.append("r.checked = ?")
        params.append(filters.checked)
    elif filters.checked == "none":
        parts.append("COALESCE(r.checked, '') = ''")
    return ("WHERE " + " AND ".join(parts), params) if parts else ("", [])


def _with_fold(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.create_function("fold", 1, fold_text, deterministic=True)
    return conn


def result_page(
    db_path: Path,
    query: str = "",
    limit: int = 50,
    before: int = 0,
    after: int = 0,
    filters: ResultFilter | None = None,
) -> dict[str, object]:
    """Một trang kết quả, dòng mới nhất (id lớn hơn) ở trên.

    before lấy các dòng cũ hơn id đó. after lấy các dòng mới hơn id đó.
    Nếu cả hai cùng có thì after thắng. before trong kết quả là id dòng cuối
    khi trang đầy và đang đi xuống, để lấy trang kế. 0 nghĩa là hết trang.
    query là đầu số, đầu @username, hoặc một phần tên (không cần gõ dấu).
    """
    chosen = filters if filters is not None else ResultFilter(query=query)
    where, params = _result_where(chosen)
    page_size = max(1, min(int(limit), 1000))
    cursor_sql = ""
    cursor_params: list[object] = []
    if after > 0:
        cursor_sql = (" AND " if where else " WHERE ") + "r.id > ?"
        cursor_params = [int(after)]
    elif before > 0:
        cursor_sql = (" AND " if where else " WHERE ") + "r.id < ?"
        cursor_params = [int(before)]
    with connect(db_path) as conn:
        _with_fold(conn)
        if where:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM results r LEFT JOIN videos v ON v.id = r.video_id {where}", params
                ).fetchone()["n"]
            )
        else:
            total = int(conn.execute("SELECT COALESCE(SUM(n), 0) AS n FROM result_counts").fetchone()["n"])
        rows = conn.execute(
            f"""
            SELECT r.id, r.phone, r.name, r.username, r.bucket, r.reason, r.video_id, r.created_at,
                   COALESCE(v.helper, '') AS helper, COALESCE(r.checked, '') AS checked,
                   COALESCE(v.device, '') AS device
            FROM results r
            LEFT JOIN videos v ON v.id = r.video_id
            {where}{cursor_sql}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            [*params, *cursor_params, page_size],
        ).fetchall()
    items = [_public_result(row) for row in rows]
    next_before = 0
    if after <= 0 and len(items) == page_size:
        next_before = int(items[-1]["id"])
    return {"items": items, "total": total, "before": next_before}


def iter_results(db_path: Path, filters: ResultFilter) -> Iterator[dict[str, object]]:
    """Mọi dòng khớp bộ lọc, cũ trước mới sau, để xuất file. Đọc trong một lần chụp nhất quán."""
    where, params = _result_where(filters)
    conn = _with_fold(sqlite3.connect(str(db_path), timeout=30, isolation_level=None))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    conn.execute("BEGIN")
    try:
        cursor = conn.execute(
            f"""
            SELECT r.id, r.phone, r.name, r.username, r.bucket, r.reason, r.video_id, r.created_at,
                   COALESCE(v.helper, '') AS helper, COALESCE(r.checked, '') AS checked,
                   COALESCE(v.device, '') AS device, COALESCE(v.name, '') AS video
            FROM results r
            LEFT JOIN videos v ON v.id = r.video_id
            {where}
            ORDER BY r.id
            """,
            params,
        )
        for row in cursor:
            item = _public_result(row)
            item["video"] = str(row["video"] or "")
            yield item
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def search_results(db_path: Path, query: str = "", limit: int = 50) -> list[dict[str, object]]:
    """Tìm theo đầu số hoặc đầu username. Dòng mới nhất ở trên."""
    page = result_page(db_path, query, limit)
    items = page["items"]
    return list(items) if isinstance(items, list) else []


def missing_videos(db_path: Path) -> list[dict[str, object]]:
    """Video đã đọc còn số chưa mở hồ sơ. Chỉ trả số đếm, không trả số điện thoại."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT v.id, COALESCE(v.device, '') AS device,
                   COALESCE(v.phone_count, 0) AS phone_count,
                   COALESCE(v.full_count, 0) AS full_count,
                   COUNT(r.id) AS unopened
            FROM videos v
            JOIN results r ON r.video_id = v.id AND r.bucket = ?
            WHERE v.status = 'done'
            GROUP BY v.id
            ORDER BY v.id DESC
            """,
            (BUCKET_UNOPENED,),
        ).fetchall()
    return [
        {
            "video_id": int(row["id"]),
            "device": str(row["device"] or ""),
            "phone_count": int(row["phone_count"] or 0),
            "full_count": int(row["full_count"] or 0),
            "unopened": int(row["unopened"] or 0),
        }
        for row in rows
    ]


def missing_text(db_path: Path, video_id: int = 0) -> str:
    """File cho Danh bạ: mỗi dòng là tên, dấu tab, số. video_id 0 là mọi video đã đọc."""
    sql = """
        SELECT r.name, r.phone
        FROM results r
        JOIN videos v ON v.id = r.video_id
        WHERE r.bucket = ? AND r.phone != '' AND v.status = 'done'
    """
    params: list[object] = [BUCKET_UNOPENED]
    if video_id > 0:
        sql += " AND r.video_id = ?"
        params.append(int(video_id))
    sql += " ORDER BY r.video_id, r.id"
    lines: list[str] = []
    with connect(db_path) as conn:
        for row in conn.execute(sql, params):
            phone = " ".join(str(row["phone"] or "").split())
            name = " ".join(str(row["name"] or "").split()) or phone
            if phone:
                lines.append(f"{name}\t{phone}")
    return "\n".join(lines) + ("\n" if lines else "")


def coverage_counts(db_path: Path) -> dict[str, object]:
    """Đếm dòng theo nhà mạng gốc. Không trả số điện thoại."""
    labels = {BUCKET_OK: "saved", BUCKET_REVIEW: "review", BUCKET_UNOPENED: "unopened"}
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"saved": 0, "review": 0, "unopened": 0})
    with connect(db_path) as conn:
        rows = conn.execute("SELECT phone, bucket FROM results WHERE phone != ''")
        for row in rows:
            name = carrier(str(row["phone"] or "")) or "Chưa rõ"
            slot = labels.get(int(row["bucket"]), "")
            if slot:
                totals[name][slot] += 1
    carriers = [
        {"carrier": name, **counts, "total": counts["saved"] + counts["review"] + counts["unopened"]}
        for name, counts in totals.items()
    ]
    carriers.sort(key=lambda item: (-int(item["total"]), str(item["carrier"])))
    return {"carriers": carriers}


def iter_backup(db_path: Path) -> Iterator[str]:
    """Ảnh chụp nhất quán các dòng đã commit. Người gọi giữ kết nối đến hết vòng lặp."""
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    conn.execute("BEGIN")
    try:
        yield "Số điện thoại,Tên,Username,Loại,Lý do,Video,Đọc bởi,Kiểm\n"
        cursor = conn.execute(
            """
            SELECT r.phone, r.name, r.username, r.bucket, r.reason,
                   COALESCE(v.name, '') AS video, COALESCE(v.helper, '') AS helper,
                   COALESCE(r.checked, '') AS checked
            FROM results r
            LEFT JOIN videos v ON v.id = r.video_id
            ORDER BY r.id
            """
        )
        for row in cursor:
            yield _csv_line(
                [
                    row["phone"],
                    row["name"],
                    row["username"],
                    BUCKET_LABEL.get(int(row["bucket"]), ""),
                    row["reason"],
                    row["video"],
                    row["helper"],
                    _check_label(str(row["checked"] or "")),
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
        "SELECT id, bucket, username, video_id FROM results WHERE phone = ? AND phone != ''",
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
    if bucket != BUCKET_OK:
        return
    same_video = int(existing["video_id"]) == video_id
    if int(existing["bucket"]) == BUCKET_OK and existing["username"] and not same_video:
        return
    conn.execute(
        "UPDATE results SET name = ?, username = ?, bucket = ?, reason = '', video_id = ?, checked = '' WHERE id = ?",
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
            # Số đang "chưa mở hồ sơ" mà video sau đã mở được hồ sơ: giữ username lại để người xem quyết định.
            if int(existing["bucket"]) == BUCKET_UNOPENED and item.username and not existing["username"]:
                conn.execute(
                    "UPDATE results SET username = ?, bucket = ?, reason = ?, video_id = ? WHERE id = ?",
                    (item.username, BUCKET_REVIEW, item.reason, video_id, existing["id"]),
                )
            return
        conn.execute(
            """
            INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.phone,
                item.contact_name or item.profile_name,
                item.username,
                BUCKET_REVIEW,
                item.reason,
                video_id,
                utcnow(),
            ),
        )
        return
    if not item.username:
        return
    nearby = conn.execute(
        "SELECT username FROM results WHERE username != '' AND length(username) BETWEEN ? AND ?",
        (len(item.username) - 1, len(item.username) + 1),
    ).fetchall()
    # Đã có @id này, hoặc @id chỉ khác một ký tự dễ đọc nhầm (l với i, _ với __): không thêm dòng mồ côi.
    if any(row["username"] == item.username or lookalike_handles(row["username"], item.username) for row in nearby):
        return
    conn.execute(
        """
        INSERT INTO results (phone, name, username, bucket, reason, video_id, created_at)
        VALUES ('', ?, ?, ?, ?, ?, ?)
        """,
        (item.profile_name, item.username, BUCKET_REVIEW, item.reason, video_id, utcnow()),
    )


def _public_result(row: sqlite3.Row) -> dict[str, object]:
    keys = row.keys()
    normalized, network, note = phone_columns(str(row["phone"] or ""))
    return {
        "id": int(row["id"]),
        "phone": row["phone"],
        "name": row["name"],
        "username": row["username"],
        "bucket": BUCKET_LABEL.get(int(row["bucket"]), ""),
        "reason": row["reason"],
        "video_id": int(row["video_id"] or 0),
        "helper": str(row["helper"] or ""),
        "checked": str(row["checked"] or ""),
        "device": str(row["device"] or "") if "device" in keys else "",
        "created_at": str(row["created_at"] or "") if "created_at" in keys else "",
        "normalized": normalized,
        "carrier": network,
        "phone_note": note,
    }


def _check_label(checked: str) -> str:
    if checked == "ok":
        return "Khớp"
    if checked == "diff":
        return "Lệch"
    return ""


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
