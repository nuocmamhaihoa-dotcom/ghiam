"""Giữ Tesseract sống trong bộ nhớ. Khung dùng PSM 11. Dòng đối chiếu dùng PSM 7, bộ chữ chuẩn."""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import os
import queue
import threading
from collections.abc import Callable
from contextlib import contextmanager
from ctypes import POINTER, c_char_p, c_int, c_ubyte, c_void_p
from pathlib import Path
from typing import Iterator

from PIL import Image

# Một lõi cho mỗi bộ đọc. ffmpeg được gọi với môi trường riêng, không kế thừa giới hạn này.
os.environ["OMP_THREAD_LIMIT"] = "1"

_PSM_SPARSE = 11
_PSM_LINE = 7
_OEM_LSTM = 1
_DPI = 300
_HANDLE_WHITELIST = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@"
_DATA_DIRS = (
    None,
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)
_Opener = Callable[[ctypes.CDLL], c_void_p | None]


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
_line_gate = _Gate()
_slots: threading.Semaphore | None = None
_slots_lock = threading.Lock()


def reader_limit() -> int:
    """Số bộ đọc được phép chạy cùng lúc. PC đặt bằng 80% lõi."""
    with _gate.lock:
        return _gate.limit


def set_reader_limit(limit: int) -> None:
    """Giới hạn số bộ đọc Tesseract sống cùng lúc, trước khi ảnh đầu được đọc."""
    n = max(1, int(limit))
    with _gate.lock:
        _gate.limit = n
    with _line_gate.lock:
        _line_gate.limit = n
    global _slots
    with _slots_lock:
        _slots = threading.Semaphore(n)


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
    if hasattr(lib, "TessBaseAPISetVariable"):
        lib.TessBaseAPISetVariable.argtypes = [c_void_p, c_char_p, c_char_p]
        lib.TessBaseAPISetVariable.restype = c_int
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


def _quiet(lib: ctypes.CDLL, api: c_void_p) -> None:
    """Tesseract in lời báo như 'Image too small' ra cửa sổ PC. Đưa vào tệp rỗng."""
    _set_variable(lib, api, b"debug_file", os.devnull.encode("utf-8"))


def _open_api(lib: ctypes.CDLL) -> c_void_p | None:
    api = lib.TessBaseAPICreate()
    if not api:
        return None
    for language in (b"vie+eng", b"eng"):
        for folder in _data_dirs():
            path = None if folder is None else folder.encode("utf-8")
            if lib.TessBaseAPIInit2(api, path, language, _OEM_LSTM) == 0:
                lib.TessBaseAPISetPageSegMode(api, _PSM_SPARSE)
                _quiet(lib, api)
                return api
    lib.TessBaseAPIDelete(api)
    return None


def _standard_dirs() -> list[str | None]:
    """Thư mục bộ chữ chuẩn. Bỏ tessdata-fast vì hướng 2 phải khác hướng 1."""
    folders: list[str | None] = []
    standard = os.environ.get("CONTROL_TESSDATA_STANDARD", "").strip().strip('"').rstrip("\\/")
    if standard:
        folders.append(standard)
    prefix = os.environ.get("TESSDATA_PREFIX", "").strip().strip('"').rstrip("\\/")
    if prefix and Path(prefix).name != "tessdata-fast" and prefix != standard:
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


def _open_line_api(lib: ctypes.CDLL) -> c_void_p | None:
    api = lib.TessBaseAPICreate()
    if not api:
        return None
    for language in (b"vie+eng", b"eng"):
        for folder in _standard_dirs():
            path = None if folder is None else folder.encode("utf-8")
            if lib.TessBaseAPIInit2(api, path, language, _OEM_LSTM) == 0:
                lib.TessBaseAPISetPageSegMode(api, _PSM_LINE)
                _quiet(lib, api)
                return api
    lib.TessBaseAPIDelete(api)
    return None


def _open_one(lib: ctypes.CDLL, gate: _Gate, opener: _Opener) -> c_void_p | None:
    """Nạp thêm một bộ đọc khi còn chỗ. None khi đã đủ số bộ đọc hoặc nạp không được."""
    with gate.lock:
        if gate.broken or gate.made >= gate.limit:
            return None
        gate.made += 1
    with gate.opening:
        api = opener(lib)
    with gate.lock:
        if api is None:
            gate.made -= 1
            if gate.opened == 0:
                gate.broken = True
            return None
        gate.opened += 1
    return api


def _borrow(lib: ctypes.CDLL, gate: _Gate, opener: _Opener) -> c_void_p | None:
    while True:
        if gate.broken:
            return None
        try:
            return gate.idle.get_nowait()
        except queue.Empty:
            pass
        with gate.lock:
            room = gate.made < gate.limit
        if room:
            api = _open_one(lib, gate, opener)
            if api is not None:
                return api
            if gate.broken:
                return None
            with gate.lock:
                room = gate.made < gate.limit
            if room:
                return None
        # Đủ số bộ đọc rồi thì chờ một bộ được trả. Bộ đang nạp mà hỏng thì hỏi lại.
        try:
            return gate.idle.get(timeout=0.5)
        except queue.Empty:
            continue


