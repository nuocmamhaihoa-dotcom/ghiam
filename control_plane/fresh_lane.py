"""Bảng đọc riêng. Video mới và video đã có đều được đọc lại từ đầu.

Không ghi vào cơ sở kết quả đang chạy. Bộ đọc đang chạy không nhận các video này.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from control_plane.video_store import (
    BUCKET_LABEL,
    begin_upload,
    commit_upload,
    connect,
    init_db,
    search_results,
)


def fresh_db_path(data_dir: Path) -> Path:
    override = os.environ.get("CONTROL_VIDEO_FRESH_DB", "").strip()
    if override:
        return Path(override)
    return data_dir / "video-fresh.db"


def fresh_video_dir(data_dir: Path) -> Path:
    override = os.environ.get("CONTROL_VIDEO_FRESH_DIR", "").strip()
    if override:
        return Path(override)
    return data_dir / "videos-fresh"


def fresh_heartbeat(data_dir: Path) -> Path:
    return data_dir / "video-fresh.heartbeat"


def _same_name_size(db_path: Path) -> set[tuple[str, int]]:
    with connect(db_path) as conn:
        return {
            (str(row["name"]), int(row["size_bytes"] or 0))
            for row in conn.execute("SELECT name, size_bytes FROM videos")
        }


def import_kept_videos(live_db: Path, data_dir: Path) -> dict[str, int]:
    """Đưa mỗi video còn file sang bảng mới, như một lần tải từ đầu.

    File đã nằm trong bảng mới thì không nhân thêm bản. Không xóa file của bảng cũ.
    """
    fresh_db = fresh_db_path(data_dir)
    folder = fresh_video_dir(data_dir)
    init_db(live_db)
    init_db(fresh_db)
    folder.mkdir(parents=True, exist_ok=True)
    with connect(live_db) as conn:
        rows = conn.execute(
            """
            SELECT name, size_bytes, sha256, path, device
            FROM videos
            WHERE path != '' AND status IN ('done', 'error')
            ORDER BY id
            """
        ).fetchall()
    have = _same_name_size(fresh_db)
    imported = 0
    skipped = 0
    missing = 0
    for row in rows:
        src = Path(str(row["path"] or ""))
        name = str(row["name"] or src.name or "video.mp4")
        size = int(row["size_bytes"] or 0)
        try:
            stat = src.stat()
        except OSError:
            missing += 1
            continue
        if not src.is_file() or stat.st_size <= 0:
            missing += 1
            continue
        if (name, size or stat.st_size) in have or (name, stat.st_size) in have:
            skipped += 1
            continue
        created = begin_upload(
            fresh_db,
            name=name,
            size_bytes=stat.st_size,
            sha256=str(row["sha256"] or ""),
            device=str(row["device"] or ""),
            allow_repeat=True,
        )
        dest = folder / f"{int(created['id'])}{src.suffix.lower()}"
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
        commit_upload(fresh_db, int(created["id"]), str(dest))
        have.add((name, stat.st_size))
        imported += 1
    return {"imported": imported, "skipped": skipped, "missing": missing}


def fresh_results(db_path: Path, query: str = "", limit: int = 80, offset: int = 0, view: str = "all") -> list[dict[str, object]]:
    items = search_results(db_path, query, limit, offset, view)
    for item in items:
        shots = item.get("shots")
        if not isinstance(shots, dict):
            continue
        item["shots"] = {
            field: str(url).replace("/v1/results/", "/v1/fresh/results/", 1)
            for field, url in shots.items()
        }
    return items


def fresh_report(db_path: Path) -> dict[str, object]:
    """Đếm từng lý do lỗi và nói rõ nhóm nào còn thiếu gì."""
    init_db(db_path)
    with connect(db_path) as conn:
        reason_rows = conn.execute(
            """
            SELECT bucket, reason, COUNT(*) AS n
            FROM results
            GROUP BY bucket, reason
            ORDER BY n DESC, bucket
            """
        ).fetchall()
        video_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM videos GROUP BY status"
        ).fetchall()
    reasons = [
        {
            "bucket": BUCKET_LABEL.get(int(row["bucket"]), ""),
            "reason": str(row["reason"] or ""),
            "n": int(row["n"]),
        }
        for row in reason_rows
    ]
    by_status = {str(row["status"]): int(row["n"]) for row in video_rows}
    saved = sum(item["n"] for item in reasons if item["bucket"] == "Đủ")
    review = sum(item["n"] for item in reasons if item["bucket"] == "Cần xem")
    scan = sum(item["n"] for item in reasons if item["bucket"] == "Lỗi quét")
    notes: list[str] = []
    if not reasons and not by_status:
        notes.append("Bảng mới chưa có video. Tải file lên, hoặc đưa video còn trên đĩa vào bảng này.")
    elif by_status.get("queued") or by_status.get("running") or by_status.get("uploading"):
        notes.append("Đang đọc từ đầu bằng cách làm mới nhất. Kết quả hiện khi từng video xong.")
    if saved or review or scan:
        total = saved + review
        pct = round(100 * saved / total) if total else 0
        notes.append(
            f"{saved} đủ, {review} cần xem, {scan} lỗi quét. "
            f"Trong số có trong danh bạ, {pct}% đã đủ 3 cột."
        )
    for item in reasons:
        if item["bucket"] == "Đủ" or not item["n"]:
            continue
        label = item["reason"] or "không ghi lý do"
        notes.append(f"{item['n']} dòng «{item['bucket']}»: {label}.")
    if any(item["reason"] == "chấm che, chưa đọc đủ số" for item in reasons):
        notes.append("Ô số của dòng chấm che hiện phần nhìn thấy và ảnh dải chữ. Không đoán ký tự bị che.")
    if any("chưa đọc được @" in item["reason"] for item in reasons):
        notes.append("Có số mà chưa có @ thì mở lại đúng người đó, nhìn rõ dòng @, rồi tải video vào bảng này.")
    return {
        "saved": saved,
        "review": review,
        "scan": scan,
        "queued": by_status.get("queued", 0),
        "running": by_status.get("running", 0),
        "done": by_status.get("done", 0),
        "error": by_status.get("error", 0),
        "reasons": reasons,
        "notes": notes,
    }
