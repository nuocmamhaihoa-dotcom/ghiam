#!/usr/bin/env python3
"""PC kéo video từ hub, đọc bằng mọi lõi của máy này, rồi gửi kết quả về.

Mỗi máy nhận một video. Máy có card NVIDIA và đã cài bộ đọc GPU thì đọc bằng GPU.
Chưa cài thì đọc bằng CPU (Tesseract), cùng cách với hub.

Đặt CONTROL_TOKEN bằng token của hub, rồi chạy:

    python pc_agent/video_worker.py

Hub mặc định là http://222.255.214.202:8088. Đổi bằng --hub hoặc CONTROL_HUB.
Card NVIDIA mà chưa có thư viện: pip install easyocr
hoặc pip install paddlepaddle-gpu paddleocr
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from control_plane.gpu_read import nvidia_name, reader_ready
from control_plane.screen_steps import ReadProgress, ScreenVideoError, analyze_screen_video


class HubClient:
    def __init__(self, hub: str, token: str) -> None:
        self.hub = hub.rstrip("/")
        self.token = token
        self._lock = threading.Lock()

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None, timeout: float | None = 60) -> dict[str, object]:
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(self.hub + path, data=data, headers=headers, method=method)
        with self._lock:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        if not raw:
            return {}
        loaded = json.loads(raw.decode("utf-8"))
        if not isinstance(loaded, dict):
            return {}
        return loaded

    def heartbeat(self, worker_id: str, name: str, cpus: int, gpu: bool = False, gpu_name: str = "") -> str:
        body = self._request(
            "POST",
            "/v1/video-workers/heartbeat",
            {"workerId": worker_id, "name": name, "cpus": cpus, "gpu": gpu, "gpuName": gpu_name},
            timeout=30,
        )
        found = body.get("workerId")
        return found if isinstance(found, str) and found else worker_id

    def claim(self, worker_id: str) -> tuple[str, dict[str, object]]:
        body = self._request("POST", "/v1/recordings/jobs/claim", {"workerId": worker_id}, timeout=30)
        found = body.get("jobId")
        job_id = found if isinstance(found, str) else ""
        resume = body.get("resume")
        return job_id, resume if isinstance(resume, dict) else {}

    def checkpoint(
        self,
        job_id: str,
        worker_id: str,
        frames: list[dict[str, object]],
        people: list[dict[str, str]] | None = None,
    ) -> None:
        payload: dict[str, object] = {"workerId": worker_id, "frames": frames}
        if people is not None:
            payload["people"] = people
        self._request("POST", f"/v1/recordings/jobs/{job_id}/checkpoint", payload, timeout=30)

    def download(self, job_id: str, worker_id: str, dest: Path) -> None:
        request = urllib.request.Request(
            f"{self.hub}/v1/recordings/jobs/{job_id}/video?workerId={worker_id}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=None) as response:
            with dest.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)

    def progress(self, job_id: str, worker_id: str, percent: int, task: str, problems: list[str]) -> None:
        self._request(
            "POST",
            f"/v1/recordings/jobs/{job_id}/progress",
            {"workerId": worker_id, "percent": percent, "task": task, "problems": problems},
            timeout=30,
        )

    def complete(self, job_id: str, worker_id: str, people: list[dict[str, str]]) -> None:
        self._request(
            "POST",
            f"/v1/recordings/jobs/{job_id}/complete",
            {"workerId": worker_id, "people": people},
            timeout=120,
        )

    def fail(self, job_id: str, worker_id: str, message: str) -> None:
        self._request(
            "POST",
            f"/v1/recordings/jobs/{job_id}/fail",
            {"workerId": worker_id, "error": message},
            timeout=30,
        )


class RemoteProgress(ReadProgress):
    """Gửi phần trăm ở luồng riêng để lần đọc chữ không chờ mạng."""

    def __init__(self, client: HubClient, job_id: str, worker_id: str, resume: dict[str, object] | None = None) -> None:
        self._client = client
        self._job_id = job_id
        self._worker_id = worker_id
        self._lock = threading.Lock()
        self._percent = 8
        self._task = "PC phụ đang đọc"
        self._problems: list[str] = []
        self._known: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
        self._pending: list[dict[str, object]] = []
        self._staged: list[dict[str, str]] | None = None
        saved = resume or {}
        frames = saved.get("frames")
        if isinstance(frames, list):
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                raw_t = frame.get("t")
                if isinstance(raw_t, bool) or not isinstance(raw_t, (int, float)):
                    continue
                captions = frame.get("captions") if isinstance(frame.get("captions"), list) else []
                sightings = frame.get("sightings") if isinstance(frame.get("sightings"), list) else []
                self._known[f"{float(raw_t):.3f}"] = (
                    [str(item) for item in captions],
                    [item for item in sightings if isinstance(item, dict)],
                )
        people = saved.get("people")
        if isinstance(people, list):
            self._staged = [item for item in people if isinstance(item, dict)]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def report(self, percent: int, task: str) -> None:
        with self._lock:
            self._percent = percent
            self._task = task or self._task

    def problem(self, text: str) -> None:
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return
        with self._lock:
            if cleaned not in self._problems:
                self._problems.append(cleaned)

    def remembered(self) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
        with self._lock:
            return {key: (list(captions), [dict(item) for item in sightings]) for key, (captions, sightings) in self._known.items()}

    def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
        key = f"{float(seconds):.3f}"
        with self._lock:
            self._known[key] = (list(captions), [dict(item) for item in sightings])
            self._pending.append({"t": float(seconds), "captions": list(captions), "sightings": [dict(item) for item in sightings]})

    def staged_people(self) -> list[dict[str, str]] | None:
        with self._lock:
            if self._staged is None:
                return None
            return [dict(row) for row in self._staged]

    def stage_people(self, people: list[dict[str, str]]) -> None:
        with self._lock:
            self._staged = [dict(row) for row in people]
            pending = self._pending
            self._pending = []
        try:
            self._client.checkpoint(self._job_id, self._worker_id, pending, people)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            with self._lock:
                self._pending = pending + self._pending

    def _snapshot(self) -> tuple[int, str, list[str], list[dict[str, object]]]:
        with self._lock:
            problems = list(self._problems)
            self._problems.clear()
            frames = self._pending
            self._pending = []
            return self._percent, self._task, problems, frames

    def _send(self) -> None:
        percent, task, problems, frames = self._snapshot()
        try:
            if frames:
                self._client.checkpoint(self._job_id, self._worker_id, frames)
            self._client.progress(self._job_id, self._worker_id, percent, task, problems)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            for item in problems:
                self.problem(item)
            if frames:
                with self._lock:
                    self._pending = frames + self._pending

    def _loop(self) -> None:
        while not self._stop.wait(0.4):
            self._send()

    def close(self) -> None:
        self._stop.set()
        self._send()
        self._thread.join(timeout=2)


def _read_one(client: HubClient, worker_id: str, job_id: str, resume: dict[str, object] | None = None) -> None:
    ready = (resume or {}).get("people")
    if isinstance(ready, list):
        client.complete(job_id, worker_id, [row for row in ready if isinstance(row, dict)])
        return
    with tempfile.TemporaryDirectory(prefix="fb-pc-") as folder:
        dest = Path(folder) / "clip.mp4"
        client.progress(job_id, worker_id, 8, "PC phụ đang tải video", [])
        client.download(job_id, worker_id, dest)
        sink = RemoteProgress(client, job_id, worker_id, resume)
        try:
            _steps, people = analyze_screen_video(dest, sink)
        except ScreenVideoError as error:
            sink.close()
            client.fail(job_id, worker_id, str(error))
            return
        finally:
            if not sink._stop.is_set():
                sink.close()
        client.complete(job_id, worker_id, people)


def _prepare_gpu() -> tuple[bool, str]:
    """Bật GPU chỉ khi card NVIDIA có thật và bộ đọc nạp được."""
    card = nvidia_name()
    if not card:
        print("Máy này đọc bằng CPU.", flush=True)
        return False, ""
    os.environ["CONTROL_OCR_ENGINE"] = "gpu"
    if reader_ready():
        print(f"Đọc bằng GPU {card}.", flush=True)
        return True, card
    os.environ.pop("CONTROL_OCR_ENGINE", None)
    print(
        "PC có card NVIDIA nhưng chưa cài bộ đọc GPU. Đang đọc bằng CPU. "
        "Cài bằng pip install easyocr hoặc pip install paddlepaddle-gpu paddleocr.",
        flush=True,
    )
    return False, ""


def main() -> None:
    os.environ.setdefault("CONTROL_OCR_RESERVE", "0")
    parser = argparse.ArgumentParser(description="PC phụ đọc video màn hình cho hub")
    parser.add_argument("--hub", default=os.environ.get("CONTROL_HUB", "http://222.255.214.202:8088"))
    args = parser.parse_args()
    token = os.environ.get("CONTROL_TOKEN", "")
    if not token:
        print("Đặt CONTROL_TOKEN rồi chạy lại.", file=sys.stderr)
        sys.exit(2)
    use_gpu, gpu_name = _prepare_gpu()
    cpus = os.cpu_count() or 1
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "PC"
    client = HubClient(args.hub, token)
    state = {"worker_id": "", "stop": threading.Event()}

    def beat() -> None:
        while not state["stop"].wait(5):
            try:
                state["worker_id"] = client.heartbeat(state["worker_id"], name, cpus, use_gpu, gpu_name)
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                pass

    try:
        state["worker_id"] = client.heartbeat("", name, cpus, use_gpu, gpu_name)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print("Chưa nối được hub.", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Đã nối hub. Máy này có {cpus} lõi, dùng hết để đọc video.", flush=True)
    threading.Thread(target=beat, daemon=True).start()
    while True:
        try:
            job_id, resume = client.claim(state["worker_id"])
            if not job_id:
                time.sleep(0.4)
                continue
            print(f"Nhận video {job_id}.", flush=True)
            _read_one(client, state["worker_id"], job_id, resume)
            print(f"Xong video {job_id}.", flush=True)
        except KeyboardInterrupt:
            state["stop"].set()
            raise
        except urllib.error.HTTPError as error:
            print(f"Hub trả {error.code}. Thử video sau.", file=sys.stderr)
            time.sleep(1)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            print("Mất kết nối hub, thử lại.", file=sys.stderr)
            time.sleep(2)


if __name__ == "__main__":
    main()
