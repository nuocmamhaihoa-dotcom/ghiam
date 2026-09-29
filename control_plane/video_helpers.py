"""PC phụ kéo video về đọc. Hub giữ việc khi không có PC rảnh."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass


OFFER_SECONDS = 3.0
LEASE_SECONDS = 90.0
FRESH_SECONDS = 15.0


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned[:40] or "PC"


def _clean_id(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_")[:64]


@dataclass
class Helper:
    worker_id: str
    name: str
    cpus: int
    seen: float
    busy: bool = False


class HelperBook:
    def __init__(self) -> None:
        self._items: dict[str, Helper] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def beat(self, worker_id: str, name: str, cpus: int) -> str:
        cleaned_id = _clean_id(worker_id) or uuid.uuid4().hex
        cores = min(256, max(1, int(cpus or 1)))
        label = _clean_name(name)
        now = time.monotonic()
        with self._lock:
            current = self._items.get(cleaned_id)
            if current is None:
                self._items[cleaned_id] = Helper(cleaned_id, label, cores, now, False)
            else:
                current.name = label
                current.cpus = cores
                current.seen = now
        return cleaned_id

    def fresh(self, worker_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(worker_id)
            return item is not None and (now - item.seen) <= FRESH_SECONDS

    def has_idle(self) -> bool:
        now = time.monotonic()
        with self._lock:
            return any((now - item.seen) <= FRESH_SECONDS and not item.busy for item in self._items.values())

    def mark_busy(self, worker_id: str) -> None:
        with self._lock:
            item = self._items.get(worker_id)
            if item is not None:
                item.busy = True
                item.seen = time.monotonic()

    def mark_idle(self, worker_id: str) -> None:
        """Bỏ cờ bận. Không làm mới giờ thấy, để PC đã tắt không bị chờ thêm."""
        with self._lock:
            item = self._items.get(worker_id)
            if item is not None:
                item.busy = False

    def public(self) -> dict[str, object]:
        now = time.monotonic()
        with self._lock:
            fresh = [item for item in self._items.values() if (now - item.seen) <= FRESH_SECONDS]
        if not fresh:
            return {"connected": False, "name": "", "cpus": 0}
        best = max(fresh, key=lambda item: (item.cpus, item.seen))
        return {"connected": True, "name": best.name, "cpus": best.cpus}


helpers = HelperBook()
