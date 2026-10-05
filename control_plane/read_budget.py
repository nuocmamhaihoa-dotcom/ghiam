"""Phần đọc video lấy một phần cố định của CPU và RAM, suốt thời gian đang đọc.

PC nối hub lấy 80%. Máy chủ trên VPS lấy 90%. Phần còn lại để máy vẫn trả lời
trang và nhịp nối. Lấy mức nhỏ hơn giữa phần trăm lõi và phần trăm RAM.
"""

from __future__ import annotations

import ctypes
import os

OCR_BYTES = 256 * 1024 * 1024
PC_SHARE_PERCENT = 80
HUB_SHARE_PERCENT = 90


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


def apply_hub_share() -> tuple[int, int]:
    """Gắn mức 90% vào tiến trình máy chủ trước khi uvicorn tách tiến trình con."""
    cpus = os.cpu_count() or 1
    ram = machine_ram_bytes()
    workers, reserve = share_budget(cpus, ram if ram > 0 else None, HUB_SHARE_PERCENT)
    os.environ["CONTROL_OCR_RESERVE"] = str(reserve)
    os.environ["CONTROL_FFMPEG_THREADS"] = str(workers)
    os.environ["CONTROL_READER_LIMIT"] = str(workers)
    return workers, cpus
