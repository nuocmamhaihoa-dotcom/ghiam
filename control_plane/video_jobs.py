"""Tiến trình đọc một video. Nằm trong bộ nhớ, mất khi hub khởi động lại."""

from __future__ import annotations

import threading
import uuid
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
        self._lock = threading.Lock()

    def update(self, percent: int, task: str) -> None:
        with self._lock:
            if self.done:
                return
            clamped = max(0, min(99, int(percent)))
            self.percent = max(self.percent, clamped)
            cleaned = " ".join(task.split())[:180]
            if cleaned:
                self.task = cleaned

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


jobs = JobStore()


class JobProgress(ReadProgress):
    def __init__(self, job: VideoJob) -> None:
        self._job = job

    def report(self, percent: int, task: str) -> None:
        self._job.update(percent, task)

    def problem(self, text: str) -> None:
        self._job.add_problem(text)
