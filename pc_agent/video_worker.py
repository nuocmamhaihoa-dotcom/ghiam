#!/usr/bin/env python3
"""PC kéo video từ hub, đọc bằng 80% CPU và RAM của máy này, rồi gửi kết quả về.

Mỗi máy nhận tối đa hai video. Một video dùng hết 80%. Hai video thì chia đôi phần đó.
Máy có card NVIDIA và đã cài bộ đọc GPU thì đọc bằng GPU.
Chưa cài thì đọc bằng CPU (Tesseract), cùng cách với hub.

Trên Windows, một lệnh cài Python, ffmpeg, Tesseract vie+eng, lưu token, và chạy khi đăng nhập.
Lệnh nằm ở đầu pc_agent/windows/Install-VideoWorker.ps1. Bản mới tự tải khi máy không đang đọc video.
Khi mở, PC tải bộ chữ nhanh giống máy chủ và đọc thử một ảnh mẫu, rồi báo kết quả lên hub.
Hub có bản mới mà máy đang rảnh thì PC thoát với mã 3 để FbPoller.bat cài bản mới rồi mở lại.

Chạy tay: đặt CONTROL_TOKEN bằng token của hub, rồi

    python pc_agent/video_worker.py

Hub mặc định là http://222.255.214.202:8088. Đổi bằng --hub hoặc CONTROL_HUB.
Card NVIDIA mà chưa có thư viện: pip install easyocr
hoặc pip install paddlepaddle-gpu paddleocr
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# libgomp chỉ đọc giới hạn này lúc được nạp, và PIL có thể nạp nó trước Tesseract.
# Thiếu giới hạn thì mỗi bộ đọc mở thêm luồng, nhiều bộ đọc cùng lúc giành CPU của nhau.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

from PIL import Image, ImageDraw, ImageFont

from control_plane import stage_timing
from control_plane.gpu_read import fallback_note, nvidia_name, reader_ready
from control_plane.read_vote import rapid_ready
from control_plane.screen_people import lines_from_tsv, prepare_tesseract, read_frame_tsv
from control_plane.screen_steps import ReadProgress, ScreenVideoError, analyze_screen_video
from control_plane.tesseract_keep import reader_mode, set_reader_limit, warm_readers
from control_plane.version import VIDEO_WORKER_BUILD
from pc_agent import pc_hardware, pc_power

_OCR_BYTES = 256 * 1024 * 1024
_SHARE_PERCENT = 80
_READ_STALL_SEC = 45
_LANE_BYTES = 8 * 1024 * 1024
_LANES = 4
_PARALLEL_MIN = 8 * 1024 * 1024
_PREFIX_BYTES = 8 * 1024 * 1024
_WAVE_BYTES = 32 * 1024 * 1024
_JOBS_PER_PC = 2
_FAST_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{name}.traineddata"
# Bộ chữ nhanh nhỏ hơn nhiều bộ chữ chuẩn (vie chuẩn 7,8 MB, eng chuẩn 23 MB). Ngoài khoảng này là tải nhầm.
_FAST_BOUNDS = {"eng": (1_000_000, 12_000_000), "vie": (200_000, 3_000_000)}
# Thoát bằng mã này thì FbPoller.bat chạy lại trình cài, tải bản mới, rồi mở lại PC phụ.
_UPDATE_EXIT = 3
_UPDATE_WAIT_SEC = 1800.0
_RETEST_SEC = 300.0
_BEAT_SEC = 3.0
_BEAT_TIMEOUT = 8.0
_CLAIM_TIMEOUT = 8.0
_TEST_WORDS = ("kiem", "tra", "doc", "chu")
_reader_note = ""
# Máy quét Facebook thường có proxy hệ thống. Proxy chết thì hub bị ngắt dù mạng thẳng vẫn thông.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _keep_socket(sock: socket.socket) -> None:
    """Giữ TCP qua NAT. Nghỉ vài giây không bị tường lửa cắt."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    if os.name == "nt" and hasattr(socket, "SIO_KEEPALIVE_VALS"):
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15_000, 5_000))
        except OSError:
            pass
        return
    for name, value in (("TCP_KEEPIDLE", 15), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3)):
        option = getattr(socket, name, None)
        if option is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            pass


def _open_direct(request: urllib.request.Request, timeout: float) -> object:
    """Mở hub thẳng, không đi proxy của Windows."""
    return _DIRECT.open(request, timeout=timeout)


