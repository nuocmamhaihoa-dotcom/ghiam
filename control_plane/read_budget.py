"""Phần đọc video lấy một phần cố định của CPU và RAM.

PC nối hub lấy 80% suốt thời gian máy đó đang nối. Máy chủ trên VPS lấy 95%
CPU và 95% RAM suốt đời tiến trình hub, không hạ khi máy rảnh. Phần còn lại
để trang và nhịp nối vẫn trả lời. Lấy mức nhỏ hơn giữa phần trăm lõi và phần
trăm RAM.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

OCR_BYTES = 256 * 1024 * 1024
PC_SHARE_PERCENT = 80
HUB_SHARE_PERCENT = 95


def share_budget(cpu_count: int, ram_bytes: int | None, percent: int) -> tuple[int, int]:
    """Số bộ đọc và số lõi để dành. Không bao giờ lấy quá số lõi của máy."""
    cpus = max(1, int(cpu_count or 1))
    part = max(1, min(100, int(percent)))
    by_cpu = max(1, (cpus * part) // 100)
    if ram_bytes is None or ram_bytes <= 0:
        workers = by_cpu
    else:
        by_ram = max(1, (int(ram_bytes) * part) // 100 // OCR_BYTES)
        workers = max(1, min(by_cpu, by_ram))
    workers = min(workers, cpus)
    return workers, cpus - workers


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


def _own_cgroup() -> Path | None:
    """Cgroup của đúng tiến trình hub. Không đụng cgroup gốc của cả máy."""
    try:
        text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or parts[0] != "0" or parts[1] != "":
            continue
        relative = parts[2].strip()
        if not relative or relative == "/" or "fb-poller" not in relative:
            return None
        path = Path("/sys/fs/cgroup") / relative.lstrip("/")
        if path.is_dir():
            return path
    return None


def hold_machine_share(percent: int, cpus: int, ram_bytes: int) -> None:
    """Giữ hub ở 95% CPU và 95% RAM của VPS cho đến khi tiến trình tắt."""
    path = _own_cgroup()
    if path is None:
        return
    part = max(1, min(100, int(percent)))
    period = 100_000
    quota = max(1000, period * max(1, int(cpus)) * part // 100)
    cpu_max = path / "cpu.max"
    try:
        if cpu_max.is_file():
            cpu_max.write_text(f"{quota} {period}\n", encoding="utf-8")
    except OSError:
        pass
    if ram_bytes <= 0:
        return
    high = ram_bytes * part // 100
    memory_high = path / "memory.high"
    try:
        if memory_high.is_file():
            memory_high.write_text(f"{high}\n", encoding="utf-8")
    except OSError:
        pass


def apply_hub_share() -> tuple[int, int]:
    """Gắn mức 95% vào tiến trình máy chủ và giữ đến khi hub tắt."""
    cpus = os.cpu_count() or 1
    ram = machine_ram_bytes()
    workers, reserve = share_budget(cpus, ram if ram > 0 else None, HUB_SHARE_PERCENT)
    os.environ["CONTROL_OCR_RESERVE"] = str(reserve)
    os.environ["CONTROL_FFMPEG_THREADS"] = str(workers)
    os.environ["CONTROL_READER_LIMIT"] = str(workers)
    hold_machine_share(HUB_SHARE_PERCENT, cpus, ram)
    return workers, cpus
