"""Tiến trình đọc một video. Việc đang chạy nằm trong bộ nhớ; sổ SQLite giữ hàng chờ và lịch sử."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from control_plane.screen_steps import _MAX_FRAMES, ReadProgress, discard_video_work
from control_plane.video_ledger import current_rev, mark_missing, open_rows, save_job

# Mỗi khung đã đọc đều được nhớ để đọc nối, và để ghép hai phần video của hai PC.
_FRAME_LIMIT = _MAX_FRAMES
_PEOPLE_LIMIT = 20_000


def _step_rank(task: str) -> int:
    """Thứ tự bước trên thanh. 0 là nhãn lạ, luôn được ghi. Bước sau không bị nhãn bước trước ghi đè."""
    text = " ".join(task.split())
    if not text:
        return 0
    if "Đọc lại" in text:
        return 4
    if text.startswith("Ghép") or text.startswith("Ghi"):
        return 5
    if "Đọc chữ" in text or "Đọc tiếp" in text:
        return 3
    if "Tách" in text or "Chọn khung" in text or "thời lượng" in text:
        return 2
    if "Đã nhận" in text or "Đang gửi" in text or "Đang chờ" in text or "PC phụ đang đọc" in text:
        return 1
    return 0


def _load_frames(raw: object) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
    frames: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    if not isinstance(raw, list):
        return frames
    for frame in raw:
        if not isinstance(frame, dict) or len(frames) >= _FRAME_LIMIT:
            continue
        try:
            stamp = float(frame.get("t"))
        except (TypeError, ValueError):
            continue
        captions = frame.get("captions") if isinstance(frame.get("captions"), list) else []
        sightings = frame.get("sightings") if isinstance(frame.get("sightings"), list) else []
        kept_captions = [" ".join(str(item).split())[:180] for item in captions[:20] if str(item).strip()]
        kept_sightings: list[dict[str, str]] = []
        for item in sightings[:30]:
            if not isinstance(item, dict):
                continue
            kept_sightings.append(
                {
                    "kind": " ".join(str(item.get("kind") or "").split())[:40],
                    "name": " ".join(str(item.get("name") or "").split())[:80],
                    "contactName": " ".join(str(item.get("contactName") or "").split())[:80],
                    "username": " ".join(str(item.get("username") or "").split())[:40],
                }
            )
        frames[f"{stamp:.3f}"] = (kept_captions, kept_sightings)
    return frames


def _tally_from(row: dict[str, Any]) -> dict[str, int] | None:
    try:
        contacts = max(0, int(row.get("seenContacts") or 0))
        accounts = max(0, int(row.get("seenAccounts") or 0))
        saved = max(0, int(row.get("savedPeople") or 0))
    except (TypeError, ValueError):
        return None
    if not contacts and not accounts and not saved:
        return None
    return {"contacts": contacts, "accounts": accounts, "saved": saved}


class VideoJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.percent = 0
        self.task = "Đang chờ"
        self.problems: list[str] = []
        self.learned = ""
        self.done = False
        self.error = ""
        self.people: list[dict[str, str]] = []
        self.saved_people = 0
        self.archive: list[dict[str, str]] = []
        self.archive_count = 0
        self.duplicates: list[dict[str, str]] = []
        self.path: Path | None = None
        # Đường dẫn ghi vào sổ trước khi cho PC nhận. PC chỉ nhận khi path đã gắn.
        self._stored_path: Path | None = None
        self.owner = ""
        self.lease = 0.0
        self._frames: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
        self._staged: list[dict[str, str]] | None = None
        self._tally: dict[str, int] | None = None
        self._words: dict[str, tuple[int, int]] = {}
        self._samples: list[tuple[str, bytes]] = []
        self._reread = False
        # Video dài chia cho hai PC: việc cha giữ video gốc, mỗi phần là một việc con để PC nhận.
        self.parent_id = ""
        self.part_ids: list[str] = []
        self.part_label = ""
        self.name = ""
        self.source = ""
        self.size = 0
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._rev = 0
        self._merging = False
        self._lock = threading.Lock()

    def _state_locked(self) -> str:
        if self.done and self.error:
            return "failed"
        if self.done:
            return "done"
        if self.owner:
            return "reading"
        return "queued"

    def _snapshot_locked(self) -> dict[str, Any]:
        frames = [
            {"t": float(key), "captions": list(captions), "sightings": [dict(item) for item in sightings]}
            for key, (captions, sightings) in self._frames.items()
        ]
        self._rev += 1
        stored = self.path if self.path is not None else self._stored_path
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "size": self.size,
            "state": self._state_locked(),
            "percent": self.percent,
            "task": self.task,
            "problems": list(self.problems),
            "error": self.error,
            "worker": self.owner,
            "path": str(stored) if stored is not None else "",
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "savedPeople": self.saved_people,
            "seenContacts": max(0, int((self._tally or {}).get("contacts") or 0)),
            "seenAccounts": max(0, int((self._tally or {}).get("accounts") or 0)),
            "parentId": self.parent_id,
            "partLabel": self.part_label,
            "partIds": list(self.part_ids),
            "frames": frames,
            "rev": self._rev,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _persist(self, row: dict[str, Any], force: bool) -> None:
        try:
            save_job(row, force=force)
        except Exception:
            return

    def stage_file(self, path: Path) -> None:
        """Ghi đường dẫn vào sổ để hub khởi động lại còn file. Chưa cho PC nhận."""
        with self._lock:
            self._stored_path = path
            row = self._snapshot_locked()
        self._persist(row, True)

    def bind(self, path: Path) -> None:
        with self._lock:
            self.path = path
            self._stored_path = path
            row = self._snapshot_locked()
        self._persist(row, True)

    def set_parts(self, part_ids: list[str]) -> None:
        with self._lock:
            self.part_ids = list(part_ids)
            row = self._snapshot_locked()
        self._persist(row, True)

    def begin_merge(self) -> bool:
        with self._lock:
            if self._merging or self.done:
                return False
            self._merging = True
            return True

    def end_merge(self) -> None:
        with self._lock:
            self._merging = False

    def words(self) -> dict[str, tuple[int, int]]:
        with self._lock:
            return dict(self._words)

    def finish_part(self, worker_id: str | None) -> bool:
        """Một phần đã đọc xong. Chưa ghi người: việc cha ghép các phần rồi mới ghi."""
        with self._lock:
            if self.done:
                return False
            if worker_id is not None and (not worker_id or self.owner != worker_id):
                return False
            self.percent = 100
            self.task = "Đã đọc xong phần này"
            self.done = True
            self.error = ""
            self.finished_at = time.time()
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def source_path(self) -> Path | None:
        with self._lock:
            return self.path

    def located_file(self) -> tuple[Path | None, bool]:
        """Đường dẫn đang nhớ, và cờ cho biết PC đã được phép nhận video này."""
        with self._lock:
            if self.path is not None:
                return self.path, True
            return self._stored_path, False

    def has_frames(self) -> bool:
        with self._lock:
            return bool(self._frames)

    def abandon_hub(self) -> bool:
        """Máy chủ giữ video nhưng luồng đọc đã mất. Trả về hàng chờ."""
        with self._lock:
            if self.done or self.owner != "hub":
                return False
            self.owner = ""
            self.lease = 0.0
            self.task = "Đọc tiếp"
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def clear_parts(self) -> bool:
        with self._lock:
            if self.done or not self.part_ids:
                return False
            self.part_ids = []
            self.task = "Đọc tiếp"
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def succeeded(self) -> bool:
        with self._lock:
            return self.done and not self.error

    def discard(self) -> None:
        with self._lock:
            path = self.path if self.path is not None else self._stored_path
            self.path = None
            self._stored_path = None
            row = self._snapshot_locked()
        self._persist(row, True)
        if path is not None:
            discard_video_work(path)

    def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
        key = f"{float(seconds):.3f}"
        kept_captions = [" ".join(str(item).split())[:180] for item in captions[:20] if str(item).strip()]
        kept_sightings: list[dict[str, str]] = []
        for item in sightings[:30]:
            if not isinstance(item, dict):
                continue
            kept_sightings.append(
                {
                    "kind": " ".join(str(item.get("kind") or "").split())[:40],
                    "name": " ".join(str(item.get("name") or "").split())[:80],
                    "contactName": " ".join(str(item.get("contactName") or "").split())[:80],
                    "username": " ".join(str(item.get("username") or "").split())[:40],
                }
            )
        with self._lock:
            if key not in self._frames and len(self._frames) >= _FRAME_LIMIT:
                return
            self._frames[key] = (kept_captions, kept_sightings)
            row = self._snapshot_locked()
        self._persist(row, False)

    def remembered(self) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
        with self._lock:
            return {key: (list(captions), [dict(item) for item in sightings]) for key, (captions, sightings) in self._frames.items()}

    def stage_people(self, people: list[dict[str, str]]) -> None:
        cleaned: list[dict[str, str]] = []
        for row in people[:_PEOPLE_LIMIT]:
            if not isinstance(row, dict):
                continue
            cleaned.append(
                {
                    "name": " ".join(str(row.get("name") or "").split())[:80],
                    "contactName": " ".join(str(row.get("contactName") or "").split())[:80],
                    "username": " ".join(str(row.get("username") or "").split())[:40],
                }
            )
        with self._lock:
            self._staged = cleaned

    def note_tally(self, contacts: int, accounts: int, saved: int) -> None:
        with self._lock:
            self._tally = {
                "contacts": max(0, int(contacts)),
                "accounts": max(0, int(accounts)),
                "saved": max(0, int(saved)),
            }

    def note_words(self, source: str, seen: int, kept: int) -> None:
        key = "hub" if source == "hub" else "pc"
        with self._lock:
            self._words[key] = (max(0, int(seen)), max(0, int(kept)))

    def note_samples(self, source: str, images: list[bytes]) -> None:
        label = "hub" if source == "hub" else "pc"
        cleaned: list[tuple[str, bytes]] = []
        for raw in images[:3]:
            if isinstance(raw, (bytes, bytearray)) and raw and len(raw) <= 150_000:
                cleaned.append((label, bytes(raw)))
        with self._lock:
            kept = [(item_source, data) for item_source, data in self._samples if item_source != label]
            self._samples = (kept + cleaned)[:6]

    def sample_jpeg(self, index: int) -> bytes | None:
        with self._lock:
            if index < 0 or index >= len(self._samples):
                return None
            return self._samples[index][1]

    def mark_reread(self) -> bool:
        """Hub đọc lại video này một lần. Khung PC đã ghi là trống thì không dùng lại."""
        with self._lock:
            if self._reread or self.done:
                return False
            self._reread = True
            self._frames.clear()
            self._staged = None
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def reread_started(self) -> bool:
        with self._lock:
            return self._reread

    def staged_people(self) -> list[dict[str, str]] | None:
        with self._lock:
            if self._staged is None:
                return None
            return [dict(row) for row in self._staged]

    def resume_public(self) -> dict[str, Any]:
        with self._lock:
            frames = [
                {"t": float(key), "captions": list(captions), "sightings": [dict(item) for item in sightings]}
                for key, (captions, sightings) in self._frames.items()
            ]
            body: dict[str, Any] = {"frames": frames}
            if self._staged is not None:
                body["people"] = [dict(row) for row in self._staged]
            return body

    def reopen(self) -> bool:
        """Lỗi xong mà file còn thì đọc nối. Phần đã đọc được giữ."""
        with self._lock:
            if not self.done or not self.error or self.path is None or not self.path.is_file():
                return False
            self.done = False
            self.error = ""
            self.owner = ""
            self.lease = 0.0
            self.finished_at = None
            self.task = "Đọc tiếp"
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def owner_id(self) -> str:
        with self._lock:
            return self.owner

    def _claim_mark(self, worker_id: str) -> bool:
        with self._lock:
            if self.done or self.owner or self.path is None or self.part_ids or not worker_id:
                return False
            self.owner = worker_id
            self.lease = time.monotonic()
            self.percent = max(self.percent, 8)
            self.task = "PC phụ đang đọc"
            if self.started_at is None:
                self.started_at = time.time()
            return True

    def claim(self, worker_id: str) -> bool:
        if not self._claim_mark(worker_id):
            return False
        self._persist(self.snapshot(), True)
        return True

    def take_hub(self) -> bool:
        with self._lock:
            if self.done or self.owner or self.part_ids:
                return False
            self.owner = "hub"
            self.lease = time.monotonic()
            if self.started_at is None:
                self.started_at = time.time()
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def release(self, worker_id: str) -> bool:
        with self._lock:
            if self.done or not worker_id or self.owner != worker_id or worker_id == "hub":
                return False
            self.owner = ""
            self.lease = 0.0
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def hold_for_growth(self) -> None:
        """File còn đang dài. Giữ đường dẫn nhưng chưa cho PC nhận, để khỏi sắp xếp lại file dở."""
        with self._lock:
            if self.done:
                return
            if self.path is not None:
                self._stored_path = self.path
                self.path = None
            row = self._snapshot_locked()
        self._persist(row, True)

    def note_worker(self, worker_id: str) -> bool:
        with self._lock:
            if self.done or self.owner != worker_id or not worker_id:
                return False
            self.lease = time.monotonic()
            return True

    def video_path(self, worker_id: str) -> Path | None:
        with self._lock:
            if self.owner != worker_id or self.path is None:
                return None
            return self.path

    def stale(self, seconds: float) -> bool:
        with self._lock:
            if self.done or not self.owner or self.owner == "hub":
                return False
            return (time.monotonic() - self.lease) > seconds

    def update(self, percent: int, task: str) -> None:
        with self._lock:
            if self.done:
                return
            clamped = max(0, min(99, int(percent)))
            self.percent = max(self.percent, clamped)
            cleaned = " ".join(task.split())[:180]
            if cleaned and _step_rank(cleaned) and _step_rank(cleaned) < _step_rank(self.task):
                cleaned = ""
            if cleaned:
                self.task = cleaned
            if self.owner and self.owner != "hub":
                self.lease = time.monotonic()
            row = self._snapshot_locked()
        self._persist(row, False)

    def note_learned(self, text: str) -> None:
        cleaned = " ".join(str(text).split())[:400]
        if not cleaned:
            return
        with self._lock:
            self.learned = cleaned

    def add_problem(self, text: str) -> None:
        cleaned = " ".join(str(text).split())[:180]
        if not cleaned:
            return
        with self._lock:
            if len(self.problems) >= 20 or cleaned in self.problems:
                return
            self.problems.append(cleaned)
            row = self._snapshot_locked()
        self._persist(row, True)
        try:
            from control_plane.issues import record_video_problem

            record_video_problem(self.id, cleaned)
        except Exception:
            pass

    def finish(
        self,
        people: list[dict[str, str]],
        saved_people: int,
        archive: list[dict[str, str]],
        archive_count: int = 0,
        duplicates: list[dict[str, str]] | None = None,
    ) -> None:
        with self._lock:
            if self.done:
                return
            self._finish_locked(people, saved_people, archive, archive_count, duplicates)
            row = self._snapshot_locked()
        self._persist(row, True)

    def finish_from_worker(
        self,
        worker_id: str,
        people: list[dict[str, str]],
        saved_people: int,
        archive: list[dict[str, str]],
        archive_count: int = 0,
        duplicates: list[dict[str, str]] | None = None,
    ) -> bool:
        with self._lock:
            if self.done or self.owner != worker_id or not worker_id:
                return False
            self._finish_locked(people, saved_people, archive, archive_count, duplicates)
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def _finish_locked(
        self,
        people: list[dict[str, str]],
        saved_people: int,
        archive: list[dict[str, str]],
        archive_count: int = 0,
        duplicates: list[dict[str, str]] | None = None,
    ) -> None:
        self.percent = 100
        self.task = "Đã ghi xong"
        self.done = True
        self.error = ""
        self.people = people
        self.saved_people = saved_people
        self.archive = archive
        self.archive_count = archive_count
        self.duplicates = list(duplicates or [])
        self.finished_at = time.time()

    def fail(self, message: str) -> None:
        cleaned = " ".join(str(message).split())[:180] or "Gặp vấn đề"
        with self._lock:
            if self.done:
                return
            self._fail_locked(cleaned)
            row = self._snapshot_locked()
        self._persist(row, True)

    def fail_from_worker(self, worker_id: str, message: str) -> bool:
        cleaned = " ".join(str(message).split())[:180] or "Gặp vấn đề"
        with self._lock:
            if self.done or self.owner != worker_id or not worker_id:
                return False
            self._fail_locked(cleaned)
            row = self._snapshot_locked()
        self._persist(row, True)
        return True

    def _fail_locked(self, cleaned: str) -> None:
        self.task = "Gặp vấn đề"
        self.done = True
        self.error = cleaned
        self.finished_at = time.time()
        if cleaned not in self.problems and len(self.problems) < 20:
            self.problems.append(cleaned)
        try:
            from control_plane.issues import record_job_failure

            record_job_failure(self.id, cleaned)
        except Exception:
            pass

    def public(self) -> dict[str, Any]:
        with self._lock:
            body: dict[str, Any] = {
                "ok": True,
                "jobId": self.id,
                "percent": self.percent,
                "task": self.task,
                "problems": list(self.problems),
                "done": self.done,
                "error": self.error,
            }
            if self.learned:
                body["learned"] = self.learned
            if self.done and not self.error:
                body["people"] = list(self.people)
                body["savedPeople"] = self.saved_people
                body["archive"] = list(self.archive)
                body["archiveCount"] = self.archive_count
                body["duplicates"] = list(self.duplicates)
                if self._tally is not None:
                    body["seenContacts"] = self._tally["contacts"]
                    body["seenAccounts"] = self._tally["accounts"]
                    body["readSaved"] = self._tally["saved"]
            pc_words = self._words.get("pc")
            hub_words = self._words.get("hub")
            if pc_words is not None:
                body["wordSeen"] = pc_words[0]
                body["wordKept"] = pc_words[1]
            if hub_words is not None:
                body["hubWordSeen"] = hub_words[0]
                body["hubWordKept"] = hub_words[1]
            body["sampleCount"] = len(self._samples)
            body["samples"] = [
                {"index": index, "source": source} for index, (source, _data) in enumerate(self._samples)
            ]
            path = self.path
            failed = self.done and bool(self.error)
        body["canContinue"] = failed and path is not None and path.is_file()
        return body


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, VideoJob] = {}
        self._lock = threading.Lock()

    def create(self, job_id: str = "", *, name: str = "", source: str = "", size: int = 0) -> VideoJob:
        key = "".join(ch for ch in str(job_id or "") if ch.isalnum())[:64] or uuid.uuid4().hex
        job = VideoJob(key)
        job.name = " ".join(str(name or "").split())[:120]
        job.source = " ".join(str(source or "").split())[:80]
        job.size = max(0, int(size or 0))
        job.created_at = time.time()
        job._rev = current_rev(key)
        with self._lock:
            current = self._jobs.get(key)
            if current is not None:
                return current
            dropped = self._take_room_locked()
            self._jobs[job.id] = job
        self._release_dropped(dropped)
        job._persist(job.snapshot(), True)
        return job

    def adopt(self, row: dict[str, Any]) -> VideoJob | None:
        """Dựng lại việc chưa xong. Máy đang đọc được bỏ, để nhận lại từ đầu hàng."""
        job_id = "".join(ch for ch in str(row.get("id") or "") if ch.isalnum())[:64]
        if not job_id:
            return None
        job = VideoJob(job_id)
        job.name = " ".join(str(row.get("name") or "").split())[:120]
        job.source = " ".join(str(row.get("source") or "").split())[:80]
        job.size = max(0, int(row.get("size") or 0))
        job.percent = max(0, min(100, int(row.get("percent") or 0)))
        job.problems = [str(item) for item in row.get("problems") or [] if str(item).strip()][:20]
        job.saved_people = max(0, int(row.get("savedPeople") or 0))
        job.parent_id = " ".join(str(row.get("parentId") or "").split())[:64]
        job.part_label = " ".join(str(row.get("partLabel") or "").split())[:40]
        job.part_ids = [str(item) for item in row.get("partIds") or [] if str(item).strip()]
        raw_path = str(row.get("path") or "")
        stored = Path(raw_path) if raw_path else None
        job._stored_path = stored
        created = row.get("createdAt")
        job.created_at = float(created) if isinstance(created, (int, float)) else time.time()
        started = row.get("startedAt")
        job.started_at = float(started) if isinstance(started, (int, float)) else None
        previous = str(row.get("state") or "")
        task = " ".join(str(row.get("task") or "").split())[:180]
        arranging = task == "Đang sắp xếp video"
        job.path = None if arranging else stored
        if arranging:
            job.task = task
        elif job.part_ids:
            job.task = task or "Chia video cho hai PC"
        elif previous == "reading":
            job.task = "Đọc tiếp"
        else:
            job.task = task or "Đang chờ"
        try:
            job._rev = max(0, int(row.get("rev") or 0))
        except (TypeError, ValueError):
            job._rev = 0
        job._frames = _load_frames(row.get("frames"))
        job._tally = _tally_from(row)
        with self._lock:
            if job_id in self._jobs:
                return None
            self._jobs[job_id] = job
        job._persist(job.snapshot(), True)
        return job

    def recall(self, row: dict[str, Any]) -> VideoJob | None:
        """Dựng lại video đã lỗi để bấm Đọc lại. Chưa ghi sổ cho đến khi đọc nối."""
        job_id = "".join(ch for ch in str(row.get("id") or "") if ch.isalnum())[:64]
        if not job_id:
            return None
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                return current
        job = VideoJob(job_id)
        job.name = " ".join(str(row.get("name") or "").split())[:120]
        job.source = " ".join(str(row.get("source") or "").split())[:80]
        job.size = max(0, int(row.get("size") or 0))
        job.percent = max(0, min(100, int(row.get("percent") or 0)))
        job.task = " ".join(str(row.get("task") or "").split())[:180] or "Gặp vấn đề"
        job.problems = [str(item) for item in row.get("problems") or [] if str(item).strip()][:20]
        job.saved_people = max(0, int(row.get("savedPeople") or 0))
        job._tally = _tally_from(row)
        job.parent_id = " ".join(str(row.get("parentId") or "").split())[:64]
        job.part_label = " ".join(str(row.get("partLabel") or "").split())[:40]
        job.part_ids = [str(item) for item in row.get("partIds") or [] if str(item).strip()]
        raw_path = str(row.get("path") or "")
        if raw_path:
            job.path = Path(raw_path)
            job._stored_path = job.path
        created = row.get("createdAt")
        job.created_at = float(created) if isinstance(created, (int, float)) else time.time()
        started = row.get("startedAt")
        job.started_at = float(started) if isinstance(started, (int, float)) else None
        finished = row.get("finishedAt")
        job.finished_at = float(finished) if isinstance(finished, (int, float)) else time.time()
        job.done = True
        job.error = " ".join(str(row.get("error") or "").split())[:180] or "Gặp vấn đề"
        try:
            job._rev = max(0, int(row.get("rev") or 0))
        except (TypeError, ValueError):
            job._rev = 0
        job._frames = _load_frames(row.get("frames"))
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                return current
            dropped = self._take_room_locked()
            self._jobs[job_id] = job
        self._release_dropped(dropped)
        return job

    def _take_room_locked(self) -> list[VideoJob]:
        """Đẩy việc đã xong ra khỏi bộ nhớ. Gọi khi đang giữ khóa kho."""
        dropped: list[VideoJob] = []
        done_ids = [item_id for item_id, item in self._jobs.items() if item.done and not self._waiting_part(item)]
        while len(self._jobs) >= 40 and done_ids:
            old = self._jobs.pop(done_ids.pop(), None)
            if old is not None:
                dropped.append(old)
        return dropped

    def _release_dropped(self, dropped: list[VideoJob]) -> None:
        """Video ghi xong thì xóa file. Video lỗi chỉ rời bộ nhớ, file còn để đọc lại."""
        for old in dropped:
            if old.error:
                continue
            old.discard()

    def _waiting_part(self, item: VideoJob) -> bool:
        """Phần đã xong nhưng việc cha chưa ghép thì giữ lại. Gọi khi đang giữ khóa."""
        if not item.parent_id:
            return False
        parent = self._jobs.get(item.parent_id)
        return parent is not None and not parent.done

    def get(self, job_id: str) -> VideoJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[VideoJob]:
        with self._lock:
            return list(self._jobs.values())

    def parts(self, job: VideoJob) -> list[VideoJob | None]:
        with self._lock:
            return [self._jobs.get(part_id) for part_id in job.part_ids]

    def claim_next(self, worker_id: str, spread: bool = False) -> VideoJob | None:
        """spread: PC đang giữ một phần của video thì để phần kia cho PC khác."""
        chosen: VideoJob | None = None
        with self._lock:
            for job in self._jobs.values():
                if spread and job.parent_id and self._holds_sibling(job, worker_id):
                    continue
                if job._claim_mark(worker_id):
                    chosen = job
                    break
        if chosen is not None:
            chosen._persist(chosen.snapshot(), True)
        return chosen

    def _holds_sibling(self, job: VideoJob, worker_id: str) -> bool:
        parent = self._jobs.get(job.parent_id)
        if parent is None:
            return False
        for part_id in parent.part_ids:
            sibling = self._jobs.get(part_id)
            if sibling is not None and sibling is not job and sibling.owner_id() == worker_id:
                return True
        return False


jobs = JobStore()


def restore_open() -> list[tuple[str, Path]]:
    """Dựng lại việc còn file. Việc đang gửi nằm ở sổ lần gửi, không đọc ở đây."""
    ready: list[tuple[str, Path]] = []
    for row in open_rows():
        job_id = str(row.get("id") or "")
        if not job_id or jobs.get(job_id) is not None:
            continue
        raw_path = str(row.get("path") or "")
        path = Path(raw_path) if raw_path else None
        if path is None or not path.is_file():
            mark_missing(job_id)
            continue
        job = jobs.adopt(row)
        if job is None or job.part_ids:
            continue
        ready.append((job.id, path))
    return ready


class JobProgress(ReadProgress):
    def __init__(self, job: VideoJob) -> None:
        self._job = job

    def report(self, percent: int, task: str) -> None:
        self._job.update(percent, task)

    def problem(self, text: str) -> None:
        self._job.add_problem(text)

    def remembered(self) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
        return self._job.remembered()

    def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
        self._job.remember_frame(seconds, captions, sightings)

    def staged_people(self) -> list[dict[str, str]] | None:
        return self._job.staged_people()

    def stage_people(self, people: list[dict[str, str]]) -> None:
        self._job.stage_people(people)

    def note_tally(self, contacts: int, accounts: int, saved: int) -> None:
        self._job.note_tally(contacts, accounts, saved)

    def note_words(self, seen: int, kept: int) -> None:
        self._job.note_words("hub", seen, kept)

    def note_samples(self, images: list[bytes]) -> None:
        if not images:
            return
        self._job.note_samples("hub", images)
        parent = jobs.get(self._job.parent_id) if self._job.parent_id else None
        if parent is not None:
            parent.note_samples("hub", images)

    def note_blank(self) -> None:
        return

    def note_learned(self, text: str) -> None:
        self._job.note_learned(text)
