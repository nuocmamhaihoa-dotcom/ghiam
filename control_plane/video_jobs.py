"""Tiến trình đọc một video. Nằm trong bộ nhớ, mất khi hub khởi động lại."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from control_plane.screen_steps import _MAX_FRAMES, ReadProgress, discard_video_work

# Mỗi khung đã đọc đều được nhớ để đọc nối, và để ghép hai phần video của hai PC.
_FRAME_LIMIT = _MAX_FRAMES
_PEOPLE_LIMIT = 20_000


class VideoJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.percent = 0
        self.task = "Đang chờ"
        self.problems: list[str] = []
        self.done = False
        self.error = ""
        self.people: list[dict[str, str]] = []
        self.saved_people = 0
        self.archive: list[dict[str, str]] = []
        self.archive_count = 0
        self.duplicates: list[dict[str, str]] = []
        self.path: Path | None = None
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
        self._merging = False
        self._lock = threading.Lock()

    def bind(self, path: Path) -> None:
        with self._lock:
            self.path = path

    def set_parts(self, part_ids: list[str]) -> None:
        with self._lock:
            self.part_ids = list(part_ids)

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
            return True

    def source_path(self) -> Path | None:
        with self._lock:
            return self.path

    def succeeded(self) -> bool:
        with self._lock:
            return self.done and not self.error

    def discard(self) -> None:
        with self._lock:
            path = self.path
            self.path = None
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
            self.task = "Đọc tiếp"
            return True

    def owner_id(self) -> str:
        with self._lock:
            return self.owner

    def claim(self, worker_id: str) -> bool:
        with self._lock:
            if self.done or self.owner or self.path is None or self.part_ids or not worker_id:
                return False
            self.owner = worker_id
            self.lease = time.monotonic()
            self.percent = max(self.percent, 8)
            self.task = "PC phụ đang đọc"
            return True

    def take_hub(self) -> bool:
        with self._lock:
            if self.done or self.owner or self.part_ids:
                return False
            self.owner = "hub"
            self.lease = time.monotonic()
            return True

    def release(self, worker_id: str) -> bool:
        with self._lock:
            if self.done or not worker_id or self.owner != worker_id or worker_id == "hub":
                return False
            self.owner = ""
            self.lease = 0.0
            return True

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
            if cleaned:
                self.task = cleaned
            if self.owner and self.owner != "hub":
                self.lease = time.monotonic()

    def add_problem(self, text: str) -> None:
        cleaned = " ".join(str(text).split())[:180]
        if not cleaned:
            return
        with self._lock:
            if len(self.problems) >= 20 or cleaned in self.problems:
                return
            self.problems.append(cleaned)

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

    def fail(self, message: str) -> None:
        cleaned = " ".join(str(message).split())[:180] or "Gặp vấn đề"
        with self._lock:
            if self.done:
                return
            self._fail_locked(cleaned)

    def fail_from_worker(self, worker_id: str, message: str) -> bool:
        cleaned = " ".join(str(message).split())[:180] or "Gặp vấn đề"
        with self._lock:
            if self.done or self.owner != worker_id or not worker_id:
                return False
            self._fail_locked(cleaned)
            return True

    def _fail_locked(self, cleaned: str) -> None:
        self.task = "Gặp vấn đề"
        self.done = True
        self.error = cleaned
        if cleaned not in self.problems and len(self.problems) < 20:
            self.problems.append(cleaned)

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

    def create(self) -> VideoJob:
        job = VideoJob(uuid.uuid4().hex)
        dropped: list[VideoJob] = []
        with self._lock:
            done_ids = [key for key, item in self._jobs.items() if item.done and not self._waiting_part(item)]
            while len(self._jobs) >= 40 and done_ids:
                old = self._jobs.pop(done_ids.pop(), None)
                if old is not None:
                    dropped.append(old)
            self._jobs[job.id] = job
        for old in dropped:
            old.discard()
        return job

    def _waiting_part(self, item: VideoJob) -> bool:
        """Phần đã xong nhưng việc cha chưa ghép thì giữ lại. Gọi khi đang giữ khóa."""
        if not item.parent_id:
            return False
        parent = self._jobs.get(item.parent_id)
        return parent is not None and not parent.done

    def get(self, job_id: str) -> VideoJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def parts(self, job: VideoJob) -> list[VideoJob | None]:
        with self._lock:
            return [self._jobs.get(part_id) for part_id in job.part_ids]

    def claim_next(self, worker_id: str, spread: bool = False) -> VideoJob | None:
        """spread: PC đang giữ một phần của video thì để phần kia cho PC khác."""
        with self._lock:
            for job in self._jobs.values():
                if spread and job.parent_id and self._holds_sibling(job, worker_id):
                    continue
                if job.claim(worker_id):
                    return job
        return None

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
