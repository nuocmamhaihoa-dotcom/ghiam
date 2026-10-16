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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from doc_video.text import handle_matches_name, lookalike_handles

from control_plane.screen_table import Review, Table, joined_name, pair_close_names

BUCKET_OK = 1
BUCKET_REVIEW = 2
BUCKET_UNOPENED = 3
BUCKET_LABEL = {BUCKET_OK: "Đã lưu", BUCKET_REVIEW: "Cần xem", BUCKET_UNOPENED: "Chưa mở hồ sơ"}
# PC giữ video ở trạng thái pc, không phải running. Bộ đọc trên máy chủ chỉ nhận queued
# và chỉ thu hồi running, nên khởi động lại bộ đọc không cướp video PC đang đọc.
# Không có nhịp trong khoảng này thì video trở lại hàng chờ.
PC_LEASE_SECONDS = 900


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
                    phone_count = NULL, full_count = NULL, device = ?
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
               phone_count, full_count, path, device, helper, check_pass
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
    items = []
    for row in rows:
        item = dict(row)
        path = str(item.pop("path") or "")
        item["has_file"] = bool(path) and Path(path).is_file()
        item["helper"] = str(item.get("helper") or "")
        item["check_pass"] = int(item.get("check_pass") or 0)
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


def claim(db_path: Path, pid: int) -> dict[str, object] | None:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, name, path, COALESCE(check_pass, 0) AS check_pass
            FROM videos
            WHERE status = 'queued' AND path != ''
            ORDER BY COALESCE(check_pass, 0), id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'running', pid = ?, started_at = ?, error = '',
                helper = '', lease = '', lease_at = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (pid, utcnow(), row["id"]),
        )
        conn.execute("COMMIT")
        if cur.rowcount != 1:
            return None
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "path": row["path"],
            "check": int(row["check_pass"]),
        }


def _release_stale_pc(conn: sqlite3.Connection, seconds: int = PC_LEASE_SECONDS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    cur = conn.execute(
        """
        UPDATE videos
        SET status = 'queued', pid = NULL, started_at = NULL, error = '',
            lease = '', helper = '', lease_at = NULL
        WHERE status = 'pc' AND (lease_at IS NULL OR lease_at = '' OR lease_at < ?)
        """,
        (cutoff,),
    )
    return cur.rowcount


def release_stale_pc(db_path: Path, seconds: int = PC_LEASE_SECONDS) -> int:
    """Video PC đã nhận nhưng không còn nhịp thì trở lại hàng chờ cho máy chủ hoặc PC khác."""
    with connect(db_path) as conn:
        return _release_stale_pc(conn, seconds)


def claim_pc(db_path: Path, helper: str) -> dict[str, object] | None:
    """Đưa một video đang chờ cho PC. Trạng thái pc, bộ đọc trên máy chủ không nhận."""
    name = clean_device(helper) or "PC"
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _release_stale_pc(conn)
        for _ in range(20):
            row = conn.execute(
                """
                SELECT id, path, size_bytes, sha256 FROM videos
                WHERE status = 'queued' AND path != ''
                ORDER BY COALESCE(check_pass, 0), id
                LIMIT 1
                """
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
                    helper = ?, lease = ?, lease_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, name, lease, now, row["id"]),
            )
            if cur.rowcount != 1:
                continue
            conn.execute("COMMIT")
            return {
                "id": int(row["id"]),
                "bytes": int(row["size_bytes"]),
                "sha256": str(row["sha256"]),
                "lease": lease,
            }
        conn.execute("COMMIT")
        return None


def touch_pc(db_path: Path, video_id: int, lease: str) -> bool:
    if not lease:
        return False
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE videos SET lease_at = ?
            WHERE id = ? AND status = 'pc' AND lease = ?
            """,
            (utcnow(), video_id, lease),
        )
        return cur.rowcount == 1


def release_pc(db_path: Path, video_id: int, lease: str) -> bool:
    """PC đọc hỏng thì trả video về hàng chờ. Phiếu không khớp thì không đụng video của máy khác."""
    if not lease:
        return False
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE videos
            SET status = 'queued', pid = NULL, started_at = NULL, error = '',
                lease = '', helper = '', lease_at = NULL
            WHERE id = ? AND status = 'pc' AND lease = ?
            """,
            (video_id, lease),
        )
        return cur.rowcount == 1


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


