"""Cấu hình thật của PC đọc video, để chọn cách tối ưu bằng số đo thay vì đoán.

Mỗi hàm trả giá trị rỗng khi không đọc được. Không gọi lệnh ngoài, chỉ đọc registry và tệp hệ thống.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import tempfile
from pathlib import Path

_GPU_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11cd-be05-08002be10318}"
_CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"


def _clean(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _registry_value(path: str, name: str) -> str:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
        value, _kind = winreg.QueryValueEx(key, name)
    return _clean(value, 120)


def _linux_cpu_blocks() -> list[dict[str, str]]:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    blocks: list[dict[str, str]] = []
    for chunk in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
        if fields:
            blocks.append(fields)
    return blocks


def cpu_name() -> str:
    try:
        if os.name == "nt":
            return _clean(_registry_value(_CPU_KEY, "ProcessorNameString"), 80)
        for block in _linux_cpu_blocks():
            if block.get("model name"):
                return _clean(block["model name"], 80)
    except (OSError, ImportError):
        return ""
    return ""


def _windows_physical_cores() -> int:
    class ProcessorInfo(ctypes.Structure):
        _fields_ = [("ProcessorMask", ctypes.c_size_t), ("Relationship", ctypes.c_int), ("Reserved", ctypes.c_ulonglong * 2)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetLogicalProcessorInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetLogicalProcessorInformation.restype = ctypes.c_int
    size = ctypes.c_ulong(0)
    kernel32.GetLogicalProcessorInformation(None, ctypes.byref(size))
    each = ctypes.sizeof(ProcessorInfo)
    if size.value < each:
        return 0
    count = size.value // each
    buffer = (ProcessorInfo * count)()
    if not kernel32.GetLogicalProcessorInformation(buffer, ctypes.byref(size)):
        return 0
    return sum(1 for item in buffer if item.Relationship == 0)


def physical_cores() -> int:
    try:
        if os.name == "nt":
            return _windows_physical_cores()
        pairs = {
            (block.get("physical id", "0"), block["core id"])
            for block in _linux_cpu_blocks()
            if block.get("core id") is not None
        }
        return len(pairs)
    except (AttributeError, OSError):
        return 0


def gpu_names() -> list[str]:
    """Card đồ họa Windows ghi trong registry. Máy khác trả danh sách rỗng."""
    if os.name != "nt":
        return []
    found: list[str] = []
    try:
        for index in range(8):
            try:
                name = _registry_value(f"{_GPU_CLASS}\\{index:04d}", "DriverDesc")
            except OSError:
                continue
            if name and name not in found:
                found.append(_clean(name, 60))
    except ImportError:
        return []
    return found[:4]


def temp_free_mb(folder: str | Path | None = None) -> int:
    """Chỗ trống của ổ chứa khung ảnh tạm."""
    target = str(folder or tempfile.gettempdir())
    try:
        return max(0, int(shutil.disk_usage(target).free // (1024 * 1024)))
    except OSError:
        return 0


def collect(logical: int, ram_bytes: int) -> dict[str, object]:
    """Gói nhỏ gửi lên hub: tên CPU, lõi vật lý và lõi luồng, RAM, card đồ họa, chỗ trống ổ tạm."""
    return {
        "cpu": cpu_name(),
        "physical": physical_cores(),
        "logical": max(0, int(logical)),
        "ramMb": max(0, int(ram_bytes) // (1024 * 1024)),
        "gpus": gpu_names(),
        "tempFreeMb": temp_free_mb(),
    }