class _DirectLane:
    """Một kết nối TCP tới hub. Nhịp sống và việc đọc video đi hai đường, không chờ nhau."""

    def __init__(self, hub: str, token: str) -> None:
        parsed = urllib.parse.urlsplit(hub)
        self._scheme = parsed.scheme or "http"
        self._host = parsed.hostname or ""
        self._port = parsed.port or (443 if self._scheme == "https" else 80)
        self._token = token
        self._lock = threading.Lock()
        self._conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.close()
        except OSError:
            return

    def _open(self) -> http.client.HTTPConnection:
        if not self._host:
            raise OSError("Thiếu địa chỉ hub.")
        if self._scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(self._host, self._port, timeout=10)
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=10)
        conn.connect()
        sock = conn.sock
        if sock is not None:
            _keep_socket(sock)
        self._conn = conn
        return conn

    def call(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout: float = 60,
    ) -> dict[str, object]:
        data = b""
        headers = {"Authorization": f"Bearer {self._token}", "Connection": "keep-alive"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(data))
        last: BaseException | None = None
        for _attempt in range(2):
            with self._lock:
                try:
                    conn = self._conn if self._conn is not None else self._open()
                    conn.timeout = timeout
                    sock = conn.sock
                    if sock is not None:
                        sock.settimeout(timeout)
                    conn.request(method, path, body=data, headers=headers)
                    response = conn.getresponse()
                    raw = response.read()
                    status = int(response.status)
                    reason = response.reason or ""
                    response_headers = response.headers
                    if status >= 500 or response_headers.get("Connection", "").lower() == "close":
                        self.close()
                except (OSError, TimeoutError, http.client.HTTPException) as error:
                    self.close()
                    last = error
                    continue
            if status >= 500:
                last = urllib.error.HTTPError(path, status, reason, response_headers, io.BytesIO(raw))
                continue
            if status >= 400:
                raise urllib.error.HTTPError(path, status, reason, response_headers, io.BytesIO(raw))
            if not raw:
                return {}
            try:
                loaded = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as error:
                raise json.JSONDecodeError(error.msg, error.doc, error.pos) from error
            if not isinstance(loaded, dict):
                return {}
            return loaded
        if isinstance(last, urllib.error.HTTPError):
            raise last
        if isinstance(last, (OSError, TimeoutError)):
            raise last
        raise OSError("Mất kết nối hub.") from last


