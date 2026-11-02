"""Bring a bound Windows browser to the front by handle, not by what's on top."""

from __future__ import annotations

import sys
from typing import Any

SW_RESTORE = 9
GA_ROOT = 2


def window_handle_at(x: int, y: int, api: Any = None) -> int:
    """Top-level HWND under this point, or 0 when not on Windows."""
    lib = api if api is not None else _user32()
    if lib is None:
        return 0
    hwnd = int(lib.WindowFromPoint(_point(lib, int(x), int(y))) or 0)
    if hwnd <= 0:
        return 0
    root = int(lib.GetAncestor(hwnd, GA_ROOT) or hwnd)
    return root if root > 0 else hwnd


def focus_handle(hwnd: int, api: Any = None) -> bool:
    """Restore and foreground a saved window. False if the handle is dead."""
    handle = int(hwnd or 0)
    if handle <= 0:
        return False
    lib = api if api is not None else _user32()
    if lib is None:
        return False
    if hasattr(lib, "IsWindow") and not lib.IsWindow(handle):
        return False
    lib.ShowWindow(handle, SW_RESTORE)
    return bool(lib.SetForegroundWindow(handle))


def bring_window_at(x: int, y: int, api: Any = None) -> bool:
    """Focus whatever top-level window currently owns (x, y)."""
    return focus_handle(window_handle_at(int(x), int(y), api=api), api=api)


def _user32() -> Any:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
    except ImportError:
        return None
    return ctypes.windll.user32


def _point(lib: Any, x: int, y: int) -> Any:
    if hasattr(lib, "POINT"):
        return lib.POINT(x, y)
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return (x, y)

    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    return Point(x, y)
