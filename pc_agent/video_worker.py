#!/usr/bin/env python3
"""PC kéo video từ hub, đọc bằng mọi lõi của máy này, rồi gửi kết quả về.

Mỗi máy nhận một video. Máy có card NVIDIA và đã cài bộ đọc GPU thì đọc bằng GPU.
Chưa cài thì đọc bằng CPU (Tesseract), cùng cách với hub.

Trên Windows, một lệnh cài Python, ffmpeg, Tesseract vie+eng, lưu token, và chạy khi đăng nhập.
Lệnh nằm ở đầu pc_agent/windows/Install-VideoWorker.ps1. Bản mới tự tải khi máy không đang đọc video.

Chạy tay: đặt CONTROL_TOKEN bằng token của hub, rồi

    python pc_agent/video_worker.py

Hub mặc định là http://222.255.214.202:8088. Đổi bằng --hub hoặc CONTROL_HUB.
Card NVIDIA mà chưa có thư viện: pip install easyocr
hoặc pip install paddlepaddle-gpu paddleocr
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
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

from control_plane.gpu_read import fallback_note, nvidia_name, reader_ready
from control_plane.screen_steps import ReadProgress, ScreenVideoError, analyze_screen_video


def say(text: str, *, err: bool = False) -> None:
    """In ra console mà không chết khi Windows dùng bảng mã cũ."""
    stream = sys.stderr if err else sys.stdout
    payload = (text + "\n").encode("utf-8", errors="replace")
    buffer = getattr(stream, "buffer", None)
    try:
        if buffer is not None:
            buffer.write(payload)
            buffer.flush()
            return
        stream.write(text + "\n")
        stream.flush()
    except Exception:
        return


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
        tally: dict[str, int] | None = None,
    ) -> None:
        payload: dict[str, object] = {"workerId": worker_id, "frames": frames}
        if people is not None:
            payload["people"] = people
        if tally is not None:
            payload["seenContacts"] = tally["contacts"]
            payload["seenAccounts"] = tally["accounts"]
            payload["readSaved"] = tally["saved"]
        self._request("POST", f"/v1/recordings/jobs/{job_id}/checkpoint", payload, timeout=180)

    def download(self, job_id: str, worker_id: str, dest: Path) -> None:
        """Tải video theo từng khúc. Mạng đứt thì nối từ byte đã ghi, không tải lại từ đầu."""
        url = f"{self.hub}/v1/recordings/jobs/{job_id}/video?workerId={worker_id}"
        last_error: Exception | None = None
        for attempt in range(8):
            have = dest.stat().st_size if dest.is_file() else 0
            headers = {"Authorization": f"Bearer {self.token}"}
            if have:
                headers["Range"] = f"bytes={have}-"
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=_READ_STALL_SEC) as response:
                    code = int(getattr(response, "status", 200) or 200)
                    if have and code == 200:
                        have = 0
                    total = _declared_total(response.headers, have, code)
                    mode = "ab" if have and code == 206 else "wb"
                    written = have if mode == "ab" else 0
                    with dest.open(mode) as handle:
                        while True:
                            try:
                                chunk = response.read(1024 * 1024)
                            except http.client.IncompleteRead as error:
                                if error.partial:
                                    handle.write(error.partial)
                                    written += len(error.partial)
                                break
                            if not chunk:
                                break
                            handle.write(chunk)
                            written += len(chunk)
                    if total is None or written >= total:
                        return
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code == 416 and have:
                    return
                if error.code in {401, 404, 409}:
                    raise
            except (OSError, urllib.error.URLError, TimeoutError, socket.timeout, http.client.IncompleteRead) as error:
                last_error = error
            time.sleep(min(8.0, 0.4 * (2**attempt)))
        if last_error is not None:
            raise OSError("Chưa tải hết video.") from last_error
        raise OSError("Chưa tải hết video.")

    def progress(self, job_id: str, worker_id: str, percent: int, task: str, problems: list[str]) -> None:
        self._request(
            "POST",
            f"/v1/recordings/jobs/{job_id}/progress",
            {"workerId": worker_id, "percent": percent, "task": task, "problems": problems},
            timeout=60,
        )

    def complete(
        self,
        job_id: str,
        worker_id: str,
        people: list[dict[str, str]],
        tally: dict[str, int] | None = None,
    ) -> None:
        payload: dict[str, object] = {"workerId": worker_id, "people": people}
        if tally is not None:
            payload["seenContacts"] = tally["contacts"]
            payload["seenAccounts"] = tally["accounts"]
            payload["readSaved"] = tally["saved"]
        self._request(
            "POST",
            f"/v1/recordings/jobs/{job_id}/complete",
            payload,
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
        self._tally: dict[str, int] | None = None
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

    def note_tally(self, contacts: int, accounts: int, saved: int) -> None:
        with self._lock:
            self._tally = {
                "contacts": max(0, int(contacts)),
                "accounts": max(0, int(accounts)),
                "saved": max(0, int(saved)),
            }

    def tally(self) -> dict[str, int] | None:
        with self._lock:
            if self._tally is None:
                return None
            return dict(self._tally)

    def stage_people(self, people: list[dict[str, str]]) -> None:
        with self._lock:
            self._staged = [dict(row) for row in people]
            pending = self._pending
            self._pending = []
            tally = dict(self._tally) if self._tally is not None else None
        try:
            self._client.checkpoint(self._job_id, self._worker_id, pending, people, tally)
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


_READ_STALL_SEC = 45


def _declared_total(headers: object, have: int, code: int) -> int | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    ranged = str(getter("Content-Range") or "")
    if "/" in ranged:
        total = ranged.rsplit("/", 1)[-1].strip()
        if total.isdigit():
            return int(total)
    length = str(getter("Content-Length") or "")
    if not length.isdigit():
        return None
    if code == 206:
        return have + int(length)
    return int(length)


def _hold_while_downloading(client: HubClient, job_id: str, worker_id: str, stop: threading.Event) -> None:
    while not stop.wait(8):
        try:
            client.progress(job_id, worker_id, 8, "PC phụ đang tải video", [])
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return


def _read_one(client: HubClient, worker_id: str, job_id: str, resume: dict[str, object] | None = None) -> None:
    ready = (resume or {}).get("people")
    if isinstance(ready, list):
        client.complete(job_id, worker_id, [row for row in ready if isinstance(row, dict)])
        return
    with tempfile.TemporaryDirectory(prefix="fb-pc-") as folder:
        dest = Path(folder) / "clip.mp4"
        client.progress(job_id, worker_id, 8, "PC phụ đang tải video", [])
        holding = threading.Event()
        holder = threading.Thread(
            target=_hold_while_downloading,
            args=(client, job_id, worker_id, holding),
            daemon=True,
        )
        holder.start()
        try:
            client.download(job_id, worker_id, dest)
        except OSError:
            holding.set()
            client.fail(job_id, worker_id, "Mất kết nối khi đang tải video. Bấm Tiếp tục để đọc nối.")
            return
        finally:
            holding.set()
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
        client.complete(job_id, worker_id, people, sink.tally())


_state_lock = threading.Lock()
_reading = False


def write_worker_state(reading: bool, path: Path | None = None) -> None:
    """Ghi đang đọc hay đang rảnh để bộ cập nhật biết có được đổi mã hay không."""
    target = path
    if target is None:
        raw = os.environ.get("FB_VIDEO_STATE", "").strip()
        if not raw:
            return
        target = Path(raw)
    payload = json.dumps({"reading": bool(reading), "pid": os.getpid(), "at": time.time()})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
    except OSError:
        return


def set_reading(reading: bool) -> None:
    global _reading
    with _state_lock:
        _reading = reading
    write_worker_state(reading)


def refresh_worker_state() -> None:
    with _state_lock:
        reading = _reading
    write_worker_state(reading)


def _prepare_gpu() -> tuple[bool, str]:
    """Bật GPU chỉ khi card NVIDIA có thật và bộ đọc nạp được. Lỗi thì đọc bằng CPU."""
    try:
        card = nvidia_name()
    except Exception:
        say("Máy này đọc bằng CPU.")
        return False, ""
    if not card:
        say("Máy này đọc bằng CPU.")
        return False, ""
    os.environ["CONTROL_OCR_ENGINE"] = "gpu"
    try:
        ready = reader_ready()
    except Exception:
        ready = False
    if ready:
        say(f"Đọc bằng GPU {card}.")
        return True, card
    os.environ.pop("CONTROL_OCR_ENGINE", None)
    say(fallback_note())
    return False, ""


def main() -> None:
    os.environ.setdefault("CONTROL_OCR_RESERVE", "0")
    parser = argparse.ArgumentParser(description="PC phụ đọc video màn hình cho hub")
    parser.add_argument("--hub", default=os.environ.get("CONTROL_HUB", "http://222.255.214.202:8088"))
    args = parser.parse_args()
    token = os.environ.get("CONTROL_TOKEN", "")
    if not token:
        say("Đặt CONTROL_TOKEN rồi chạy lại.", err=True)
        sys.exit(2)
    use_gpu, gpu_name = _prepare_gpu()
    cpus = os.cpu_count() or 1
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "PC"
    client = HubClient(args.hub, token)
    state = {"worker_id": "", "stop": threading.Event()}

    def beat() -> None:
        while not state["stop"].wait(5):
            refresh_worker_state()
            try:
                state["worker_id"] = client.heartbeat(state["worker_id"], name, cpus, use_gpu, gpu_name)
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                pass

    while True:
        try:
            state["worker_id"] = client.heartbeat("", name, cpus, use_gpu, gpu_name)
            break
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            say("Chưa nối được hub. Thử lại.", err=True)
            time.sleep(5)
    say(f"Đã nối hub. Máy này có {cpus} lõi, dùng hết để đọc video.")
    refresh_worker_state()
    threading.Thread(target=beat, daemon=True).start()
    while True:
        try:
            job_id, resume = client.claim(state["worker_id"])
            if not job_id:
                time.sleep(1.0)
                continue
            say(f"Nhận video {job_id}.")
            set_reading(True)
            try:
                _read_one(client, state["worker_id"], job_id, resume)
            finally:
                set_reading(False)
            say(f"Xong video {job_id}.")
        except KeyboardInterrupt:
            state["stop"].set()
            raise
        except urllib.error.HTTPError as error:
            say(f"Hub trả {error.code}. Thử video sau.", err=True)
            time.sleep(1)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            say("Mất kết nối hub, thử lại.", err=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
