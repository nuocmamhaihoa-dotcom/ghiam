"""Sổ video trên SQLite. Bộ nhớ vẫn là việc đang chạy; sổ này còn sau khi hub khởi động lại."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from control_plane import db
from control_plane.settings import settings

_LOCK = threading.Lock()
_SEEN: dict[str, float] = {}
_KEEP_SECONDS = 7 * 24 * 3600
_KEEP_FINISHED = 400
_HISTORY = 80


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _load_list(raw: object) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        loaded = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _problems(raw: object) -> list[str]:
    found: list[str] = []
    for item in _load_list(raw):
        text = _text(item, 180)
        if text and text not in found and len(found) < 20:
            found.append(text)
    return found


def _frames(raw: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in _load_list(raw):
        if not isinstance(item, dict):
            continue
        try:
            stamp = float(item.get("t"))
        except (TypeError, ValueError):
            continue
        captions = [_text(line, 180) for line in _load_list(item.get("captions")) if _text(line, 180)]
        sightings: list[dict[str, str]] = []
        for sighting in _load_list(item.get("sightings")):
            if not isinstance(sighting, dict):
                continue
            sightings.append(
                {
                    "kind": _text(sighting.get("kind"), 40),
                    "name": _text(sighting.get("name"), 80),
                    "contactName": _text(sighting.get("contactName"), 80),
                    "username": _text(sighting.get("username"), 40),
                }
            )
        found.append({"t": stamp, "captions": captions[:20], "sightings": sightings[:30]})
    return found


def _ids(raw: object) -> list[str]:
    return [_text(item, 64) for item in _load_list(raw) if _text(item, 64)]


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _public_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "jobId": str(row["id"]),
        "name": str(row["name"] or ""),
        "source": str(row["source"] or ""),
        "size": int(row["size"] or 0),
        "state": str(row["state"] or ""),
        "percent": int(row["percent"] or 0),
        "task": str(row["task"] or ""),
        "problems": _problems(row["problems_json"]),
        "error": str(row["error"] or ""),
        "worker": str(row["worker"] or ""),
        "workerName": "",
        "savedPeople": int(row["saved_people"] or 0),
        "createdAt": float(row["created_at"] or 0),
        "startedAt": _number(row["started_at"]),
        "finishedAt": _number(row["finished_at"]),
        "partLabel": str(row["part_label"] or ""),
    }


def _open_item(row: sqlite3.Row) -> dict[str, Any]:
    body = _public_item(row)
    body["path"] = str(row["path"] or "")
    body["parentId"] = str(row["parent_id"] or "")
    body["partIds"] = _ids(row["part_ids_json"])
    body["frames"] = _frames(row["frames_json"])
    body["rev"] = int(row["rev"] or 0)
    body["id"] = body["jobId"]
    return body


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _upsert(row: dict[str, Any]) -> None:
    job_id = _text(row.get("id"), 64)
    if not job_id:
        return
    created = _number(row.get("createdAt")) or time.time()
    with db.connect(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO video_jobs (
              id, name, source, size, state, percent, task, problems_json, error, worker, path,
              created_at, started_at, finished_at, saved_people, parent_id, part_label,
              part_ids_json, frames_json, upload_id, rev
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              source=excluded.source,
              size=excluded.size,
              state=excluded.state,
              percent=excluded.percent,
              task=excluded.task,
              problems_json=excluded.problems_json,
              error=excluded.error,
              worker=excluded.worker,
              path=excluded.path,
              created_at=video_jobs.created_at,
              started_at=COALESCE(video_jobs.started_at, excluded.started_at),
              finished_at=excluded.finished_at,
              saved_people=excluded.saved_people,
              parent_id=excluded.parent_id,
              part_label=excluded.part_label,
              part_ids_json=excluded.part_ids_json,
              frames_json=excluded.frames_json,
              upload_id=CASE
                WHEN excluded.upload_id != '' THEN excluded.upload_id
                ELSE video_jobs.upload_id
              END,
              rev=excluded.rev
            WHERE excluded.rev >= video_jobs.rev
            """,
            (
                job_id,
                _text(row.get("name"), 120),
                _text(row.get("source"), 80),
                max(0, int(row.get("size") or 0)),
                _text(row.get("state"), 20) or "queued",
                max(0, min(100, int(row.get("percent") or 0))),
                _text(row.get("task"), 180),
                _dump(_problems(row.get("problems"))),
                _text(row.get("error"), 180),
                _text(row.get("worker"), 64),
                str(row.get("path") or ""),
                created,
                _number(row.get("startedAt")),
                _number(row.get("finishedAt")),
                max(0, int(row.get("savedPeople") or 0)),
                _text(row.get("parentId"), 64),
                _text(row.get("partLabel"), 40),
                _dump(_ids(row.get("partIds"))),
                _dump(_frames(row.get("frames"))),
                _text(row.get("uploadId"), 64),
                max(0, int(row.get("rev") or 0)),
            ),
        )


