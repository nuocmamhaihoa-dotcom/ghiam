"""Một lượt tự sửa, gộp các lần sửa từ trước, chạy cạnh tiến trình đang tải và đang đọc.

Không khởi động lại dịch vụ. Không xóa dòng kết quả. Không nhận video đang đọc.
Không xóa lần tải còn mới. Video được đọc lại xếp sau hàng chờ, không chen video đang đợi.

Mỗi lỗi đã gặp có một cách sửa, và lần sau gặp lại thì làm ngay trong lượt này:

- Tiến trình đọc đã tắt: trả video về hàng chờ. Pid còn sống không bị cắt.
- Tải dở quá hạn mà file đã đủ byte: đưa vào hàng chờ. Chưa đủ thì bỏ dòng tải.
- PC mất nhịp hoặc đọc xong không gửi: trả video về hàng chờ.
- PC ghi đang đọc, không giữ video, đã mất kết nối: ghi đang nghỉ. Không đổi giờ nhìn thấy.
- Khung JPEG hỏng, đĩa đầy, khóa cơ sở dữ liệu, đĩa đọc lỗi, hết bộ nhớ, nhóm tiến trình gãy:
  file còn và chưa có kết quả thì xếp sau hàng chờ. Lỗi này không phải của file, đọc lại được.
- Không mở được video: đọc lại đúng một lần. Lần hỏng thứ hai giữ là lỗi, không lặp mãi.
- Thiếu ffmpeg hoặc tesseract: giữ là lỗi. Đọc lại không cài được công cụ.
- File rỗng hoặc file không còn trong thư mục video: giữ là lỗi, lời nhắn rõ.
- File đã bị đánh mất nhưng có lại: đọc một lần, xếp sau hàng chờ.
- File nằm ngoài thư mục video: không đọc lại và không nói là file đã mất.
- Đã có dòng kết quả: không đọc lại.
- Mảnh tải quá hạn, file tạm up- hết hạn, bản thu nhỏ không ai dùng: xóa. Khóa và file đang ghi giữ.
- Thư mục khung của lần đọc đã chết: xóa khi cũ hơn lần đọc đang chạy, hoặc khi không có ai đọc và đã quá hạn một khung. Thư mục đang ghi giữ.
- Sổ phụ SQLite quá lớn: chốt kiểu không chặn người đang ghi.
- Video đã xong mà còn trống số điện thoại đã đọc: điền lại từ dòng kết quả.
- @id cũ chưa có khóa chữ thường: gắn từng cụm, không đổi ngày sửa của dòng.

PC bị loại sau nhiều lần hỏng không được tự mở lại. Người dùng bấm cho đọc lại trên trang.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from control_plane.pc_fleet import ONLINE_SECONDS
from control_plane.upload_parts import sweep_parts
from control_plane.video_shrink import drop_stale_temps
from control_plane.video_store import (
    connect,
    backfill_video_counts,
    drop_orphan_small,
    drop_stale_uploads,
    fill_username_keys,
    recover_dead,
    release_stale_pc,
    utcnow,
)

# Một khung JPEG hỏng. Bộ đọc hiện bỏ khung đó và đọc tiếp.
_BAD_FRAME = ("image file is truncated", "cannot identify image file")
# Đĩa đầy phải hoãn, không để video nằm ở trạng thái lỗi.
_DISK_FULL = ("no space left", "errno 28", "disk is full")
# Lỗi của lúc đó, không của file. Hết thì đọc lại được.
_BUSY = (
    "database is locked",
    "database is busy",
    "sqlite_busy",
    "disk i/o error",
    "readonly database",
    "locking protocol",
    "brokenexecutor",
    "process pool",
    "terminated abruptly",
    "một khung không đọc xong",
    "resource temporarily unavailable",
    "too many open files",
    "cannot allocate memory",
    "memoryerror",
    "input/output error",
    "out of memory",
    "timed out",
    "connection reset",
    "broken pipe",
    "deadlock",
    "database schema has changed",
    "interrupted system call",
    "device or resource busy",
    "stale file handle",
)
# File có thể chưa ghi xong lúc ffmpeg mở. Đọc lại một lần. Hỏng thật thì dừng.
_ONCE = (
    "không mở được video",
    "không đọc được video",
    "không đọc được",
    "ffmpeg không đọc được",
    "không thấy file",
    "permission denied",
)
# Cài thiếu. Xếp lại hàng chờ không làm cho công cụ xuất hiện.
_TOOL = (
    "cần cài ffmpeg",
    "chưa cài ffmpeg",
    "chưa cài tesseract",
    "thiếu gói chữ",
)
_MISSING = "File video không còn trên máy chủ."
_EMPTY = "File video rỗng."
# Máy chủ đã đánh mất file. Nếu file có lại thì đọc một lần, không lặp khi đường dẫn nhấp nháy.
_RETURNED = (_MISSING, "Không còn file video.", _EMPTY)
# Thư mục khung ffmpeg để lại khi tiến trình đọc bị tắt giữa chừng.
_FRAME_PREFIXES = ("docvideo-frames-", "danhba-frames-", "docvideo-", "ocr-")
# Hạn một khung là 15 phút. Thư mục không có lần đọc nào đang chạy phải cũ hơn hạn đó mới xóa.
_FRAME_KEEP_SECONDS = 900 + 300
# Sổ phụ lớn hơn mức này thì chốt, để đĩa không đầy vì WAL. Không chốt sổ nhỏ mỗi phút.
_WAL_BYTES = 64 * 1024 * 1024
# Đã chốt. Không xếp lại.
_TERMINAL = (
    "file video không còn trên máy chủ.",
    "không còn file video.",
    "file video rỗng.",
    "tải lên bị gián đoạn",
)


def _kind(error: str) -> str:
    lowered = error.lower()
    if any(piece in lowered for piece in _TOOL) or any(piece in lowered for piece in _TERMINAL):
        return ""
    if any(piece in lowered for piece in _BAD_FRAME):
        return "frame"
    if any(piece in lowered for piece in _DISK_FULL):
        return "disk"
    if any(piece in lowered for piece in _BUSY):
        return "busy"
    if any(piece in lowered for piece in _ONCE):
        return "once"
    return ""


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except (ValueError, OSError):
        return False


def _requeue_behind(db_path: Path, video_dir: Path) -> list[str]:
    """Đưa video lỗi đã biết nguyên nhân về sau hàng chờ, nếu file còn và chưa có kết quả."""
    root = video_dir.resolve()
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, path, error, COALESCE(heal_tries, 0) AS heal_tries,
                   (SELECT COUNT(*) FROM results WHERE video_id = videos.id) AS saved
            FROM videos
            WHERE status = 'error'
            """
        ).fetchall()
        for row in rows:
            kind = _kind(str(row["error"] or ""))
            if not kind:
                continue
            video_id = int(row["id"])
            if int(row["saved"] or 0) > 0:
                # Đã có kết quả thì không đọc lại và không nhắc lại mỗi phút.
                continue
            path = Path(str(row["path"] or ""))
            inside = _inside(path, root)
            if not (inside and path.is_file()):
                if path.is_file():
                    # File nằm ngoài thư mục video. Không đọc lại và không nói là file đã mất.
                    continue
                if str(row["error"] or "") == _MISSING:
                    continue
                cur = conn.execute(
                    """
                    UPDATE videos
                    SET error = ?, finished_at = COALESCE(finished_at, ?)
                    WHERE id = ? AND status = 'error' AND error != ?
                    """,
                    (_MISSING, utcnow(), video_id, _MISSING),
                )
                if cur.rowcount == 1:
                    notes.append(f"Video {video_id} lỗi đã biết nhưng file không còn. Giữ là lỗi.")
                continue
            if path.stat().st_size <= 0:
                cur = conn.execute(
                    """
                    UPDATE videos
                    SET error = ?, finished_at = COALESCE(finished_at, ?), priority = 0
                    WHERE id = ? AND status = 'error' AND error != ?
                    """,
                    (_EMPTY, utcnow(), video_id, _EMPTY),
                )
                if cur.rowcount == 1:
                    notes.append(f"Video {video_id} là file rỗng. Giữ là lỗi, không đọc lại.")
                continue
            if kind == "once" and int(row["heal_tries"] or 0) >= 1:
                continue
            extra = ", heal_tries = COALESCE(heal_tries, 0) + 1" if kind == "once" else ""
            cur = conn.execute(
                f"""
                UPDATE videos
                SET status = 'queued', error = '', finished_at = NULL, pid = NULL,
                    started_at = NULL, phone_count = NULL, full_count = NULL,
                    helper = '', lease = '', lease_at = NULL, check_pass = 0,
                    check_from = '', check_error = '',
                    progress_stage = '', progress_done = 0, progress_total = 0,
                    progress_at = NULL, progress_same_at = NULL, priority = -1
                    {extra}
                WHERE id = ? AND status = 'error'
                """,
                (video_id,),
            )
            if cur.rowcount != 1:
                continue
            notes.append(_requeue_note(video_id, kind))
    return notes


