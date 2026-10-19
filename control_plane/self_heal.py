"""Một lượt tự sửa, gộp các lần sửa từ trước, chạy cạnh tiến trình đang tải và đang đọc.

Không khởi động lại dịch vụ. Không xóa dòng kết quả. Không nhận video đang đọc.
Không xóa lần tải còn mới. Video được đọc lại xếp sau hàng chờ, không chen video đang đợi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from control_plane.pc_fleet import ONLINE_SECONDS
from control_plane.video_store import (
    connect,
    drop_orphan_small,
    drop_stale_uploads,
    recover_dead,
    release_stale_pc,
    utcnow,
)

# Lỗi của bộ đọc cũ: một khung JPEG hỏng làm rơi cả video. Bộ đọc hiện bỏ khung đó và đọc tiếp.
_BAD_FRAME = ("image file is truncated", "cannot identify image file")
# Đĩa đầy phải hoãn, không để video nằm ở trạng thái lỗi.
_DISK_FULL = ("no space left", "errno 28")


def _kind(error: str) -> str:
    lowered = error.lower()
    if any(piece in lowered for piece in _BAD_FRAME):
        return "frame"
    if any(piece in lowered for piece in _DISK_FULL):
        return "disk"
    return ""


def _requeue_behind(db_path: Path, video_dir: Path) -> list[str]:
    """Đưa video lỗi đã biết nguyên nhân về sau hàng chờ, nếu file còn và chưa có kết quả."""
    root = video_dir.resolve()
    notes: list[str] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, path, error,
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
            try:
                inside = path.resolve().is_relative_to(root)
            except (ValueError, OSError):
                inside = False
            if not (inside and path.is_file()):
                if path.is_file():
                    # File nằm ngoài thư mục video. Không đọc lại và không nói là file đã mất.
                    continue
                cur = conn.execute(
                    """
                    UPDATE videos
                    SET error = ?, finished_at = COALESCE(finished_at, ?)
                    WHERE id = ? AND status = 'error'
                    """,
                    ("File video không còn trên máy chủ.", utcnow(), video_id),
                )
                if cur.rowcount == 1:
                    notes.append(f"Video {video_id} lỗi đã biết nhưng file không còn. Giữ là lỗi.")
                continue
            cur = conn.execute(
                """
                UPDATE videos
                SET status = 'queued', error = '', finished_at = NULL, pid = NULL,
                    started_at = NULL, phone_count = NULL, full_count = NULL,
                    helper = '', lease = '', lease_at = NULL, check_pass = 0,
                    check_from = '', check_error = '',
                    progress_stage = '', progress_done = 0, progress_total = 0,
                    progress_at = NULL, progress_same_at = NULL, priority = -1
                WHERE id = ? AND status = 'error'
                """,
                (video_id,),
            )
            if cur.rowcount != 1:
                continue
            if kind == "frame":
                notes.append(f"Video {video_id} lỗi vì một khung ảnh hỏng. Xếp sau hàng chờ để đọc lại.")
            else:
                notes.append(f"Video {video_id} lỗi vì đĩa đầy. Xếp sau hàng chờ để đọc lại.")
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
                ("File video không còn trên máy chủ.", utcnow(), video_id),
            )
            if cur.rowcount == 1:
                notes.append(f"Video {video_id} đang chờ nhưng file không còn. Đánh là lỗi.")
    return notes


def _rest_abandoned_pcs(db_path: Path, now: datetime, offline_seconds: float) -> list[str]:
    """PC ghi đang đọc, không giữ video, và đã mất kết nối thì ghi đang nghỉ.

    Không đổi giờ nhìn thấy lần cuối, để trang không tưởng PC vừa nối lại.
    PC đang nối, hoặc đang giữ video, không bị đụng.
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


def heal_once(
    db_path: Path,
    video_dir: Path,
    heartbeat: Path | None = None,
    *,
    now: datetime | None = None,
    alive=None,
    upload_stale_seconds: float = 1800,
    pc_offline_seconds: float = ONLINE_SECONDS,
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
    dropped = drop_stale_uploads(db_path, upload_stale_seconds)
    if dropped:
        notes.append(f"Bỏ {dropped} lần tải dở quá hạn. Lần tải đang chạy không bị đụng.")
    released = release_stale_pc(db_path)
    if released:
        notes.append(f"Trả {released} video PC mất nhịp hoặc đọc xong không gửi về hàng chờ.")
    notes.extend(_requeue_behind(db_path, video_dir))
    notes.extend(_mark_missing_queued(db_path, video_dir))
    notes.extend(_rest_abandoned_pcs(db_path, moment, pc_offline_seconds))
    orphans = drop_orphan_small(db_path, video_dir)
    if orphans:
        notes.append(f"Xóa {orphans} file thu nhỏ không video nào dùng.")
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