def _prune(conn: sqlite3.Connection) -> None:
    cutoff = time.time() - _KEEP_SECONDS
    conn.execute(
        """
        DELETE FROM video_jobs
        WHERE state IN ('done', 'failed')
          AND finished_at IS NOT NULL
          AND finished_at < ?
        """,
        (cutoff,),
    )
    rows = conn.execute(
        """
        SELECT id FROM video_jobs
        WHERE state IN ('done', 'failed')
        ORDER BY COALESCE(finished_at, created_at) DESC
        """
    ).fetchall()
    stale = [str(row["id"]) for row in rows[_KEEP_FINISHED:]]
    if stale:
        conn.executemany("DELETE FROM video_jobs WHERE id=?", [(item,) for item in stale])


def save_job(row: dict[str, Any], force: bool = False) -> None:
    """Ghi một việc. Tiến trình khung thì cách khoảng 2 giây; đổi trạng thái thì ghi ngay."""
    job_id = _text(row.get("id"), 64)
    if not job_id:
        return
    now = time.monotonic()
    with _LOCK:
        if not force and now - _SEEN.get(job_id, 0.0) < 2.0:
            return
        try:
            _upsert(row)
        except Exception:
            return
        _SEEN[job_id] = now
        if str(row.get("state") or "") not in {"done", "failed"}:
            return
        try:
            with db.connect(settings.db_path) as conn:
                _prune(conn)
        except Exception:
            return


def current_rev(job_id: str) -> int:
    key = _text(job_id, 64)
    if not key:
        return 0
    try:
        with db.connect(settings.db_path) as conn:
            row = conn.execute("SELECT rev FROM video_jobs WHERE id=?", (key,)).fetchone()
    except Exception:
        return 0
    if row is None:
        return 0
    try:
        return max(0, int(row["rev"] or 0))
    except (TypeError, ValueError):
        return 0


def open_upload(job_id: str, name: str, source: str, size: int) -> None:
    """Một dòng từ lúc bắt đầu gửi. Gửi xong thì cùng mã này thành việc đọc."""
    key = _text(job_id, 64)
    if not key:
        return
    with _LOCK:
        try:
            with db.connect(settings.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO video_jobs (
                      id, name, source, size, state, percent, task, problems_json, error, worker, path,
                      created_at, saved_people, parent_id, part_label, part_ids_json, frames_json, upload_id
                    ) VALUES (?, ?, ?, ?, 'uploading', 0, 'Đang gửi', '[]', '', '', '', ?, 0, '', '', '[]', '[]', '')
                    ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name,
                      source=excluded.source,
                      size=excluded.size
                    WHERE video_jobs.state='uploading'
                    """,
                    (
                        key,
                        _text(name, 120),
                        _text(source, 80),
                        max(0, int(size)),
                        time.time(),
                    ),
                )
        except Exception:
            return
        _SEEN[key] = time.monotonic()


def touch_upload(job_id: str, percent: int, task: str) -> None:
    """Chỉ cập nhật lần đang gửi. Việc đã vào hàng chờ không bị kéo về đang gửi."""
    key = _text(job_id, 64)
    if not key:
        return
    now = time.monotonic()
    with _LOCK:
        if now - _SEEN.get(key, 0.0) < 2.0:
            return
        try:
            with db.connect(settings.db_path) as conn:
                conn.execute(
                    """
                    UPDATE video_jobs
                    SET percent=?, task=?
                    WHERE id=? AND state='uploading'
                    """,
                    (max(0, min(99, int(percent))), _text(task, 180) or "Đang gửi", key),
                )
        except Exception:
            return
        _SEEN[key] = now


def mark_missing(job_id: str) -> None:
    key = _text(job_id, 64)
    if not key:
        return
    message = "Mất file video."
    now = time.time()
    with _LOCK:
        try:
            with db.connect(settings.db_path) as conn:
                row = conn.execute("SELECT problems_json FROM video_jobs WHERE id=?", (key,)).fetchone()
                if row is None:
                    return
                problems = _problems(row["problems_json"])
                if message not in problems and len(problems) < 20:
                    problems.append(message)
                conn.execute(
                    """
                    UPDATE video_jobs
                    SET state='failed', error=?, task='Gặp vấn đề',
                        finished_at=COALESCE(finished_at, ?), problems_json=?
                    WHERE id=? AND state IN ('queued', 'reading')
                    """,
                    (message, now, _dump(problems), key),
                )
        except Exception:
            return


def open_rows() -> list[dict[str, Any]]:
    """Việc chưa xong, việc gửi trước đứng trước."""
    try:
        with db.connect(settings.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM video_jobs
                WHERE state IN ('queued', 'reading')
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
    except Exception:
        return []
    return [_open_item(row) for row in rows]


def board() -> dict[str, Any]:
    try:
        with db.connect(settings.db_path) as conn:
            active = conn.execute(
                """
                SELECT * FROM video_jobs
                WHERE state IN ('uploading', 'reading')
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
            queued = conn.execute(
                """
                SELECT * FROM video_jobs
                WHERE state='queued'
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
            history = conn.execute(
                """
                SELECT * FROM video_jobs
                WHERE state IN ('done', 'failed')
                ORDER BY COALESCE(finished_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (_HISTORY,),
            ).fetchall()
    except Exception:
        return {"ok": True, "active": [], "queued": [], "history": []}
    return {
        "ok": True,
        "active": [_public_item(row) for row in active],
        "queued": [_public_item(row) for row in queued],
        "history": [_public_item(row) for row in history],
    }
