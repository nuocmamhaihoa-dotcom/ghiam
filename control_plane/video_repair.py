"""Tự sửa các lỗi đã gặp khi hub và PC đọc video.

Vòng này chạy trên hub. Nó không đọc lại một video đã hỏng nội dung.
Nó gỡ các trạng thái kẹt mà các bản trước đã sửa trong mã, phòng khi
trạng thái đó xuất hiện lại lúc đang chạy: PC ngừng gửi tiến trình,
máy chủ nhận video trong khi PC đang rảnh, khe đọc của máy chủ bị kẹt,
video đủ byte mà chưa được đọc, phần video bị mất, và dấu thanh đặt sai chỗ.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from control_plane.syllables import restore_name
from control_plane.video_jobs import JobStore, VideoJob

RETRY_MARK = "Tự sửa lần"
RETRY_LIMIT = 2
_TRANSIENT = (
    "Mất kết nối",
    "Không xử lý được video",
    "PC gặp lỗi khi đọc",
)

_runtime_lock = threading.Lock()
_live: dict[str, int] = {}
_hub_threads: dict[int, int] = {}


def claim_start(job_id: str) -> bool:
    """Giữ một vé trước khi mở luồng, để hai vòng sửa không đọc cùng một video."""
    with _runtime_lock:
        if _live.get(job_id, 0) > 0:
            return False
        _live[job_id] = 1
        return True


def drop(job_id: str) -> None:
    with _runtime_lock:
        _live.pop(job_id, None)


def running(job_id: str) -> bool:
    with _runtime_lock:
        return _live.get(job_id, 0) > 0


def note_hub_enter() -> None:
    ident = threading.get_ident()
    with _runtime_lock:
        _hub_threads[ident] = _hub_threads.get(ident, 0) + 1


def note_hub_leave() -> None:
    ident = threading.get_ident()
    with _runtime_lock:
        left = _hub_threads.get(ident, 0) - 1
        if left <= 0:
            _hub_threads.pop(ident, None)
        else:
            _hub_threads[ident] = left


def live_hub_slots() -> int:
    """Bỏ khe của luồng đã chết. Số còn lại là số video máy chủ đang đọc thật."""
    alive = {thread.ident for thread in threading.enumerate()}
    with _runtime_lock:
        total = 0
        for ident, count in list(_hub_threads.items()):
            if ident not in alive:
                _hub_threads.pop(ident, None)
                continue
            total += count
        return total


def is_transient(message: str) -> bool:
    text = " ".join(str(message).split())
    return any(text.startswith(prefix) for prefix in _TRANSIENT)


def should_retry(job: VideoJob) -> bool:
    body = job.public()
    if not body.get("done") or not body.get("error"):
        return False
    if not is_transient(str(body.get("error") or "")):
        return False
    problems = body.get("problems") if isinstance(body.get("problems"), list) else []
    used = sum(1 for item in problems if str(item).startswith(RETRY_MARK))
    return used < RETRY_LIMIT


def retry_note(job: VideoJob) -> str:
    body = job.public()
    problems = body.get("problems") if isinstance(body.get("problems"), list) else []
    used = sum(1 for item in problems if str(item).startswith(RETRY_MARK))
    return f"{RETRY_MARK} {used + 1}: đọc lại sau lỗi tạm."


def heal_people(people: list[dict[str, str]]) -> list[dict[str, str]]:
    """Chuyển dấu thanh về đúng nguyên âm trước khi ghi. Không thêm dấu mới."""
    healed: list[dict[str, str]] = []
    for row in people:
        item = dict(row)
        for key in ("name", "contactName"):
            raw = item.get(key) or ""
            if not raw:
                continue
            try:
                fixed = restore_name(str(raw), allow_unique=False)
            except Exception:
                continue
            if fixed:
                item[key] = fixed
        healed.append(item)
    return healed


class RepairHooks:
    def __init__(
        self,
        jobs: JobStore,
        uploads: Callable[[], list[tuple[str, dict[str, Any]]]],
        complete_upload: Callable[[str], bool],
        spawn: Callable[[str, Path, str], bool],
        retry: Callable[[VideoJob], bool],
        has_idle: Callable[[], bool],
        heal_hub: Callable[[], bool],
        lease_seconds: float,
        frontier: Callable[[list[tuple[int, int]]], int],
    ) -> None:
        self.jobs = jobs
        self.uploads = uploads
        self.complete_upload = complete_upload
        self.spawn = spawn
        self.retry = retry
        self.has_idle = has_idle
        self.heal_hub = heal_hub
        self.lease_seconds = lease_seconds
        self.frontier = frontier


def sweep(hooks: RepairHooks) -> list[str]:
    """Sửa một lượt. Trả về tên các việc đã làm, để nhật ký và bài thử đối chiếu."""
    actions: list[str] = []
    try:
        if hooks.heal_hub():
            actions.append("hub_slot_leak")
    except Exception:
        pass
    for job in hooks.jobs.all():
        try:
            _heal_job(hooks, job, actions)
        except Exception:
            continue
    try:
        uploads = hooks.uploads()
    except Exception:
        uploads = []
    for upload_id, item in uploads:
        try:
            if item.get("finished"):
                continue
            size = int(item.get("size") or 0)
            ranges = item.get("ranges") if isinstance(item.get("ranges"), list) else []
            if size <= 0 or hooks.frontier(ranges) != size:
                continue
            if hooks.complete_upload(upload_id):
                actions.append("upload_bytes_complete")
        except Exception:
            continue
    return actions


def _heal_job(hooks: RepairHooks, job: VideoJob, actions: list[str]) -> None:
    if job.done:
        if hooks.retry(job):
            actions.append("transient_failure_retry")
        return
    if job.part_ids:
        _heal_parent(hooks, job, actions)
        return
    located, bound = job.located_file()
    if located is not None and not located.is_file():
        job.fail("Video không còn trên máy chủ.")
        actions.append("missing_video_file")
        return
    if located is None:
        return
    owner = job.owner_id()
    if owner and owner != "hub":
        if not job.stale(hooks.lease_seconds):
            return
        if job.release(owner):
            job.add_problem("Tự sửa: PC ngừng gửi tiến trình, xếp lại hàng.")
            actions.append("stale_pc_lease")
        if running(job.id) or job.owner_id():
            return
    elif owner == "hub":
        if running(job.id):
            return
        early = job.percent < 10 and not job.has_frames() and hooks.has_idle()
        if not job.abandon_hub():
            return
        if early:
            job.add_problem("Tự sửa: máy chủ nhả video vì PC đang rảnh.")
            actions.append("hub_took_idle_pc_job")
        else:
            job.add_problem("Tự sửa: máy chủ đọc lại video bị đứt.")
            actions.append("hub_reader_lost")
    if job.owner_id() or job.done or running(job.id):
        return
    kind = "schedule" if bound else "prepare"
    if hooks.spawn(job.id, located, kind):
        actions.append("orphan_queued_job" if kind == "schedule" else "orphan_arranging_job")


def _heal_parent(hooks: RepairHooks, job: VideoJob, actions: list[str]) -> None:
    located, _bound = job.located_file()
    if located is None or not located.is_file():
        job.fail("Video không còn trên máy chủ.")
        actions.append("missing_video_file")
        return
    parts = hooks.jobs.parts(job)
    if all(part is not None for part in parts):
        return
    for part in parts:
        if part is not None and not part.done:
            part.fail("Tự sửa: mất một phần, đọc lại cả video.")
    if not job.clear_parts():
        return
    job.add_problem("Tự sửa: mất một phần video, đọc lại cả video.")
    actions.append("stuck_split_parent")
    if running(job.id) or job.done:
        return
    if hooks.spawn(job.id, located, "prepare"):
        actions.append("orphan_arranging_job")
