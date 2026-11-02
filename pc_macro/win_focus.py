"""Bring the bound Evoshop window forward by handle — never the hub overlay."""

from __future__ import annotations

import sys
from typing import Any, Callable, Iterable

try:
    import ctypes
    from ctypes import wintypes
except ImportError:  # pragma: no cover
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]

SW_RESTORE = 9
SW_SHOWMINNOACTIVE = 7
GA_ROOT = 2
GW_HWNDNEXT = 2
VK_MENU = 0x12
KEYEVENTF_KEYUP = 2
TITLE_MAX = 120
HUB_MARKERS = (
    "103.200.20.245",
    ":8088",
    "gõ số rồi enter",
    "gõ số rời",
    "pc-macro",
    "fb-poller",
)


def window_handle_at(x: int, y: int, api: Any = None, exclude: Iterable[int] | None = None) -> int:
    """Top-level HWND under this point, skipping overlay / excluded windows."""
    lib = api if api is not None else _user32()
    if lib is None:
        return 0
    skip = {int(handle) for handle in (exclude or ()) if int(handle or 0) > 0}
    hit = int(lib.WindowFromPoint(_point(lib, int(x), int(y))) or 0)
    current = _root_of(lib, hit) if hit > 0 else 0
    seen: set[int] = set()
    while current > 0 and current not in seen:
        seen.add(current)
        if _usable_at(lib, current, int(x), int(y), skip):
            return current
        current = int(lib.GetWindow(current, GW_HWNDNEXT) or 0) if hasattr(lib, "GetWindow") else 0
    return _hit_test_enum(lib, int(x), int(y), skip)


def focus_handle(hwnd: int, api: Any = None) -> bool:
    """Restore and foreground a saved window. False if the handle is dead."""
    handle = int(hwnd or 0)
    if handle <= 0:
        return False
    lib = api if api is not None else _user32()
    if lib is None:
        return False
    if not _alive(lib, handle):
        return False
    if hasattr(lib, "ShowWindow"):
        lib.ShowWindow(handle, SW_RESTORE)
    if _set_foreground(lib, handle):
        return True
    _alt_pulse(lib)
    if _set_foreground(lib, handle):
        return True
    _attach_focus(lib, handle)
    return _is_foreground(lib, handle) or _set_foreground(lib, handle)


def bring_window_at(x: int, y: int, api: Any = None, exclude: Iterable[int] | None = None) -> bool:
    """Focus whatever top-level window currently owns (x, y), skipping excludes."""
    return focus_handle(window_handle_at(int(x), int(y), api=api, exclude=exclude), api=api)


def window_title(hwnd: int, api: Any = None) -> str:
    """Visible title of a top-level window, or empty."""
    handle = int(hwnd or 0)
    if handle <= 0:
        return ""
    lib = api if api is not None else _user32()
    if lib is None:
        return ""
    if hasattr(lib, "title_of"):
        return clean_title(lib.title_of(handle))
    return clean_title(_read_title(lib, handle))


def find_window_by_title(title: str, api: Any = None) -> int:
    """First visible window whose title equals or contains the saved title."""
    needle = clean_title(title).lower()
    if not needle:
        return 0
    lib = api if api is not None else _user32()
    if lib is None:
        return 0
    exact = 0
    partial = 0

    def visit(hwnd: int) -> None:
        nonlocal exact, partial
        handle = int(hwnd or 0)
        if handle <= 0 or not _alive(lib, handle) or not _visible(lib, handle):
            return
        text = window_title(handle, api=lib).lower()
        if not text or is_hub_title(text):
            return
        if text == needle:
            exact = exact or handle
            return
        if needle in text or text in needle:
            partial = partial or handle

    _enum_windows(lib, visit)
    return exact or partial


def lower_hub_windows(api: Any = None) -> int:
    """Minimize hub Chrome so it cannot steal the next click or key."""
    lib = api if api is not None else _user32()
    if lib is None:
        return 0
    lowered = 0

    def visit(hwnd: int) -> None:
        nonlocal lowered
        handle = int(hwnd or 0)
        if handle <= 0 or not _alive(lib, handle):
            return
        if not is_hub_title(window_title(handle, api=lib)):
            return
        if hasattr(lib, "ShowWindow"):
            lib.ShowWindow(handle, SW_SHOWMINNOACTIVE)
            lowered += 1

    _enum_windows(lib, visit)
    return lowered


def is_hub_title(title: str) -> bool:
    """True when this Chrome tab is the hub page, not Evoshop."""
    text = clean_title(title).lower()
    if not text:
        return False
    return any(mark in text for mark in HUB_MARKERS)


def is_window_alive(hwnd: int, api: Any = None) -> bool:
    handle = int(hwnd or 0)
    if handle <= 0:
        return False
    lib = api if api is not None else _user32()
    if lib is None:
        return False
    return _alive(lib, handle)


def clean_title(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:TITLE_MAX]


def _usable_at(lib: Any, hwnd: int, x: int, y: int, skip: set[int]) -> bool:
    if hwnd <= 0 or hwnd in skip:
        return False
    if not _alive(lib, hwnd) or not _visible(lib, hwnd):
        return False
    return _contains_point(lib, hwnd, x, y)