def _warm_gate(lib: ctypes.CDLL, gate: _Gate, opener: _Opener, target: int) -> int:
    while True:
        with gate.lock:
            if gate.broken or gate.made >= target:
                return gate.opened
        api = _open_one(lib, gate, opener)
        if api is None:
            with gate.lock:
                return gate.opened
        gate.idle.put(api)


def warm_readers(count: int | None = None) -> int:
    """Nạp sẵn bộ đọc trước video đầu tiên. Trả về số bộ đọc khung đang có."""
    lib = _library()
    if lib is None:
        return 0
    with _gate.lock:
        target = _gate.limit if count is None else max(1, min(int(count), _gate.limit))
    opened = _warm_gate(lib, _gate, _open_api, target)
    with _line_gate.lock:
        line_target = _line_gate.limit if count is None else max(1, min(int(count), _line_gate.limit))
    _warm_gate(lib, _line_gate, _open_line_api, line_target)
    return opened


def reader_mode() -> str:
    """api khi đọc bằng Tesseract trong bộ nhớ, cli khi phải gọi lệnh tesseract từng khung."""
    lib = _library()
    if lib is None or _gate.broken:
        return "cli"
    return "api"


def _pixels(image: Image.Image) -> tuple[object, int, int] | None:
    gray = image if image.mode == "L" else image.convert("L")
    width, height = gray.size
    if width < 2 or height < 2:
        return None
    raw = gray.tobytes()
    return (c_ubyte * len(raw)).from_buffer_copy(raw), width, height


def _tsv_from(lib: ctypes.CDLL, api: c_void_p) -> str:
    ptr = lib.TessBaseAPIGetTsvText(api, 0)
    try:
        if not ptr:
            return ""
        return ctypes.string_at(ptr).decode("utf-8", "replace")
    finally:
        if ptr:
            lib.TessDeleteText(ptr)
        lib.TessBaseAPIClear(api)


def _recognize(lib: ctypes.CDLL, api: c_void_p, image: Image.Image) -> str:
    packed = _pixels(image)
    if packed is None:
        return ""
    pixels, width, height = packed
    lib.TessBaseAPIClearAdaptiveClassifier(api)
    lib.TessBaseAPISetPageSegMode(api, _PSM_SPARSE)
    lib.TessBaseAPISetImage(api, pixels, width, height, 1, width)
    lib.TessBaseAPISetSourceResolution(api, _DPI)
    return _tsv_from(lib, api)


def _set_variable(lib: ctypes.CDLL, api: c_void_p, name: bytes, value: bytes) -> None:
    if not hasattr(lib, "TessBaseAPISetVariable"):
        return
    lib.TessBaseAPISetVariable(api, name, value)


def _recognize_line(lib: ctypes.CDLL, api: c_void_p, image: Image.Image, kind: str) -> str:
    packed = _pixels(image)
    if packed is None:
        return ""
    pixels, width, height = packed
    lib.TessBaseAPIClearAdaptiveClassifier(api)
    lib.TessBaseAPISetPageSegMode(api, _PSM_LINE)
    if kind == "handle":
        _set_variable(lib, api, b"tessedit_char_whitelist", _HANDLE_WHITELIST)
    else:
        _set_variable(lib, api, b"tessedit_char_whitelist", b"")
    try:
        lib.TessBaseAPISetImage(api, pixels, width, height, 1, width)
        lib.TessBaseAPISetSourceResolution(api, _DPI)
        return _tsv_from(lib, api)
    finally:
        _set_variable(lib, api, b"tessedit_char_whitelist", b"")


def _drain(gate: _Gate) -> None:
    lib = _gate.lib
    if lib is None:
        return
    while True:
        try:
            api = gate.idle.get_nowait()
        except queue.Empty:
            break
        lib.TessBaseAPIEnd(api)
        lib.TessBaseAPIDelete(api)


def _shutdown() -> None:
    _drain(_line_gate)
    _drain(_gate)


atexit.register(_shutdown)


@contextmanager
def _slot() -> Iterator[None]:
    token = _slots
    if token is not None:
        token.acquire()
    try:
        yield
    finally:
        if token is not None:
            token.release()


def _read_with(
    image: Image.Image,
    gate: _Gate,
    opener: _Opener,
    recognize: Callable[[ctypes.CDLL, c_void_p, Image.Image], str],
) -> str | None:
    lib = _library()
    if lib is None or gate.broken:
        return None
    with _slot():
        api = _borrow(lib, gate, opener)
        if api is None:
            return None
        try:
            return recognize(lib, api, image)
        except Exception:
            return None
        finally:
            gate.idle.put(api)


def read_tsv(image: Image.Image) -> str | None:
    """TSV của một ảnh đã làm nét. None khi phải gọi lệnh tesseract như cũ."""
    return _read_with(image, _gate, _open_api, _recognize)


def read_line_tsv(image: Image.Image, *, kind: str) -> str | None:
    """TSV một dòng, PSM 7, bộ chữ chuẩn. None khi phải gọi lệnh tesseract."""

    def recognize(lib: ctypes.CDLL, api: c_void_p, picture: Image.Image) -> str:
        return _recognize_line(lib, api, picture, kind)

    return _read_with(image, _line_gate, _open_line_api, recognize)
