"""PC đọc video: số lõi theo lúc máy rảnh, mức ưu tiên thấp, và không bị Windows bóp tốc độ theo tiến trình.

Mọi hàm hệ thống trả giá trị an toàn khi máy không phải Windows hoặc hệ thống từ chối.
"""

from __future__ import annotations

import ctypes
import os

# Không có bàn phím hay chuột từ ngần này giây thì máy tính là rảnh.
IDLE_AFTER_SEC = 120.0

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_THREAD_PRIORITY_HIGHEST = 2
_PROCESS_POWER_THROTTLING = 4
_THROTTLE_EXECUTION_SPEED = 0x1


class PowerBudget:
    """Số bộ đọc theo lúc dùng máy: vừa phải khi bạn đang làm việc, nhiều hơn khi máy rảnh đủ lâu."""

    def __init__(self, active: int, resting: int, idle_after: float = IDLE_AFTER_SEC) -> None:
        self.active = max(1, int(active))
        self.resting = max(self.active, int(resting))
        self.idle_after = float(idle_after)
        self.workers = self.active

    def update(self, idle_seconds: float | None) -> bool:
        """Cập nhật theo số giây không có thao tác. Trả True khi số bộ đọc vừa đổi."""
        idle = idle_seconds is not None and idle_seconds >= self.idle_after
        target = self.resting if idle else self.active
        changed = target != self.workers
        self.workers = target
        return changed

    @property
    def is_resting(self) -> bool:
        return self.workers == self.resting and self.resting != self.active


def idle_seconds() -> float | None:
    """Số giây từ thao tác bàn phím hoặc chuột gần nhất. None khi không đọc được."""
    if os.name != "nt":
        return None
    try:

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInput()
        info.cbSize = ctypes.sizeof(info)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]
        user32.GetLastInputInfo.restype = ctypes.c_int
        kernel32.GetTickCount.restype = ctypes.c_uint
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((int(kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0
    except (AttributeError, OSError):
        return None


def lower_priority() -> bool:
    """Tiến trình đọc video chạy dưới mức bình thường. ffmpeg và tesseract con kế thừa mức này."""
    try:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.SetPriorityClass.restype = ctypes.c_int
            return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS))
        os.nice(5)
        return True
    except (AttributeError, OSError):
        return False


def keep_full_speed() -> bool:
    """Windows 11: báo cho hệ thống đừng đẩy tiến trình ẩn này vào chế độ tiết kiệm điện (EcoQoS)."""
    if os.name != "nt":
        return False
    try:

        class State(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        state = State(1, _THROTTLE_EXECUTION_SPEED, 0)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        kernel32.SetProcessInformation.restype = ctypes.c_int
        return bool(
            kernel32.SetProcessInformation(
                kernel32.GetCurrentProcess(), _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)
            )
        )
    except (AttributeError, OSError):
        return False


def raise_this_thread() -> bool:
    """Luồng giữ nhịp nối hub chạy ngang mức bình thường dù cả tiến trình ở mức thấp."""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel32.SetThreadPriority.restype = ctypes.c_int
        return bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), _THREAD_PRIORITY_HIGHEST))
    except (AttributeError, OSError):
        return False
