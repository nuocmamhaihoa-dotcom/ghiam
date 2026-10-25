"""Việc nạp / tra cứu chạy nền: trả mã việc ngay, trang hỏi tiến độ. Gửi lại cùng mã yêu cầu thì không chạy lại."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from control_plane.settings import settings

JOB_TTL_SEC = 3 * 24 * 3600
SAVE_EVERY_SEC = 0.7
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE = ("queued", "waiting", "running")
_lock = threading.Lock()
_live: dict[str, dict[str, Any]] = {}
_events: dict[str, threading.Event] = {}


class JobError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def jobs_root() -> Path:
    root = settings.data_dir / "khach-jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_file(job_id: str, suffix: str = ".json") -> Path:
    if not _JOB_ID.fullmatch(job_id or ""):
        raise JobError("Mã việc không đúng.")
    return jobs_root() / f"{job_id}{suffix}"


def _save(job: dict[str, Any]) -> None:
    path = job_file(job["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(job, ensure_ascii=False))


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        live = _live.get(job_id)
        if live is not None:
            return _snapshot(live)
    try:
        job = json.loads(job_file(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, JobError):
        return None
    if job.get("status") in _ACTIVE:
        pid = int(job.get("pid") or 0)
        if pid == os.getpid() or not _pid_alive(pid):
            job["status"] = "interrupted"
            if job.get("kind") == "import":
                job["error"] = "Lần nạp dừng vì mất kết nối. Khách đã ghi vẫn còn. Bấm Tiếp tục để chạy phần còn lại."
            else:
                job["error"] = "Việc này dừng vì mất kết nối. Hãy chạy lại."
            job["updatedAt"] = time.time()
            try:
                _save(job)
            except (OSError, JobError):
                pass
    return job


def remember_resume(job_id: str, child_id: str) -> None:
    """Ghi việc nối tiếp để bấm Tiếp tục lần nữa không nạp trùng."""
    with _lock:
        live = _live.get(job_id)
        if live is not None:
            info = live.get("info")
            if not isinstance(info, dict):
                info = {}
                live["info"] = info
            info["resumedBy"] = child_id
            _save(live)
            return
    try:
        job = json.loads(job_file(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, JobError):
        return
    info = job.get("info")
    if not isinstance(info, dict):
        info = {}
        job["info"] = info
    info["resumedBy"] = child_id
    _save(job)


def request_job_id(request_id: str) -> str | None:
    """Việc đã gắn với mã yêu cầu, để bấm Tiếp tục lần nữa không tạo thêm lần nạp."""
    if not _REQUEST_ID.fullmatch(request_id or ""):
        return None
    try:
        known = json.loads(_request_file(request_id).read_text(encoding="utf-8")).get("job")
    except (OSError, ValueError, AttributeError):
        return None
    text = str(known or "")
    return text if _JOB_ID.fullmatch(text) else None


def _request_file(request_id: str) -> Path:
    return jobs_root() / f"req-{request_id}.json"


def start_job(
    kind: str,
    request_id: str,
    work: Callable[["JobContext"], dict[str, Any]],
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chạy `work` ở luồng nền. Cùng request_id (trang gửi lại vì rớt mạng) thì trả việc cũ."""
    request_id = request_id if _REQUEST_ID.fullmatch(request_id or "") else ""
    with _lock:
        if request_id:
            try:
                known = json.loads(_request_file(request_id).read_text(encoding="utf-8")).get("job")
            except (OSError, ValueError, AttributeError):
                known = None
            if known:
                live = _live.get(str(known))
                if live is not None:
                    return _snapshot(live)
                saved = None
                try:
                    saved = json.loads(job_file(str(known)).read_text(encoding="utf-8"))
                except (OSError, ValueError, JobError):
                    saved = None
                if saved and saved.get("status") == "done":
                    return saved
        now = time.time()
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "status": "queued",
            "progress": {},
            "info": info or {},
            "result": None,
            "error": None,
            "requestId": request_id,
            "pid": os.getpid(),
            "createdAt": now,
            "updatedAt": now,
        }
        _live[job["id"]] = job
        _events[job["id"]] = threading.Event()
        _save(job)
        if request_id:
            _request_file(request_id).write_text(json.dumps({"job": job["id"]}), encoding="utf-8")
        snapshot = _snapshot(job)
    threading.Thread(target=_run, args=(job, work), name=f"khach-{kind}", daemon=True).start()
    return snapshot


class JobContext:
    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        self.id = str(job["id"])
        self._saved = 0.0

    def report(self, progress: dict[str, Any] | None = None, status: str | None = None, force: bool = False) -> None:
        with _lock:
            if progress:
                self.job["progress"].update(progress)
            if status:
                self.job["status"] = status
            self.job["updatedAt"] = time.time()
            now = time.monotonic()
            if force or status or now - self._saved >= SAVE_EVERY_SEC:
                self._saved = now
                _save(self.job)


def _run(job: dict[str, Any], work: Callable[[JobContext], dict[str, Any]]) -> None:
    context = JobContext(job)
    context.report(status="running")
    try:
        result = work(context)
        with _lock:
            job["result"] = result
            job["status"] = "done"
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        with _lock:
            job["status"] = "error"
            if isinstance(detail, str) and detail:
                job["error"] = detail
            else:
                job["error"] = "Máy chủ gặp lỗi khi xử lý. Bấm lại để thử lần nữa."
        if not isinstance(detail, str):
            traceback.print_exc(file=sys.stderr)
    finally:
        with _lock:
            job["updatedAt"] = time.time()
            job["finishedAt"] = job["updatedAt"]
            _save(job)
            _live.pop(job["id"], None)
            event = _events.pop(job["id"], None)
        if event is not None:
            event.set()
        cleanup_expired()


def wait_job(job_id: str, timeout: float) -> dict[str, Any] | None:
    with _lock:
        event = _events.get(job_id)
    if event is not None:
        event.wait(timeout)
    return get_job(job_id)


@contextmanager
def import_lock(context: JobContext | None = None) -> Iterator[None]:
    """Chỉ một lần nạp ghi kho cùng lúc, kể cả khi máy chủ chạy nhiều tiến trình."""
    path = settings.data_dir / "khach-import.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if context is not None:
                    context.report({"phase": "waiting"}, status="waiting")
                time.sleep(0.5)
        if context is not None and context.job.get("status") == "waiting":
            context.report(status="running")
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_last_cleanup = 0.0


def cleanup_expired(force: bool = False) -> int:
    global _last_cleanup
    now = time.time()
    if not force and now - _last_cleanup < 3600:
        return 0
    _last_cleanup = now
    removed = 0
    for path in jobs_root().iterdir():
        try:
            if now - path.stat().st_mtime > JOB_TTL_SEC:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed
