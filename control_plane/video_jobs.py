"""Tiến trình đọc một video. Nằm trong bộ nhớ, mất khi hub khởi động lại."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from control_plane.screen_steps import ReadProgress


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
        self.path: Path | None = None
        self.owner = ""
        self.lease = 0.0
        self._lock = threading.Lock()

    def bind(self, path: Path) -> None:
        with self._lock:
            self.path = path

    def owner_id(self) -> str:
        with self._lock:
            return self.owner

    def claim(self, worker_id: str) -> bool:
        with self._lock:
            if self.done or self.owner or self.path is None or not worker_id:
                return False
            self.owner = worker_id
            self.lease = time.monotonic()
            self.percent = max(self.percent, 8)
            self.task = "PC phụ đang đọc"
            return True

    def take_hub(self) -> bool:
        with self._lock:
            if self.done or self.owner:
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
    ) -> None:
        with self._lock:
            if self.done:
                return
            self._finish_locked(people, saved_people, archive)

    def finish_from_worker(
        self,
        worker_id: str,
        people: list[dict[str, str]],
        saved_people: int,
        archive: list[dict[str, str]],
    ) -> bool:
        with self._lock:
            if self.done or self.owner != worker_id or not worker_id:
                return False
            self._finish_locked(people, saved_people, archive)
            return True

    def _finish_locked(
        self,
        people: list[dict[str, str]],
        saved_people: int,
        archive: list[dict[str, str]],
    ) -> None:
        self.percent = 100
        self.task = "Đã ghi xong"
        self.done = True
        self.error = ""
        self.people = people
        self.saved_people = saved_people
        self.archive = archive

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
            return body


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, VideoJob] = {}
        self._lock = threading.Lock()

    def create(self) -> VideoJob:
        job = VideoJob(uuid.uuid4().hex)
        with self._lock:
            done_ids = [key for key, item in self._jobs.items() if item.done]
            while len(self._jobs) >= 40 and done_ids:
                self._jobs.pop(done_ids.pop(), None)
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> VideoJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def claim_next(self, worker_id: str) -> VideoJob | None:
        with self._lock:
            for job in self._jobs.values():
                if job.claim(worker_id):
                    return job
        return None


jobs = JobStore()


class JobProgress(ReadProgress):
    def __init__(self, job: VideoJob) -> None:
        self._job = job

    def report(self, percent: int, task: str) -> None:
        self._job.update(percent, task)

    def problem(self, text: str) -> None:
        self._job.add_problem(text)
