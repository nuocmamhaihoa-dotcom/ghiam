"""PC phụ kéo video về đọc. Một PC giữ tối đa hai video. Hub đọc khi PC không còn chỗ."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

from control_plane.version import VIDEO_WORKER_BUILD

OFFER_SECONDS = 8.0
LEASE_SECONDS = 90.0
# Nhịp PC là 3 giây. Mạng chập một lúc vẫn tính là đang nối. Im 45 giây thì hub đọc thay.
FRESH_SECONDS = 45.0
JOBS_PER_PC = 2


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

    def usable(self) -> bool:
        return self.reader_ok is not False


class HelperBook:
    def __init__(self) -> None:
        self._items: dict[str, Helper] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

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
            if item is not None:
                item.seen = now

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
                and item.held < JOBS_PER_PC
            )

    def has_idle(self) -> bool:
        """Còn PC vừa nối và đang giữ ít hơn hai video."""
        return self.idle_count() > 0

    def try_hold(self, worker_id: str) -> bool:
        """Giữ thêm một video. Đủ hai video, PC không còn tươi, hoặc PC đọc thử không ra chữ, thì từ chối."""
        now = time.monotonic()
        with self._lock:
            item = self._items.get(worker_id)
            if (
                item is None
                or (now - item.seen) > FRESH_SECONDS
                or item.held >= JOBS_PER_PC
                or not item.usable()
            ):
                return False
            item.held += 1
            item.seen = now
            return True

    def mark_busy(self, worker_id: str) -> None:
        self.try_hold(worker_id)

    def mark_idle(self, worker_id: str) -> None:
        """Trả một chỗ. Không làm mới giờ thấy, để PC đã tắt không bị chờ thêm."""
        with self._lock:
            item = self._items.get(worker_id)
            if item is not None and item.held > 0:
                item.held -= 1

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
            "outdated": sum(1 for item in fresh if 0 < item.build < VIDEO_WORKER_BUILD),
            "broken": sum(1 for item in fresh if not item.usable()),
        }


helpers = HelperBook()