def _requeue_note(video_id: int, kind: str) -> str:
    if kind == "frame":
        return f"Video {video_id} lỗi vì một khung ảnh hỏng. Xếp sau hàng chờ để đọc lại."
    if kind == "disk":
        return f"Video {video_id} lỗi vì đĩa đầy. Xếp sau hàng chờ để đọc lại."
    if kind == "once":
        return f"Video {video_id} không mở được lần trước. Xếp sau hàng chờ để đọc lại một lần."
    return f"Video {video_id} lỗi tạm thời. Xếp sau hàng chờ để đọc lại."


def _restore_returned_files(db_path: Path, video_dir: Path) -> list[str]:
    """File đã bị đánh mất nhưng đã nằm lại trong thư mục video thì đọc lại một lần."""
    root = video_dir.resolve()
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, path, error, COALESCE(heal_tries, 0) AS heal_tries,
                   (SELECT COUNT(*) FROM results WHERE video_id = videos.id) AS saved
            FROM videos
            WHERE status = 'error'
            """
        ).fetchall()
        for row in rows:
            if str(row["error"] or "") not in _RETURNED:
                continue
            if int(row["saved"] or 0) > 0 or int(row["heal_tries"] or 0) >= 1:
                continue
            path = Path(str(row["path"] or ""))
            try:
                ready = _inside(path, root) and path.is_file() and path.stat().st_size > 0
            except OSError:
                ready = False
            if not ready:
                continue
            video_id = int(row["id"])
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'queued', error = '', finished_at = NULL, pid = NULL,
                    started_at = NULL, phone_count = NULL, full_count = NULL,
                    helper = '', lease = '', lease_at = NULL, check_pass = 0,
                    check_from = '', check_error = '',
                    progress_stage = '', progress_done = 0, progress_total = 0,
                    progress_at = NULL, progress_same_at = NULL, priority = -1,
                    heal_tries = COALESCE(heal_tries, 0) + 1
                WHERE id = ? AND status = 'error'
                """,
                (video_id,),
            )
            if cur.rowcount == 1:
                if str(row["error"] or "") == _EMPTY:
                    notes.append(f"Video {video_id} hết rỗng. Xếp sau hàng chờ để đọc một lần.")
                else:
                    notes.append(f"Video {video_id} được đánh mất nhưng file đã có lại. Xếp sau hàng chờ để đọc một lần.")
    return notes


