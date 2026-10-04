"""PC phụ kéo video từ hub. PC chỉ có CPU giữ tối đa hai video. PC có GPU giữ tối đa bốn. Hub chỉ đọc khi không còn PC đang nối."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from control_plane.settings import settings
from control_plane.version import VIDEO_WORKER_BUILD

OFFER_SECONDS = 8.0
LEASE_SECONDS = 90.0
# Nhịp PC là 3 giây. Mạng chập một lúc vẫn tính là đang nối. Im 45 giây thì hub đọc thay.
FRESH_SECONDS = 45.0
JOBS_CPU = 2
JOBS_GPU = 4


def slots_for(gpu: bool) -> int:
    """CPU giữ hai video. PC đã báo GPU thì giữ bốn video trên một card."""
    return JOBS_GPU if gpu else JOBS_CPU


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned[:40] or "PC"


def _clean_id(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_")[:64]


def _clean_gpu_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned[:80]


def _clean_note(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    return "".join(ch for ch in cleaned if ch.isprintable())[:120]


def _clean_label(value: object, limit: int) -> str:
    cleaned = " ".join(str(value or "").split())
    return "".join(ch for ch in cleaned if ch.isprintable())[:limit]


_TIMING_KEYS = (
    "frames",
    "readMs",
    "voteMs",
    "thumbMs",
    "ffmpegSec",
    "voteLines",
    "reused",
    "third",
    "rereads",
    "skipped",
    "rescued",
    "strips",
    "fades",
)


def _whole(value: object, limit: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(limit, int(value)))


def clean_timing(data: object) -> dict[str, int]:
    """Thời gian từng bước PC báo lên: chỉ các khóa biết trước, số nguyên không âm."""
    if not isinstance(data, dict):
        return {}
    found: dict[str, int] = {}
    for key in _TIMING_KEYS:
        number = _whole(data.get(key), 1_000_000_000)
        if number is not None:
            found[key] = number
    return found


def clean_hardware(data: object) -> dict[str, object]:
    """Cấu hình PC báo lên: tên CPU, lõi vật lý và luồng, RAM, card đồ họa, chỗ trống ổ tạm."""
    if not isinstance(data, dict):
        return {}
    found: dict[str, object] = {}
    cpu = _clean_label(data.get("cpu"), 80)
    if cpu:
        found["cpu"] = cpu
    for key, limit in (("physical", 512), ("logical", 512), ("ramMb", 4_194_304), ("tempFreeMb", 100_000_000)):
        number = _whole(data.get(key), limit)
        if number is not None:
            found[key] = number
    gpus = data.get("gpus")
    if isinstance(gpus, list):
        names = [_clean_label(item, 60) for item in gpus[:4]]
        found["gpus"] = [name for name in names if name]
    return found


@dataclass
class Helper:
    worker_id: str
    name: str
    cpus: int
    seen: float
    held: int = 0
    gpu: bool = False
    gpu_name: str = ""
    workers: int = 0
    build: int = 0
    models: str = ""
    # None là PC chưa báo kết quả đọc thử. False là PC đọc ảnh mẫu không ra chữ.
    reader_ok: bool | None = None
    reader_note: str = ""
    reader_mode: str = ""
    hardware: dict[str, object] = field(default_factory=dict)
    timing: dict[str, int] = field(default_factory=dict)

    def usable(self) -> bool:
        return self.reader_ok is not False


class HelperBook:
    def __init__(self, path: Path | None = None) -> None:
        self._items: dict[str, Helper] = {}
        self._lock = threading.Lock()
        self._path = path
        self._load()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
        self._write([])

    def _rows_locked(self, now_mono: float) -> list[dict[str, object]]:
        now_wall = time.time()
        rows: list[dict[str, object]] = []
        for item in self._items.values():
            age = max(0.0, now_mono - item.seen)
            if age > LEASE_SECONDS:
                continue
            rows.append(
                {
                    "workerId": item.worker_id,
                    "name": item.name,
                    "cpus": item.cpus,
                    "seenWall": now_wall - age,
                    "gpu": item.gpu,
                    "gpuName": item.gpu_name,
                    "workers": item.workers,
                    "build": item.build,
                    "models": item.models,
                    "readerOk": item.reader_ok,
                    "readerNote": item.reader_note,
                    "readerMode": item.reader_mode,
                    "hardware": item.hardware,
                    "timing": item.timing,
                }
            )
        return rows

    def _write(self, rows: list[dict[str, object]]) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            return

    def _load(self) -> None:
        """Hub khởi động lại vẫn nhận ra PC vừa gửi nhịp, không trả 409."""
        if self._path is None or not self._path.is_file():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return
        if not isinstance(loaded, list):
            return
        now_wall = time.time()
        now_mono = time.monotonic()
        with self._lock:
            for row in loaded:
                if not isinstance(row, dict):
                    continue
                seen_wall = row.get("seenWall")
                if isinstance(seen_wall, bool) or not isinstance(seen_wall, (int, float)):
                    continue
                age = max(0.0, now_wall - float(seen_wall))
                if age > FRESH_SECONDS:
                    continue
                worker_id = _clean_id(str(row.get("workerId") or ""))
                if not worker_id:
                    continue
                cpus = row.get("cpus")
                cores = min(256, max(1, int(cpus))) if isinstance(cpus, int) and not isinstance(cpus, bool) else 1
                item = Helper(worker_id, _clean_name(str(row.get("name") or "")), cores, now_mono - age)
                item.gpu = bool(row.get("gpu"))
                item.gpu_name = _clean_gpu_name(str(row.get("gpuName") or "")) if item.gpu else ""
                workers = row.get("workers")
                item.workers = (
                    min(cores, max(0, int(workers)))
                    if isinstance(workers, int) and not isinstance(workers, bool)
                    else 0
                )
                build = row.get("build")
                item.build = int(build) if isinstance(build, int) and not isinstance(build, bool) and build > 0 else 0
                models = row.get("models")
                item.models = "fast" if models == "fast" else ("standard" if models == "standard" else "")
                reader_ok = row.get("readerOk")
                item.reader_ok = reader_ok if isinstance(reader_ok, bool) else None
                item.reader_note = _clean_note(str(row.get("readerNote") or ""))
                mode = row.get("readerMode")
                item.reader_mode = "api" if mode == "api" else ("cli" if mode == "cli" else "")
                item.hardware = clean_hardware(row.get("hardware"))
                item.timing = clean_timing(row.get("timing"))
                self._items[worker_id] = item

    def beat(
        self,
        worker_id: str,
        name: str,
        cpus: int,
        gpu: bool = False,
        gpu_name: str = "",
        workers: int = 0,
        build: int = 0,
        models: str = "",
        reader_ok: bool | None = None,
        reader_note: str = "",
        reader_mode: str = "",
        hardware: object = None,
        timing: object = None,
    ) -> str:
        cleaned_id = _clean_id(worker_id) or uuid.uuid4().hex
        cores = min(256, max(1, int(cpus or 1)))
        label = _clean_name(name)
        using_gpu = bool(gpu)
        card = _clean_gpu_name(gpu_name) if using_gpu else ""
        readers = min(cores, max(0, int(workers or 0)))
        now = time.monotonic()
        with self._lock:
            current = self._items.get(cleaned_id)
            if current is None:
                current = Helper(cleaned_id, label, cores, now, 0, using_gpu, card, readers)
                self._items[cleaned_id] = current
            current.name = label
            current.cpus = cores
            current.seen = now
            current.gpu = using_gpu
            current.gpu_name = card
            current.workers = readers
            current.build = max(0, int(build or 0))
            current.models = "fast" if models == "fast" else ("standard" if models == "standard" else "")
            current.reader_ok = reader_ok
            current.reader_note = _clean_note(reader_note)
            current.reader_mode = "api" if reader_mode == "api" else ("cli" if reader_mode == "cli" else "")
            fresh_hardware = clean_hardware(hardware)
            if fresh_hardware:
                current.hardware = fresh_hardware
            fresh_timing = clean_timing(timing)
            if fresh_timing:
                current.timing = fresh_timing
            rows = self._rows_locked(now)
        self._write(rows)
        return cleaned_id

    def fresh(self, worker_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(worker_id)
            return item is not None and (now - item.seen) <= FRESH_SECONDS

    def touch(self, worker_id: str) -> None:
        """Giữ PC trong danh sách đang nối khi nó đang tải hoặc đang đọc, không chỉ khi heartbeat."""
        if not worker_id:
            return
        now = time.monotonic()
        with self._lock:
            item = self._items.get(worker_id)
            if item is None:
                return
            item.seen = now
            rows = self._rows_locked(now)
        self._write(rows)

    def note(self, worker_id: str) -> bool:
        """Lệnh vừa tới từ PC đã biết, trong hạn giữ video, thì tính là còn nối."""
        if not worker_id:
            return False
        now = time.monotonic()
        with self._lock:
            item = self._items.get(worker_id)
            if item is None or (now - item.seen) > LEASE_SECONDS:
                return False
            item.seen = now
            rows = self._rows_locked(now)
        self._write(rows)
        return True

    def has_fresh(self) -> bool:
        """Còn PC đọc được chữ vừa gửi nhịp, kể cả PC đang bận đọc video."""
        now = time.monotonic()
        with self._lock:
            return any((now - item.seen) <= FRESH_SECONDS and item.usable() for item in self._items.values())

    def idle_count(self, skip: str = "") -> int:
        """Số PC đọc được chữ, vừa nối, và còn chỗ nhận thêm video."""
        now = time.monotonic()
        with self._lock:
            return sum(
                1
                for item in self._items.values()
                if item.worker_id != skip
                and (now - item.seen) <= FRESH_SECONDS
                and item.usable()
                and item.held < slots_for(item.gpu)
            )

    def has_idle(self) -> bool:
        """Còn PC vừa nối và còn chỗ nhận thêm video."""
        return self.idle_count() > 0

    def try_hold(self, worker_id: str) -> bool:
        """Giữ thêm một video. Hết chỗ, PC không còn tươi, hoặc PC đọc thử không ra chữ, thì từ chối."""
        now = time.monotonic()
        with self._lock:
            item = self._items.get(worker_id)
            if (
                item is None
                or (now - item.seen) > FRESH_SECONDS
                or item.held >= slots_for(item.gpu)
                or not item.usable()
            ):
                return False
            item.held += 1
            item.seen = now
            rows = self._rows_locked(now)
        self._write(rows)
        return True

    def mark_busy(self, worker_id: str) -> None:
        self.try_hold(worker_id)

    def mark_idle(self, worker_id: str) -> None:
        """Trả một chỗ. Không làm mới giờ thấy, để PC đã tắt không bị chờ thêm."""
        with self._lock:
            item = self._items.get(worker_id)
            if item is not None and item.held > 0:
                item.held -= 1

    def name_for(self, worker_id: str) -> str:
        """Tên máy đang đọc. Không có trong sổ thì gọi là PC."""
        if worker_id == "hub":
            return "Máy chủ"
        if not worker_id:
            return ""
        with self._lock:
            item = self._items.get(worker_id)
        if item is None or not item.name:
            return "PC"
        return item.name

    def public(self) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            fresh = [item for item in self._items.values() if (now - item.seen) <= FRESH_SECONDS]
        if not fresh:
            return {
                "connected": False,
                "name": "",
                "cpus": 0,
                "count": 0,
                "cores": 0,
                "gpu": 0,
                "workers": 0,
                "latestBuild": VIDEO_WORKER_BUILD,
            }
        best = max(fresh, key=lambda item: (item.usable(), item.cpus, item.seen))
        return {
            "connected": True,
            "name": best.name,
            "cpus": best.cpus,
            "count": len(fresh),
            "cores": sum(item.cpus for item in fresh),
            "gpu": sum(1 for item in fresh if item.gpu),
            "workers": best.workers,
            "build": best.build,
            "latestBuild": VIDEO_WORKER_BUILD,
            "models": best.models,
            "readerOk": best.reader_ok,
            "readerNote": best.reader_note,
            "readerMode": best.reader_mode,
            "hardware": best.hardware,
            "timing": best.timing,
            "outdated": sum(1 for item in fresh if 0 < item.build < VIDEO_WORKER_BUILD),
            "broken": sum(1 for item in fresh if not item.usable()),
        }


helpers = HelperBook(settings.data_dir / "video_workers.json")