def _hit_test_enum(lib: Any, x: int, y: int, skip: set[int]) -> int:
    found = 0

    def visit(hwnd: int) -> None:
        nonlocal found
        if found:
            return
        handle = _root_of(lib, int(hwnd or 0))
        if _usable_at(lib, handle, x, y, skip):
            found = handle

    _enum_windows(lib, visit)
    return found


def _root_of(lib: Any, hwnd: int) -> int:
    handle = int(hwnd or 0)
    if handle <= 0:
        return 0
    if hasattr(lib, "GetAncestor"):
        root = int(lib.GetAncestor(handle, GA_ROOT) or handle)
        return root if root > 0 else handle
    return handle


def _alive(lib: Any, hwnd: int) -> bool:
    if hasattr(lib, "IsWindow"):
        return bool(lib.IsWindow(int(hwnd)))
    return int(hwnd) > 0


def _visible(lib: Any, hwnd: int) -> bool:
    if hasattr(lib, "IsWindowVisible"):
        return bool(lib.IsWindowVisible(int(hwnd)))
    return True


def _contains_point(lib: Any, hwnd: int, x: int, y: int) -> bool:
    if hasattr(lib, "contains"):
        return bool(lib.contains(hwnd, x, y))
    box = _window_rect(lib, hwnd)
    if box is None:
        return True
    left, top, right, bottom = box
    return left <= x < right and top <= y < bottom


def _window_rect(lib: Any, hwnd: int) -> tuple[int, int, int, int] | None:
    if hasattr(lib, "rect_of"):
        box = lib.rect_of(hwnd)
        if box and len(box) == 4:
            return int(box[0]), int(box[1]), int(box[2]), int(box[3])
        return None
    if ctypes is None or wintypes is None or not hasattr(lib, "GetWindowRect"):
        return None

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    box = Rect()
    if not lib.GetWindowRect(int(hwnd), ctypes.byref(box)):
        return None
    return int(box.left), int(box.top), int(box.right), int(box.bottom)


def _read_title(lib: Any, hwnd: int) -> str:
    if ctypes is None or not hasattr(lib, "GetWindowTextLengthW") or not hasattr(lib, "GetWindowTextW"):
        return ""
    length = int(lib.GetWindowTextLengthW(int(hwnd)) or 0)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 2)
    lib.GetWindowTextW(int(hwnd), buf, length + 1)
    return buf.value or ""


def _enum_windows(lib: Any, visit: Callable[[int], None]) -> None:
    if hasattr(lib, "enum_windows"):
        for hwnd in lib.enum_windows() or []:
            visit(int(hwnd))
        return
    if ctypes is None or not hasattr(lib, "EnumWindows"):
        return
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: Any, _lp: Any) -> bool:
        visit(int(hwnd or 0))
        return True

    lib.EnumWindows(proc(callback), 0)


def _set_foreground(lib: Any, hwnd: int) -> bool:
    if hasattr(lib, "BringWindowToTop"):
        lib.BringWindowToTop(int(hwnd))
    if not hasattr(lib, "SetForegroundWindow"):
        return False
    return bool(lib.SetForegroundWindow(int(hwnd)))


def _is_foreground(lib: Any, hwnd: int) -> bool:
    if not hasattr(lib, "GetForegroundWindow"):
        return False
    current = int(lib.GetForegroundWindow() or 0)
    return current == int(hwnd) or _root_of(lib, current) == int(hwnd)


def _alt_pulse(lib: Any) -> None:
    """Release Windows' foreground lock so SetForegroundWindow can succeed."""
    if not hasattr(lib, "keybd_event"):
        return
    lib.keybd_event(VK_MENU, 0, 0, 0)
    lib.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def _attach_focus(lib: Any, hwnd: int) -> None:
    if not hasattr(lib, "AttachThreadInput") or not hasattr(lib, "GetWindowThreadProcessId"):
        return
    foreground = int(lib.GetForegroundWindow() or 0) if hasattr(lib, "GetForegroundWindow") else 0
    current_thread = _current_thread(lib)
    fg_thread = int(lib.GetWindowThreadProcessId(foreground, None) or 0) if foreground else 0
    target_thread = int(lib.GetWindowThreadProcessId(int(hwnd), None) or 0)
    attached: list[int] = []
    for other in (fg_thread, target_thread):
        if other and other != current_thread:
            lib.AttachThreadInput(current_thread, other, True)
            attached.append(other)
    if hasattr(lib, "BringWindowToTop"):
        lib.BringWindowToTop(int(hwnd))
    if hasattr(lib, "SetForegroundWindow"):
        lib.SetForegroundWindow(int(hwnd))
    for other in attached:
        lib.AttachThreadInput(current_thread, other, False)


def _current_thread(lib: Any) -> int:
    if hasattr(lib, "GetCurrentThreadId"):
        return int(lib.GetCurrentThreadId() or 0)
    return 0


def _user32() -> Any:
    if sys.platform != "win32" or ctypes is None:
        return None
    return ctypes.windll.user32


def _point(lib: Any, x: int, y: int) -> Any:
    if hasattr(lib, "POINT"):
        return lib.POINT(x, y)
    if ctypes is None or wintypes is None:
        return (x, y)

    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    return Point(x, y)