def _mark_missing_queued(db_path: Path, video_dir: Path) -> list[str]:
    """Video đang chờ mà file đã mất thì đánh lỗi, để bộ đọc không nhận một file không có."""
    del video_dir
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, path FROM videos WHERE status = 'queued'").fetchall()
        for row in rows:
            path = Path(str(row["path"] or ""))
            if path.is_file():
                continue
            video_id = int(row["id"])
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'error', error = ?, finished_at = ?, pid = NULL, priority = 0
                WHERE id = ? AND status = 'queued'
                """,
                (_MISSING, utcnow(), video_id),
            )
            if cur.rowcount == 1:
                notes.append(f"Video {video_id} đang chờ nhưng file không còn. Đánh là lỗi.")
    return notes


def _mark_empty_queued(db_path: Path) -> list[str]:
    """File chờ mà không có byte nào thì đánh lỗi. Đọc lại cũng không ra khung."""
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, path FROM videos WHERE status = 'queued'").fetchall()
        for row in rows:
            path = Path(str(row["path"] or ""))
            try:
                empty = path.is_file() and path.stat().st_size <= 0
            except OSError:
                empty = False
            if not empty:
                continue
            video_id = int(row["id"])
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'error', error = ?, finished_at = ?, pid = NULL, priority = 0
                WHERE id = ? AND status = 'queued'
                """,
                (_EMPTY, utcnow(), video_id),
            )
            if cur.rowcount == 1:
                notes.append(f"Video {video_id} đang chờ nhưng file rỗng. Đánh là lỗi.")
    return notes


