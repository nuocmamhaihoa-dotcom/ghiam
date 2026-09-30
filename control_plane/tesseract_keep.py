"""Giữ Tesseract sống trong bộ nhớ. Cùng PSM 11, cùng dpi 300, không phóng to ảnh."""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import os
import queue
import threading
from ctypes import POINTER, c_char_p, c_int, c_ubyte, c_void_p
from pathlib import Path

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
        self.opened = 0
        self.limit = max(1, os.cpu_count() or 1)
        self.broken = False
        self.lock = threading.Lock()
        # Mỗi lần chỉ nạp một bộ đọc. Các luồng khác vẫn mượn được bộ đọc đã có.
        self.opening = threading.Lock()
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


def _library_names() -> list[str]:
    names: list[str] = []
    found = ctypes.util.find_library("tesseract")
    if found:
        names.append(found)
    exe = os.environ.get("CONTROL_TESSERACT", "").strip().strip('"')
    if exe:
        folder = Path(exe).parent
        if folder.is_dir():
            for pattern in ("libtesseract-5.dll", "libtesseract.dll", "tesseract.dll"):
                candidate = folder / pattern
                if candidate.is_file():
                    names.append(str(candidate))
            names.extend(str(path) for path in sorted(folder.glob("libtesseract*.dll")))
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique


def _library() -> ctypes.CDLL | None:
    if _gate.loaded:
        return _gate.lib
    with _gate.lock:
        if _gate.loaded:
            return _gate.lib
        found: ctypes.CDLL | None = None
        for name in _library_names():
            try:
                lib = ctypes.CDLL(name)
            except OSError:
                continue
            if _bind(lib):
                found = lib
                break
        # Đánh dấu đã nạp sau cùng. Luồng khác thấy dấu này thì thư viện đã sẵn.
        _gate.lib = found
        _gate.broken = found is None
        _gate.loaded = True
        return found


def _data_dirs() -> list[str | None]:
    folders: list[str | None] = [None]
    prefix = os.environ.get("TESSDATA_PREFIX", "").strip().strip('"').rstrip("\\/")
    if prefix:
        folders.append(prefix)
    folders.extend(_DATA_DIRS)
    exe = os.environ.get("CONTROL_TESSERACT", "").strip().strip('"')
    if exe:
        folders.append(str(Path(exe).parent / "tessdata"))
    unique: list[str | None] = []
    seen: set[str] = set()
    for folder in folders:
        key = "" if folder is None else folder
        if key in seen:
            continue
        seen.add(key)
        unique.append(folder)
    return unique


def _open_api(lib: ctypes.CDLL) -> c_void_p | None:
    api = lib.TessBaseAPICreate()
    if not api:
        return None
    for language in (b"vie+eng", b"eng"):
        for folder in _data_dirs():
            path = None if folder is None else folder.encode("utf-8")
            if lib.TessBaseAPIInit2(api, path, language, _OEM_LSTM) == 0:
                lib.TessBaseAPISetPageSegMode(api, _PSM_SPARSE)
                return api
    lib.TessBaseAPIDelete(api)
    return None


def _open_one(lib: ctypes.CDLL) -> c_void_p | None:
    """Nạp thêm một bộ đọc khi còn chỗ. None khi đã đủ số bộ đọc hoặc nạp không được."""
    with _gate.lock:
        if _gate.broken or _gate.made >= _gate.limit:
            return None
        _gate.made += 1
    with _gate.opening:
        api = _open_api(lib)
    with _gate.lock:
        if api is None:
            _gate.made -= 1
            if _gate.opened == 0:
                _gate.broken = True
            return None
        _gate.opened += 1
    return api


def _borrow(lib: ctypes.CDLL) -> c_void_p | None:
    while True:
        if _gate.broken:
            return None
        try:
            return _gate.idle.get_nowait()
        except queue.Empty:
            pass
        with _gate.lock:
            room = _gate.made < _gate.limit
        if room:
            api = _open_one(lib)
            if api is not None:
                return api
            if _gate.broken:
                return None
            with _gate.lock:
                room = _gate.made < _gate.limit
            if room:
                return None
        # Đủ số bộ đọc rồi thì chờ một bộ được trả. Bộ đang nạp mà hỏng thì hỏi lại.
        try:
            return _gate.idle.get(timeout=0.5)
        except queue.Empty:
            continue


def warm_readers(count: int | None = None) -> int:
    """Nạp sẵn bộ đọc trước video đầu tiên. Trả về số bộ đọc đang có."""
    lib = _library()
    if lib is None:
        return 0
    with _gate.lock:
        target = _gate.limit if count is None else max(1, min(int(count), _gate.limit))
    while True:
        with _gate.lock:
            if _gate.broken or _gate.made >= target:
                return _gate.opened
        api = _open_one(lib)
        if api is None:
            with _gate.lock:
                return _gate.opened
        _gate.idle.put(api)


def reader_mode() -> str:
    """api khi đọc bằng Tesseract trong bộ nhớ, cli khi phải gọi lệnh tesseract từng khung."""
    lib = _library()
    if lib is None or _gate.broken:
        return "cli"
    return "api"


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