def worker_budget(cpu_count: int, ram_bytes: int | None) -> tuple[int, int]:
    """Số bộ đọc và số lõi để dành. Lấy mức nhỏ hơn giữa 80% lõi và 80% RAM."""
    cpus = max(1, int(cpu_count or 1))
    by_cpu = max(1, (cpus * _SHARE_PERCENT) // 100)
    if ram_bytes is None or ram_bytes <= 0:
        workers = by_cpu
    else:
        by_ram = max(1, (int(ram_bytes) * _SHARE_PERCENT) // 100 // _OCR_BYTES)
        workers = max(1, min(by_cpu, by_ram))
    workers = min(workers, cpus)
    return workers, cpus - workers


def idle_budget(cpu_count: int, ram_bytes: int | None) -> int:
    """Số bộ đọc khi máy rảnh: mọi lõi trừ một lõi cho nhịp nối hub, và không vượt 80% RAM."""
    cpus = max(1, int(cpu_count or 1))
    workers = max(1, cpus - 1)
    if ram_bytes is not None and ram_bytes > 0:
        by_ram = max(1, (int(ram_bytes) * _SHARE_PERCENT) // 100 // _OCR_BYTES)
        workers = min(workers, by_ram)
    return max(1, min(workers, cpus))


class _JobSlots:
    """Đếm video đang giữ trên máy này và chia số lõi khi có hai video."""

    def __init__(self, workers: int) -> None:
        self.workers = max(1, workers)
        self.held = 0
        self._lock = threading.Lock()

    def set_workers(self, workers: int) -> None:
        with self._lock:
            self.workers = max(1, int(workers))

    def take(self) -> bool:
        with self._lock:
            if self.held >= _JOBS_PER_PC:
                return False
            self.held += 1
            return True

    def give(self) -> None:
        with self._lock:
            self.held = max(0, self.held - 1)

    def share(self) -> int:
        with self._lock:
            running = self.held
        if running >= 2:
            return max(1, self.workers // 2)
        return self.workers

    def busy(self) -> int:
        with self._lock:
            return self.held


def machine_ram_bytes() -> int:
    """RAM vật lý. 0 khi không đọc được, lúc đó chỉ giới hạn theo số lõi."""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
        if kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return 0
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return 0
    if not isinstance(pages, int) or not isinstance(size, int) or pages <= 0 or size <= 0:
        return 0
    return pages * size


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


def hardware_line(found: dict[str, object]) -> str:
    """Một câu về cấu hình máy cho cửa sổ PC. Rỗng khi không đọc được gì."""
    parts: list[str] = []
    cpu = str(found.get("cpu") or "")
    if cpu:
        parts.append(cpu)
    physical = found.get("physical")
    logical = found.get("logical")
    if isinstance(physical, int) and physical > 0 and isinstance(logical, int) and logical > 0:
        parts.append(f"{physical} lõi vật lý, {logical} luồng")
    gpus = found.get("gpus")
    if isinstance(gpus, list) and gpus:
        parts.append("card đồ họa " + ", ".join(str(item) for item in gpus))
    free = found.get("tempFreeMb")
    if isinstance(free, int) and free > 0:
        parts.append(f"ổ tạm trống {free // 1024} GB")
    return "Cấu hình: " + "; ".join(parts) + "." if parts else ""


class HubClient:
    def __init__(self, hub: str, token: str) -> None:
        self.hub = hub.rstrip("/")
        self.token = token
        # Nhịp sống không chờ lần tải video hay lần gửi kết quả.
        self._beat = _DirectLane(self.hub, self.token)
        self._work = _DirectLane(self.hub, self.token)

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None, timeout: float | None = 60) -> dict[str, object]:
        limit = 60.0 if timeout is None else float(timeout)
        return self._work.call(method, path, payload, limit)

    def heartbeat(
        self,
        worker_id: str,
        name: str,
        cpus: int,
        gpu: bool = False,
        gpu_name: str = "",
        workers: int = 0,
        report: dict[str, object] | None = None,
    ) -> tuple[str, int]:
        """Trả về mã PC và bản mới nhất hub đang phát."""
        payload: dict[str, object] = {
            "workerId": worker_id,
            "name": name,
            "cpus": cpus,
            "gpu": gpu,
            "gpuName": gpu_name,
            "workers": workers,
        }
        if report:
            payload.update(report)
        body = self._beat.call("POST", "/v1/video-workers/heartbeat", payload, timeout=_BEAT_TIMEOUT)
        found = body.get("workerId")
        latest = body.get("build")
        latest_build = latest if isinstance(latest, int) and not isinstance(latest, bool) else 0
        return (found if isinstance(found, str) and found else worker_id), latest_build

    def fetch_model(self, name: str) -> bytes:
        request = urllib.request.Request(
            f"{self.hub}/v1/updates/tessdata/{name}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with _open_direct(request, 60) as response:
            return response.read(_FAST_BOUNDS["eng"][1] + 1)  # type: ignore[attr-defined]

    def claim(self, worker_id: str) -> tuple[str, dict[str, object]]:
        body = self._request("POST", "/v1/recordings/jobs/claim", {"workerId": worker_id}, timeout=_CLAIM_TIMEOUT)
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
        """Tải video nhiều kết nối. Một đường đứt thì các đường kia vẫn chạy, rồi nối từ byte đã ghi."""
        url = f"{self.hub}/v1/recordings/jobs/{job_id}/video?workerId={worker_id}"
        last_error: Exception | None = None
        for attempt in range(8):
            have = dest.stat().st_size if dest.is_file() else 0
            headers = {"Authorization": f"Bearer {self.token}"}
            if have:
                headers["Range"] = f"bytes={have}-"
            request = urllib.request.Request(url, headers=headers, method="GET")
            copied_end: int | None = None
            total_size = 0
            try:
                with _open_direct(request, _READ_STALL_SEC) as response:
                    code = int(getattr(response, "status", 200) or 200)
                    if have and code == 200:
                        have = 0
                    total = _declared_total(response.headers, have, code)
                    remaining = None if total is None else total - have
                    if total is None or remaining is None or remaining < _PARALLEL_MIN:
                        if _read_body(response, dest, have, code, total):
                            return
                    else:
                        first_end = min(total, have + _LANE_BYTES)
                        if _copy_limited(response, dest, have, code, first_end - have):
                            copied_end = first_end
                            total_size = total
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code == 416 and have:
                    return
                if error.code in {401, 404, 409}:
                    raise
            except (OSError, urllib.error.URLError, TimeoutError, socket.timeout, http.client.IncompleteRead) as error:
                last_error = error
            if copied_end is not None:
                try:
                    if _parallel_from(url, self.token, dest, copied_end, total_size):
                        return
                except (OSError, urllib.error.URLError, TimeoutError, socket.timeout, http.client.IncompleteRead) as error:
                    last_error = error
            time.sleep(min(8.0, 0.4 * (2**attempt)))
        if last_error is not None:
            raise OSError("Chưa tải hết video.") from last_error
        raise OSError("Chưa tải hết video.")

    def progress(
        self, job_id: str, worker_id: str, percent: int, task: str, problems: list[str], learned: str = ""
    ) -> None:
        body: dict[str, object] = {"workerId": worker_id, "percent": percent, "task": task, "problems": problems}
        if learned:
            body["learned"] = learned
        self._request("POST", f"/v1/recordings/jobs/{job_id}/progress", body, timeout=60)

    def samples(self, job_id: str, worker_id: str, images: list[bytes]) -> None:
        payload = {
            "workerId": worker_id,
            "images": [base64.b64encode(item).decode("ascii") for item in images[:3]],
        }
        self._request(
            "POST",
            f"/v1/recordings/jobs/{job_id}/samples",
            payload,
            timeout=60,
        )

    def complete(
        self,
        job_id: str,
        worker_id: str,
        people: list[dict[str, str]],
        tally: dict[str, int] | None = None,
        reading: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {"workerId": worker_id, "people": people}
        if tally is not None:
            payload["seenContacts"] = tally["contacts"]
            payload["seenAccounts"] = tally["accounts"]
            payload["readSaved"] = tally["saved"]
        if reading is not None:
            payload["noText"] = bool(reading.get("noText"))
            payload["wordSeen"] = int(reading.get("wordSeen") or 0)
            payload["wordKept"] = int(reading.get("wordKept") or 0)
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
        self._learned = ""
        self._known: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
        self._pending: list[dict[str, object]] = []
        self._staged: list[dict[str, str]] | None = None
        self._tally: dict[str, int] | None = None
        self._seen = 0
        self._kept = 0
        self._blank = False
        self._samples_sent = False
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

    def note_learned(self, text: str) -> None:
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return
        with self._lock:
            self._learned = cleaned
        say(cleaned)

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

    def note_words(self, seen: int, kept: int) -> None:
        with self._lock:
            added_seen = max(0, int(seen))
            added_kept = max(0, int(kept))
            self._seen += added_seen
            self._kept += added_kept
            if added_seen or added_kept:
                self._blank = False

    def note_blank(self) -> None:
        with self._lock:
            self._blank = True

    def note_samples(self, images: list[bytes]) -> None:
        if not images:
            return
        with self._lock:
            if self._samples_sent:
                return
            self._samples_sent = True
        try:
            self._client.samples(self._job_id, self._worker_id, images)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            with self._lock:
                self._samples_sent = False

    def reading(self) -> dict[str, object]:
        with self._lock:
            return {"noText": self._blank, "wordSeen": self._seen, "wordKept": self._kept}

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
        with self._lock:
            learned, self._learned = self._learned, ""
        try:
            if frames:
                self._client.checkpoint(self._job_id, self._worker_id, frames)
            self._client.progress(self._job_id, self._worker_id, percent, task, problems, learned)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if learned:
                with self._lock:
                    self._learned = self._learned or learned
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


def _write_stream(response: object, handle: object, limit: int | None) -> int:
    """Đọc thân HTTP. limit là số byte cần lấy; None là đọc đến khi hết."""
    written = 0
    while limit is None or written < limit:
        want = 4 * 1024 * 1024 if limit is None else min(4 * 1024 * 1024, limit - written)
        try:
            chunk = response.read(want)  # type: ignore[attr-defined]
        except http.client.IncompleteRead as error:
            if error.partial:
                take = error.partial if limit is None else error.partial[: limit - written]
                handle.write(take)  # type: ignore[attr-defined]
                written += len(take)
            break
        if not chunk:
            break
        handle.write(chunk)  # type: ignore[attr-defined]
        written += len(chunk)
    return written


def _read_body(response: object, dest: Path, have: int, code: int, total: int | None) -> bool:
    mode = "ab" if have and code == 206 else "wb"
    written = have if mode == "ab" else 0
    with dest.open(mode) as handle:
        written += _write_stream(response, handle, None)
    return total is None or written >= total


def _copy_limited(response: object, dest: Path, have: int, code: int, count: int) -> bool:
    if count <= 0:
        return True
    mode = "ab" if have and code == 206 else "wb"
    with dest.open(mode) as handle:
        written = _write_stream(response, handle, count)
    return written >= count


class _Memory:
    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def write(self, chunk: bytes) -> None:
        self.parts.append(chunk)

    def join(self) -> bytes:
        return b"".join(self.parts)


def _fetch_range(url: str, token: str, start: int, end: int) -> bytes:
    """Tải đúng khoảng [start, end)."""
    if end <= start:
        return b""
    headers = {
        "Authorization": f"Bearer {token}",
        "Range": f"bytes={start}-{end - 1}",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    want = end - start
    sink = _Memory()
    with _open_direct(request, _READ_STALL_SEC) as response:
        code = int(getattr(response, "status", 200) or 200)
        if code != 206:
            raise OSError("Máy chủ không trả khúc video.")
        got = _write_stream(response, sink, want)
    blob = sink.join()
    if got != want or len(blob) != want:
        raise OSError("Khúc video tải thiếu.")
    return blob


def _parallel_from(url: str, token: str, dest: Path, frontier: int, total: int) -> bool:
    """Bốn đường cùng lúc. Khúc nào xong thì giữ, khúc đứt thì lần sau tải tiếp từ byte đã ghi."""
    while frontier < total:
        pieces: list[tuple[int, int]] = []
        for index in range(_LANES):
            start = frontier + index * _LANE_BYTES
            if start >= total:
                break
            pieces.append((start, min(total, start + _LANE_BYTES)))
        blobs: list[bytes | None] = [None] * len(pieces)
        errors: list[BaseException] = []
        lock = threading.Lock()

        def grab(index: int, start: int, end: int) -> None:
            try:
                blob = _fetch_range(url, token, start, end)
            except (OSError, urllib.error.URLError, TimeoutError, socket.timeout, http.client.IncompleteRead) as error:
                with lock:
                    errors.append(error)
                return
            blobs[index] = blob

        threads = [
            threading.Thread(target=grab, args=(index, start, end), daemon=True)
            for index, (start, end) in enumerate(pieces)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with dest.open("ab") as handle:
            for blob in blobs:
                if blob is None:
                    return False
                handle.write(blob)
        if errors:
            return False
        frontier = pieces[-1][1]
    return frontier >= total


def _hold_while_downloading(client: HubClient, job_id: str, worker_id: str, stop: threading.Event) -> None:
    while not stop.wait(8):
        try:
            client.progress(job_id, worker_id, 8, "PC phụ đang tải video", [])
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return


def _guarded_read(
    client: HubClient,
    worker_id: str,
    job_id: str,
    resume: dict[str, object] | None = None,
    slots: _JobSlots | None = None,
) -> None:
    """Lỗi bất ngờ thì báo hub đọc tiếp. Mất cả đường báo thì hub vẫn nhận lại video khi hết hạn giữ."""
    try:
        _read_one(client, worker_id, job_id, resume, slots)
    except Exception:
        try:
            client.fail(job_id, worker_id, "PC gặp lỗi khi đọc. Máy chủ sẽ đọc tiếp.")
        except Exception:
            return


def _read_one(
    client: HubClient,
    worker_id: str,
    job_id: str,
    resume: dict[str, object] | None = None,
    slots: _JobSlots | None = None,
) -> None:
    ready = (resume or {}).get("people")
    if isinstance(ready, list):
        client.complete(job_id, worker_id, [row for row in ready if isinstance(row, dict)])
        return
    if _reader_note:
        try:
            client.progress(job_id, worker_id, 8, "PC phụ đang đọc video", [_reader_note])
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
    with tempfile.TemporaryDirectory(prefix="fb-pc-") as folder:
        dest = Path(folder) / "clip.mp4"
        failed: list[str] = []
        finished = threading.Event()

        def pull() -> None:
            try:
                client.download(job_id, worker_id, dest)
            except OSError:
                failed.append("Mất kết nối khi đang tải video. Bấm Tiếp tục để đọc nối.")
            finally:
                finished.set()

        client.progress(job_id, worker_id, 8, "PC phụ đang tải video", [])
        holding = threading.Event()
        holder = threading.Thread(
            target=_hold_while_downloading,
            args=(client, job_id, worker_id, holding),
            daemon=True,
        )
        holder.start()
        threading.Thread(target=pull, daemon=True).start()
        sink: RemoteProgress | None = None
        try:
            seen = 0
            while True:
                size = dest.stat().st_size if dest.is_file() else 0
                done = finished.is_set()
                if done and failed:
                    break
                if not done and size < _PREFIX_BYTES:
                    finished.wait(0.25)
                    continue
                if not done and seen and size < seen + _WAVE_BYTES:
                    finished.wait(0.4)
                    continue
                if size < 32:
                    if done:
                        break
                    finished.wait(0.25)
                    continue
                # Đọc thẳng file đang tải. Mỗi đợt chỉ tách phần mới, khung cuối chưa chắc thì đợt sau tách lại.
                last = done and not failed
                if sink is None:
                    holding.set()
                    sink = RemoteProgress(client, job_id, worker_id, resume)
                cpus = os.cpu_count() or 1
                share = slots.share() if slots is not None else cpus
                try:
                    _steps, people = analyze_screen_video(
                        dest,
                        sink,
                        threads=str(max(1, share)),
                        reserve=max(0, cpus - max(1, share)),
                        keep_open=not last,
                    )
                except ScreenVideoError as error:
                    if not last:
                        seen = size
                        continue
                    sink.close()
                    client.fail(job_id, worker_id, str(error))
                    return
                seen = size
                if last:
                    sink.close()
                    client.complete(job_id, worker_id, people, sink.tally(), sink.reading())
                    return
            if sink is not None:
                sink.close()
            client.fail(
                job_id,
                worker_id,
                failed[0] if failed else "Không đọc được video.",
            )
        finally:
            holding.set()
            if sink is not None and not sink._stop.is_set():
                sink.close()


_state_lock = threading.Lock()
_reading = False
_readers = 0


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
    global _reading, _readers
    with _state_lock:
        if reading:
            _readers += 1
        else:
            _readers = max(0, _readers - 1)
        _reading = _readers > 0
    write_worker_state(_reading)


def refresh_worker_state() -> None:
    with _state_lock:
        reading = _reading
    write_worker_state(reading)


def _fast_ok(path: Path, name: str) -> bool:
    low, high = _FAST_BOUNDS[name]
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return low <= size <= high


def _fetch_url(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as response:
        return response.read(_FAST_BOUNDS["eng"][1] + 1)


def ensure_fast_models(fetch_hub: Callable[[str], bytes], folder: Path, fetch_url: Callable[[str], bytes] = _fetch_url) -> bool:
    """Bộ chữ nhanh vie+eng như máy chủ. Tải từ hub, hub không có thì tải GitHub. Thiếu thì giữ bộ chữ cũ."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    for name in ("eng", "vie"):
        dest = folder / f"{name}.traineddata"
        if _fast_ok(dest, name):
            continue
        low, high = _FAST_BOUNDS[name]
        for source in ("hub", "github"):
            try:
                blob = fetch_hub(name) if source == "hub" else fetch_url(_FAST_URL.format(name=name))
            except (OSError, urllib.error.URLError, TimeoutError, socket.timeout, http.client.HTTPException):
                continue
            if not low <= len(blob) <= high:
                continue
            part = dest.with_name(dest.name + ".part")
            try:
                part.write_bytes(blob)
                part.replace(dest)
            except OSError:
                continue
            break
        if not _fast_ok(dest, name):
            return False
    return True


def _test_font() -> ImageFont.FreeTypeFont | None:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for path in (
        windows / "arialbd.ttf",
        windows / "arial.ttf",
        windows / "segoeui.ttf",
        windows / "tahoma.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), 44)
        except OSError:
            continue
    return None


def reader_self_test() -> dict[str, object]:
    """Đọc một ảnh mẫu bằng đúng cách đọc video. readerOk None khi máy không có phông để vẽ ảnh mẫu."""
    font = _test_font()
    if font is None:
        return {"readerOk": None, "readerNote": "", "readerMs": 0}
    image = Image.new("RGB", (720, 1400), (255, 255, 255))
    pen = ImageDraw.Draw(image)
    pen.text((40, 420), "Kiem Tra Doc Chu", font=font, fill=(0, 0, 0))
    pen.text((40, 500), "@kiem.tra2026", font=font, fill=(0, 0, 0))
    with tempfile.TemporaryDirectory(prefix="fb-pc-test-") as folder:
        path = Path(folder) / "f-00001.jpg"
        image.save(path, format="JPEG", quality=92)
        start = time.perf_counter()
        try:
            tsv = read_frame_tsv(path)
        except Exception:
            tsv = ""
        took = int((time.perf_counter() - start) * 1000)
    words = {
        word.strip("@.,").casefold()
        for line in lines_from_tsv(tsv)
        for word in line.text.split()
    }
    if any(word in words for word in _TEST_WORDS):
        return {"readerOk": True, "readerNote": "", "readerMs": took}
    if not tsv:
        return {"readerOk": False, "readerNote": "PC chưa chạy được Tesseract.", "readerMs": took}
    return {"readerOk": False, "readerNote": "PC đọc ảnh mẫu không ra chữ.", "readerMs": took}


def read_update_mark(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_update_mark(path: Path, build: int) -> bool:
    try:
        path.write_text(json.dumps({"build": int(build), "at": time.time()}), encoding="utf-8")
    except OSError:
        return False
    return True


def update_due(own: int, latest: int, held: int, tried: dict[str, object], now: float) -> bool:
    """Hub có bản mới và máy không giữ video nào. Vừa thử lên đúng bản đó trong 30 phút thì chờ."""
    if latest <= own or held > 0:
        return False
    at = tried.get("at")
    if tried.get("build") == latest and isinstance(at, (int, float)) and now - float(at) < _UPDATE_WAIT_SEC:
        return False
    return True


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
    global _reader_note
    cpus = os.cpu_count() or 1
    ram = machine_ram_bytes()
    pc_power.lower_priority()
    pc_power.keep_full_speed()
    workers, reserve = worker_budget(cpus, ram)
    budget = pc_power.PowerBudget(workers, idle_budget(cpus, ram))
    os.environ["CONTROL_OCR_RESERVE"] = str(reserve)
    os.environ["CONTROL_FFMPEG_THREADS"] = str(workers)
    set_reader_limit(workers)
    parser = argparse.ArgumentParser(description="PC phụ đọc video màn hình cho hub")
    parser.add_argument("--hub", default=os.environ.get("CONTROL_HUB", "http://222.255.214.202:8088"))
    args = parser.parse_args()
    token = os.environ.get("CONTROL_TOKEN", "")
    if not token:
        say("Đặt CONTROL_TOKEN rồi chạy lại.", err=True)
        sys.exit(2)
    client = HubClient(args.hub, token)
    _reader_note = prepare_tesseract([ROOT, ROOT.parent])
    standard_prefix = os.environ.get("TESSDATA_PREFIX", "").strip()
    if standard_prefix:
        os.environ["CONTROL_TESSDATA_STANDARD"] = standard_prefix.rstrip("\\/")
    fast_folder = ROOT.parent / "tessdata-fast"
    models = "standard"
    if ensure_fast_models(client.fetch_model, fast_folder):
        os.environ["TESSDATA_PREFIX"] = str(fast_folder) + os.sep
        models = "fast"
    use_gpu, gpu_name = _prepare_gpu()
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "PC"
    # hardware và timing có sẵn khóa từ đầu: gán lại giá trị không làm đổi kích thước dict đang được chép lúc gửi nhịp.
    report: dict[str, object] = {"build": VIDEO_WORKER_BUILD, "models": models, "hardware": {}, "timing": {}}
    report.update(reader_self_test())
    report["readerMode"] = reader_mode()
    threading.Thread(target=warm_readers, daemon=True).start()

    def gather_hardware() -> None:
        try:
            found = pc_hardware.collect(cpus, ram)
        except Exception:
            return
        report["hardware"] = found
        text = hardware_line(found)
        if text:
            say(text)

    threading.Thread(target=gather_hardware, daemon=True).start()
    slots = _JobSlots(workers)
    mark = ROOT.parent / "update-tried.json"
    state: dict[str, object] = {"worker_id": "", "latest": 0}
    stop = threading.Event()
    updating = threading.Event()

    link_down = False

    def tune_cores() -> None:
        """Máy rảnh đủ lâu thì dùng thêm lõi. Bạn quay lại dùng máy thì về mức 80% ngay."""
        if not budget.update(pc_power.idle_seconds()):
            return
        os.environ["CONTROL_OCR_RESERVE"] = str(cpus - budget.workers)
        os.environ["CONTROL_FFMPEG_THREADS"] = str(budget.workers)
        set_reader_limit(budget.workers)
        slots.set_workers(budget.workers)
        if budget.is_resting:
            threading.Thread(target=warm_readers, daemon=True).start()
            say(f"Máy rảnh. Đọc video bằng {budget.workers} lõi.")
        else:
            say(f"Bạn đang dùng máy. Đọc video bằng {budget.workers} lõi.")

    def note_timing() -> None:
        """Hết video cuối cùng thì in và gửi lên hub thời gian từng bước của kỳ vừa rồi."""
        if slots.busy() > 0:
            return
        data = stage_timing.take()
        text = stage_timing.describe(data)
        if not text:
            return
        report["timing"] = stage_timing.summary(data)
        say(text)

    def pulse() -> bool:
        try:
            worker_id, latest = client.heartbeat(
                str(state["worker_id"]), name, cpus, use_gpu, gpu_name, budget.workers, dict(report)
            )
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return False
        state["worker_id"] = worker_id
        state["latest"] = latest
        if update_due(VIDEO_WORKER_BUILD, latest, slots.busy(), read_update_mark(mark), time.time()):
            updating.set()
        return True

    def note_down(text: str) -> None:
        nonlocal link_down
        if link_down:
            return
        say(text, err=True)
        link_down = True

    def note_up() -> None:
        nonlocal link_down
        if not link_down:
            return
        say("Đã nối lại hub.")
        link_down = False

    def beat() -> None:
        pc_power.raise_this_thread()
        tested = time.monotonic()
        while not stop.wait(_BEAT_SEC):
            refresh_worker_state()
            tune_cores()
            if report.get("readerOk") is False and time.monotonic() - tested >= _RETEST_SEC:
                report.update(reader_self_test())
                report["readerMode"] = reader_mode()
                tested = time.monotonic()
            pulse()

    while True:
        try:
            state["worker_id"], state["latest"] = client.heartbeat(
                "", name, cpus, use_gpu, gpu_name, budget.workers, dict(report)
            )
            break
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            say("Chưa nối được hub. Thử lại.", err=True)
            time.sleep(5)
    if _reader_note:
        say(_reader_note, err=True)
    resting = f" và {budget.resting} lõi khi máy rảnh {int(pc_power.IDLE_AFTER_SEC // 60)} phút" if budget.resting > workers else ""
    if ram > 0:
        say(
            f"Đã nối hub. Máy này có {cpus} lõi, {ram // (1024 * 1024)} MB RAM, dùng {workers} lõi (80%) khi bạn đang dùng máy{resting} để đọc video. Tối đa hai video cùng lúc."
        )
    else:
        say(f"Đã nối hub. Máy này có {cpus} lõi, dùng {workers} lõi (80%) khi bạn đang dùng máy{resting} để đọc video. Tối đa hai video cùng lúc.")
    say(f"Bản {VIDEO_WORKER_BUILD}. " + ("Bộ chữ nhanh." if models == "fast" else "Bộ chữ chuẩn."))
    if report.get("readerOk") is True:
        say(f"Đọc thử ảnh mẫu được, mất {report.get('readerMs')} ms.")
    if not rapid_ready():
        say("Chưa có RapidOCR. Đối chiếu tên và @ bằng Tesseract.")
    elif report.get("readerOk") is False:
        say(f"{report.get('readerNote')} Máy chủ đọc thay cho đến khi PC đọc được.", err=True)
    refresh_worker_state()
    threading.Thread(target=beat, daemon=True).start()
    while True:
        if updating.is_set():
            if slots.busy() == 0:
                latest = int(str(state["latest"]))
                if write_update_mark(mark, latest):
                    say(f"Hub có bản {latest}. Đang tự cập nhật.")
                    stop.set()
                    sys.exit(_UPDATE_EXIT)
                updating.clear()
            time.sleep(0.5)
            continue
        claimed = False
        started = False
        try:
            if not slots.take():
                time.sleep(0.2)
                continue
            claimed = True
            job_id, resume = client.claim(str(state["worker_id"]))
            note_up()
            if not job_id:
                slots.give()
                claimed = False
                time.sleep(0.5)
                continue
            say(f"Nhận video {job_id}.")
            set_reading(True)
            started = True

            def _run(job_id: str = job_id, resume: dict[str, object] | None = resume) -> None:
                try:
                    _guarded_read(client, str(state["worker_id"]), job_id, resume, slots)
                finally:
                    slots.give()
                    set_reading(False)
                say(f"Xong video {job_id}.")
                note_timing()

            threading.Thread(target=_run, daemon=True).start()
            claimed = False
            started = False
        except KeyboardInterrupt:
            if started:
                set_reading(False)
            if claimed:
                slots.give()
            stop.set()
            raise
        except urllib.error.HTTPError as error:
            if started:
                set_reading(False)
            if claimed:
                slots.give()
            if error.code == 409:
                pulse()
            note_down(f"Hub trả {error.code}. Thử video sau.")
            time.sleep(0.5)
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if started:
                set_reading(False)
            if claimed:
                slots.give()
            note_down("Mất kết nối hub, thử lại.")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