def _promote_finished_uploads(db_path: Path, now: datetime, stale_seconds: float) -> list[str]:
    """Tải đã ghi đủ byte nhưng dòng vẫn ghi đang tải, và đã quá hạn, thì đưa vào hàng chờ.

    Lần tải còn mới không bị đụng: commit của trình duyệt đang chạy.
    File chưa đủ số byte đã khai thì để bước bỏ tải dở xử lý.
    """
    cutoff = now - timedelta(seconds=stale_seconds)
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, path, size_bytes, created_at FROM videos WHERE status = 'uploading'"
        ).fetchall()
        for row in rows:
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
            except ValueError:
                created = datetime.min.replace(tzinfo=timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created > cutoff:
                continue
            path = Path(str(row["path"] or ""))
            try:
                actual = path.stat().st_size if path.is_file() else 0
            except OSError:
                actual = 0
            expected = int(row["size_bytes"] or 0)
            if actual <= 0 or (expected > 0 and actual != expected):
                continue
            video_id = int(row["id"])
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'queued', error = '', priority = 0, finished_at = NULL, pid = NULL
                WHERE id = ? AND status = 'uploading'
                """,
                (video_id,),
            )
            if cur.rowcount == 1:
                notes.append(f"Video {video_id} tải đủ byte nhưng còn ghi đang tải. Đưa vào hàng chờ.")
    return notes


def _rest_abandoned_pcs(db_path: Path, now: datetime, offline_seconds: float) -> list[str]:
    """PC ghi đang đọc, không giữ video, và đã mất kết nối thì ghi đang nghỉ.

    Không đổi giờ nhìn thấy lần cuối, để trang không tưởng PC vừa nối lại.
    PC đang nối, đang giữ video, hoặc đang chờ đĩa, không bị đụng.
    PC bị loại không được tự mở lại.
    """
    cutoff = now - timedelta(seconds=offline_seconds)
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name, state, last_seen, video_id FROM pcs WHERE state = 'reading'"
        ).fetchall()
        for row in rows:
            name = str(row["name"])
            try:
                seen = datetime.fromisoformat(str(row["last_seen"]))
            except ValueError:
                seen = datetime.min.replace(tzinfo=timezone.utc)
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            if seen >= cutoff:
                continue
            live = conn.execute(
                "SELECT 1 FROM videos WHERE status = 'pc' AND helper = ?",
                (name,),
            ).fetchone()
            if live is not None:
                continue
            cur = conn.execute(
                """
                UPDATE pcs SET state = 'idle', video_id = NULL
                WHERE name = ? AND state = 'reading'
                """,
                (name,),
            )
            if cur.rowcount == 1:
                notes.append(f"PC {name} ghi đang đọc nhưng không giữ video và đã mất kết nối. Ghi đang nghỉ.")
    return notes


def _parse_time(raw: object) -> datetime | None:
    try:
        seen = datetime.fromisoformat(str(raw or ""))
    except ValueError:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen


def drop_abandoned_frames(frames_dir: Path | None, db_path: Path, now: datetime) -> int:
    """Xóa thư mục khung của lần đọc đã chết.

    Còn video đang đọc thì chỉ xóa thư mục cũ hơn giờ video đó bắt đầu.
    Không còn ai đọc thì chỉ xóa thư mục đã quá hạn một khung. Thư mục đang ghi giữ.
    """
    if frames_dir is None or not frames_dir.is_dir():
        return 0
    root = frames_dir.resolve()
    earliest: datetime | None = None
    with connect(db_path) as conn:
        rows = conn.execute("SELECT started_at FROM videos WHERE status = 'running'").fetchall()
    for row in rows:
        started = _parse_time(row["started_at"])
        if started is None:
            continue
        if earliest is None or started < earliest:
            earliest = started
    removed = 0
    try:
        children = list(frames_dir.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.name.startswith(_FRAME_PREFIXES) or not child.is_dir():
            continue
        try:
            if not child.resolve().is_relative_to(root):
                continue
            mtime = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if earliest is not None:
            if mtime >= earliest:
                continue
        elif (now - mtime).total_seconds() < _FRAME_KEEP_SECONDS:
            continue
        shutil.rmtree(child, ignore_errors=True)
        if not child.exists():
            removed += 1
    return removed


def _checkpoint_wal(db_path: Path, min_bytes: int = _WAL_BYTES) -> bool:
    """Chốt sổ phụ khi nó đã lớn. PASSIVE không chặn người đang ghi. Không ghi chú nếu không nhỏ lại."""
    wal = Path(str(db_path) + "-wal")
    try:
        before = wal.stat().st_size
    except OSError:
        return False
    if before < min_bytes:
        return False
    try:
        with connect(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        after = wal.stat().st_size if wal.is_file() else 0
    except (OSError, sqlite3.Error):
        return False
    return after < before


def heal_once(
    db_path: Path,
    video_dir: Path,
    heartbeat: Path | None = None,
    *,
    now: datetime | None = None,
    alive=None,
    upload_stale_seconds: float = 1800,
    pc_offline_seconds: float = ONLINE_SECONDS,
    frames_dir: Path | None = None,
    wal_bytes: int = _WAL_BYTES,
) -> list[str]:
    """Sửa những gì các lần trước đã học, rồi trả về câu đã làm. Lần sau không còn gì thì trả về rỗng.

    heartbeat giữ trong chữ ký để lần gọi từ bộ đọc truyền nhịp hiện tại. Video đang đọc
    chỉ được trả về hàng chờ khi pid của nó không còn sống, nên nhịp mới không bị cắt.
    """
    del heartbeat, alive
    moment = now or datetime.now(timezone.utc)
    notes: list[str] = []
    for video_id in _dead_ids(db_path):
        notes.append(f"Video {video_id} đang đọc nhưng tiến trình đã tắt. Đưa về hàng chờ.")
    notes.extend(_promote_finished_uploads(db_path, moment, upload_stale_seconds))
    dropped = drop_stale_uploads(db_path, upload_stale_seconds)
    if dropped:
        notes.append(f"Bỏ {dropped} lần tải dở quá hạn. Lần tải đang chạy không bị đụng.")
    released = release_stale_pc(db_path)
    if released:
        notes.append(f"Trả {released} video PC mất nhịp hoặc đọc xong không gửi về hàng chờ.")
    notes.extend(_requeue_behind(db_path, video_dir))
    notes.extend(_restore_returned_files(db_path, video_dir))
    notes.extend(_mark_missing_queued(db_path, video_dir))
    notes.extend(_mark_empty_queued(db_path))
    notes.extend(_rest_abandoned_pcs(db_path, moment, pc_offline_seconds))
    # Không gỡ khóa. Bản đang fsync trên hub cũ chưa làm mới mtime của khóa.
    expired = sweep_parts(video_dir, locks=False)
    if expired:
        notes.append(f"Xóa {expired} mảnh tải bỏ dở quá hạn. Lần tải đang ghi không bị đụng.")
    orphans = drop_orphan_small(db_path, video_dir)
    if orphans:
        notes.append(f"Xóa {orphans} file thu nhỏ không video nào dùng.")
    temps = drop_stale_temps(video_dir)
    if temps:
        notes.append(f"Xóa {temps} file tạm đã hết hạn. File đang tải và bản đang thu nhỏ không bị đụng.")
    frames = drop_abandoned_frames(frames_dir, db_path, moment)
    if frames:
        notes.append(f"Xóa {frames} thư mục khung của lần đọc đã chết. Lần đọc đang chạy không bị đụng.")
    if _checkpoint_wal(db_path, wal_bytes):
        notes.append("Chốt sổ phụ để trả chỗ trên đĩa.")
    filled = backfill_video_counts(db_path)
    if filled:
        notes.append(f"Điền số đã đọc cho {filled} video đã xong.")
    keyed = fill_username_keys(db_path)
    if keyed:
        notes.append(f"Gắn khóa @id cho {keyed} dòng cũ. @id khác hoa thường không hiện thêm.")
    return notes


def _dead_ids(db_path: Path) -> list[int]:
    """Gọi recover_dead và cho biết video nào vừa được trả về. Không đụng pid còn sống."""
    before = _running_ids(db_path)
    recover_dead(db_path)
    left = set(_running_ids(db_path))
    return [video_id for video_id in before if video_id not in left]


def _running_ids(db_path: Path) -> list[int]:
    with connect(db_path) as conn:
        return [int(row["id"]) for row in conn.execute("SELECT id FROM videos WHERE status = 'running' ORDER BY id")]
