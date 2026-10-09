"""Hàng đợi video và kết quả đã đọc.

Ghi bằng SQLite WAL, synchronous=FULL: mất điện vẫn giữ các dòng đã commit.
Mỗi video chỉ vài chục dòng, nên 15 tiến trình đọc không nghẽn ở chỗ ghi.
Một dòng khoảng vài trăm byte. Một trăm triệu dòng nằm trong vài chục GB.
"""

from __future__ import annotations

import os
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
    raw = os.environ.get("CONTROL_VIDEO_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    cpus = os.cpu_count() or 2
    return max(1, cpus - 1)


def feeder_count() -> int:
    """Số video đọc cùng lúc. Mặc định 1: nhiều máy vẫn đẩy lên song song, xếp hàng đọc lần lượt.

    Mỗi video dùng riêng một nhóm tiến trình. Tăng CONTROL_VIDEO_FEEDERS chỉ khi chắc
    máy chủ đủ RAM; chia sẻ chung một nhóm tiến trình đã từng làm nghẽn cả hàng đợi.
    """
    raw = os.environ.get("CONTROL_VIDEO_FEEDERS", "").strip()
    if raw:
        return max(1, int(raw))
    return 1


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
              progress_at TEXT
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


def queued_bytes(db_path: Path) -> int:
    """Dung lượng video còn nằm trên đĩa, kể cả video lỗi được giữ lại để bấm Đọc lại."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0) AS n FROM videos
            WHERE status IN ('uploading', 'queued', 'running') OR (status = 'error' AND path != '')
            """
        ).fetchone()
    return int(row["n"])


def can_accept(used_bytes: int, new_bytes: int, limit_bytes: int, free_bytes: int) -> bool:
    if new_bytes > limit_bytes or used_bytes + new_bytes > limit_bytes:
        return False
    return free_bytes > new_bytes + (2 * 1024 * 1024 * 1024)


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
                    progress = '', progress_at = NULL
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


def commit_upload(db_path: Path, video_id: int, path: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE videos SET path = ?, status = 'queued' WHERE id = ? AND status = 'uploading'",
            (path, video_id),
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
              v.device, v.progress, v.progress_at,
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
    return [dict(row) for row in rows]


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
            "SELECT id, name, path FROM videos WHERE status = 'queued' AND path != '' ORDER BY id LIMIT 1"
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
        return {"id": int(row["id"]), "name": row["name"], "path": row["path"]}


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
    with connect(db_path) as conn:
        row = conn.execute("SELECT path, status FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None or row["status"] != "error" or not row["path"] or not Path(row["path"]).exists():
            return False
        cur = conn.execute(
            "UPDATE videos SET status = 'queued', error = '', finished_at = NULL, pid = NULL WHERE id = ? AND status = 'error'",
            (video_id,),
        )
        return cur.rowcount == 1


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
                    ORDER BY r.id ASC
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
                ORDER BY r.id ASC
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
        yield "Số điện thoại,Tên,Username,Time quét,Tên máy,Video\n"
        cursor = conn.execute(
            """
            SELECT
              r.phone, r.name, r.username, r.created_at AS scanned_at,
              COALESCE(v.device, '') AS device,
              COALESCE(v.name, '') AS video
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
