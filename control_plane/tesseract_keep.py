"""Giữ Tesseract sống trong bộ nhớ. Cùng PSM 11, cùng dpi 300, không phóng to ảnh."""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import os
import queue
import threading
from ctypes import POINTER, c_char_p, c_int, c_ubyte, c_void_p

from PIL import Image

# Một lõi cho mỗi bộ đọc. ffmpeg được gọi với môi trường riêng, không kế thừa giới hạn này.
os.environ["OMP_THREAD_LIMIT"] = "1"

_PSM_SPARSE = 11
_OEM_LSTM = 1
_DPI = 300
_DATA_DIRS = (
    None,
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)


class _Gate:
    def __init__(self) -> None:
        self.lib: ctypes.CDLL | None = None
        self.idle: queue.Queue[c_void_p] = queue.Queue()
        self.made = 0
        self.limit = max(1, os.cpu_count() or 1)
        self.broken = False
        self.lock = threading.Lock()
        self.loaded = False


_gate = _Gate()


def set_reader_limit(limit: int) -> None:
    """Giới hạn số bộ đọc Tesseract sống cùng lúc, trước khi ảnh đầu được đọc."""
    with _gate.lock:
        _gate.limit = max(1, int(limit))


def _bind(lib: ctypes.CDLL) -> bool:
    if not hasattr(lib, "TessBaseAPIGetTsvText"):
        return False
    lib.TessBaseAPICreate.restype = c_void_p
    lib.TessBaseAPIDelete.argtypes = [c_void_p]
    lib.TessBaseAPIEnd.argtypes = [c_void_p]
    lib.TessBaseAPIInit2.argtypes = [c_void_p, c_char_p, c_char_p, c_int]
    lib.TessBaseAPIInit2.restype = c_int
    lib.TessBaseAPISetPageSegMode.argtypes = [c_void_p, c_int]
    lib.TessBaseAPISetSourceResolution.argtypes = [c_void_p, c_int]
    lib.TessBaseAPISetImage.argtypes = [c_void_p, POINTER(c_ubyte), c_int, c_int, c_int, c_int]
    lib.TessBaseAPIGetTsvText.argtypes = [c_void_p, c_int]
    lib.TessBaseAPIGetTsvText.restype = c_void_p
    lib.TessDeleteText.argtypes = [c_void_p]
    lib.TessBaseAPIClear.argtypes = [c_void_p]
    lib.TessBaseAPIClearAdaptiveClassifier.argtypes = [c_void_p]
    return True


def _library() -> ctypes.CDLL | None:
    if _gate.loaded:
        return _gate.lib
    with _gate.lock:
        if _gate.loaded:
            return _gate.lib
        _gate.loaded = True
        found = ctypes.util.find_library("tesseract")
        if not found:
            _gate.broken = True
            return None
        try:
            lib = ctypes.CDLL(found)
        except OSError:
            _gate.broken = True
            return None
        if not _bind(lib):
            _gate.broken = True
            return None
        _gate.lib = lib
        return lib


def _open_api(lib: ctypes.CDLL) -> c_void_p | None:
    api = lib.TessBaseAPICreate()
    if not api:
        return None
    for language in (b"vie+eng", b"eng"):
        for folder in _DATA_DIRS:
            path = None if folder is None else folder.encode("utf-8")
            if lib.TessBaseAPIInit2(api, path, language, _OEM_LSTM) == 0:
                lib.TessBaseAPISetPageSegMode(api, _PSM_SPARSE)
                return api
    lib.TessBaseAPIDelete(api)
    return None


def _borrow(lib: ctypes.CDLL) -> c_void_p | None:
    if _gate.broken:
        return None
    try:
        return _gate.idle.get_nowait()
    except queue.Empty:
        pass
    with _gate.lock:
        if _gate.broken:
            return None
        if _gate.made < _gate.limit:
            api = _open_api(lib)
            if api is None:
                if _gate.made == 0:
                    _gate.broken = True
                return None
            _gate.made += 1
            return api
    return _gate.idle.get()


def _recognize(lib: ctypes.CDLL, api: c_void_p, image: Image.Image) -> str:
    gray = image if image.mode == "L" else image.convert("L")
    width, height = gray.size
    if width < 2 or height < 2:
        return ""
    raw = gray.tobytes()
    pixels = (c_ubyte * len(raw)).from_buffer_copy(raw)
    lib.TessBaseAPIClearAdaptiveClassifier(api)
    lib.TessBaseAPISetPageSegMode(api, _PSM_SPARSE)
    lib.TessBaseAPISetImage(api, pixels, width, height, 1, width)
    lib.TessBaseAPISetSourceResolution(api, _DPI)
    ptr = lib.TessBaseAPIGetTsvText(api, 0)
    try:
        if not ptr:
            return ""
        return ctypes.string_at(ptr).decode("utf-8", "replace")
    finally:
        if ptr:
            lib.TessDeleteText(ptr)
        lib.TessBaseAPIClear(api)


def _shutdown() -> None:
    lib = _gate.lib
    if lib is None:
        return
    while True:
        try:
            api = _gate.idle.get_nowait()
        except queue.Empty:
            break
        lib.TessBaseAPIEnd(api)
        lib.TessBaseAPIDelete(api)


atexit.register(_shutdown)


def read_tsv(image: Image.Image) -> str | None:
    """TSV của một ảnh đã làm nét. None khi phải gọi lệnh tesseract như cũ."""
    lib = _library()
    if lib is None or _gate.broken:
        return None
    api = _borrow(lib)
    if api is None:
        return None
    try:
        return _recognize(lib, api, image)
    except Exception:
        return None
    finally:
        _gate.idle.put(api)