def requeue(db_path: Path, video_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET status = 'queued', pid = NULL, started_at = NULL WHERE id = ? AND status = 'running'",
            (video_id,),
        )


def requeue_running(db_path: Path, keep_pid: int) -> int:
    """Lúc bộ đọc khởi động, mọi video đang đọc dở thuộc tiến trình cũ đã chết. Không dựa vào pid còn sống,
    vì sau khi khởi động lại máy, pid cũ có thể đã thuộc về chương trình khác."""
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE videos SET status = 'queued', pid = NULL, started_at = NULL
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
                "UPDATE videos SET status = 'queued', pid = NULL, started_at = NULL WHERE id = ? AND status = 'running'",
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
        where += " AND status = 'pc' AND lease = ?"
        params.append(lease)
    cur = conn.execute(
        f"""
        UPDATE videos
        SET status = 'done', pid = NULL, finished_at = ?, error = '',
            {count_sql}
            lease = '', {helper_sql}, lease_at = NULL, check_pass = 0
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

    Có phiếu thì chỉ ghi khi video vẫn đang do đúng PC đó giữ. Phiếu sai thì
    không đổi dòng nào.
    """
    phones, full = video_counts(table)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if lease is not None:
            owned = conn.execute(
                "SELECT id FROM videos WHERE id = ? AND status = 'pc' AND lease = ? AND lease != ''",
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


def fail(db_path: Path, video_id: int, message: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE videos
            SET status = 'error', pid = NULL, finished_at = ?, error = ?,
                helper = CASE WHEN helper IS NULL OR helper = '' THEN 'VPS' ELSE helper END,
                check_pass = 0
            WHERE id = ?
            """,
            (utcnow(), message[:500], video_id),
        )


def retry(db_path: Path, video_id: int) -> bool:
    """Đưa video lỗi hoặc đã đọc xong về hàng chờ, nếu file còn trên máy chủ.

    Đọc lại dùng bộ đọc hiện tại. Kết quả mới chỉ nâng dòng cần xem hoặc chưa
    mở hồ sơ lên đã lưu; dòng đã lưu đủ ba ô không bị ghi đè.
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


def _result_filter(query: str, alias: str = "") -> tuple[str, list[object]]:
    """Lọc theo đầu số hoặc đầu username. Chuỗi rỗng là mọi dòng."""
    prefix = f"{alias}." if alias else ""
    text = "".join(query.split())
    if text.startswith("+84"):
        text = "0" + text[3:]
    if text.isdigit():
        return (
            f"WHERE {prefix}phone >= ? AND {prefix}phone < ? AND {prefix}phone != ''",
            [text, text + ":"],
        )
    if text:
        handle = text if text.startswith("@") else "@" + text
        return (
            f"WHERE {prefix}username >= ? AND {prefix}username < ? AND {prefix}username != ''",
            [handle, handle + "\U0010ffff"],
        )
    return "", []


def result_page(
    db_path: Path,
    query: str = "",
    limit: int = 50,
    before: int = 0,
    after: int = 0,
) -> dict[str, object]:
    """Một trang kết quả, dòng mới nhất (id lớn hơn) ở trên.

    before lấy các dòng cũ hơn id đó. after lấy các dòng mới hơn id đó.
    Nếu cả hai cùng có thì after thắng. before trong kết quả là id dòng cuối
    khi trang đầy và đang đi xuống, để lấy trang kế. 0 nghĩa là hết trang.
    """
    where, params = _result_filter(query, "r")
    count_where, count_params = _result_filter(query)
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
        if count_where:
            total = int(conn.execute(f"SELECT COUNT(*) AS n FROM results {count_where}", count_params).fetchone()["n"])
        else:
            total = int(conn.execute("SELECT COALESCE(SUM(n), 0) AS n FROM result_counts").fetchone()["n"])
        rows = conn.execute(
            f"""
            SELECT r.id, r.phone, r.name, r.username, r.bucket, r.reason, r.video_id,
                   COALESCE(v.helper, '') AS helper, COALESCE(r.checked, '') AS checked
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


def search_results(db_path: Path, query: str = "", limit: int = 50) -> list[dict[str, object]]:
    """Tìm theo đầu số hoặc đầu username. Dòng mới nhất ở trên."""
    page = result_page(db_path, query, limit)
    items = page["items"]
    return list(items) if isinstance(items, list) else []


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
