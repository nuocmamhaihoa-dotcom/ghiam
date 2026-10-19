"""Gọi tesseract. Mỗi lần gọi đọc cả loạt ảnh để không tốn thời gian nạp bộ chữ nhiều lần."""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import functools
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# Mỗi tiến trình đọc đã chiếm một nhân. Không để tesseract tự mở thêm luồng.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

DIGITS = "0123456789"
ASCII = "@._-0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ "
_ONE_THREAD = {**os.environ, "OMP_THREAD_LIMIT": "1", "OMP_NUM_THREADS": "1"}
_ENGINE: dict[str, ctypes.CDLL | None] = {}
_ENGINES: dict[tuple[str, int, str, str | None], int | None] = {}


@dataclass(frozen=True)
class TextLine:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    # Chiều cao chữ: trung vị chiều cao các từ. Vòng tròn chạm màn hình đè lên
    # dòng làm khung dòng cao vọt, nhưng không làm đổi trung vị.
    word_h: int = 0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def text_h(self) -> int:
        """Chiều cao khung dòng, trừ khi khung cao vọt quá chiều cao chữ (có vật đè lên dòng)."""
        if self.word_h and self.h > 1.6 * self.word_h:
            return self.word_h
        return self.h


def check_tesseract() -> None:
    if shutil.which("tesseract") is None:
        raise RuntimeError("Chưa cài tesseract. Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-vie")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    if "vie" not in langs or "eng" not in langs:
        raise RuntimeError("Thiếu gói chữ. Ubuntu: sudo apt install tesseract-ocr-vie tesseract-ocr-eng")


@functools.lru_cache(maxsize=1)
def best_dir() -> str | None:
    """Bộ chữ lớn tessdata_best đọc số chính xác hơn. Dùng nếu có."""
    raw = os.environ.get("DOC_VIDEO_TESSDATA", "").strip()
    folder = Path(raw) if raw else Path(__file__).resolve().parents[1] / "tessdata_best"
    if (folder / "eng.traineddata").is_file() and (folder / "vie.traineddata").is_file():
        return str(folder)
    return None


def _library():
    """Thư viện tesseract nạp một lần trong tiến trình. None nếu máy không có.

    Windows dùng lệnh tesseract. Nạp nhầm file dll làm cả tiến trình đọc tắt giữa chừng.
    """
    if "lib" in _ENGINE:
        return _ENGINE["lib"]
    if os.name == "nt":
        _ENGINE["lib"] = None
        return None
    name = ctypes.util.find_library("tesseract")
    try:
        lib = ctypes.CDLL(name or "libtesseract.so.5")
        lib.TessBaseAPIGetTsvText.restype = ctypes.c_void_p
    except (AttributeError, OSError):
        _ENGINE["lib"] = None
        return None
    lib.TessBaseAPICreate.restype = ctypes.c_void_p
    lib.TessBaseAPIInit2.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.TessBaseAPIInit2.restype = ctypes.c_int
    lib.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.TessBaseAPISetVariable.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.TessBaseAPISetVariable.restype = ctypes.c_int
    lib.TessBaseAPISetImage.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.TessBaseAPISetSourceResolution.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.TessBaseAPIGetTsvText.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.TessBaseAPIEnd.argtypes = [ctypes.c_void_p]
    lib.TessBaseAPIDelete.argtypes = [ctypes.c_void_p]
    lib.TessDeleteText.argtypes = [ctypes.c_void_p]
    _ENGINE["lib"] = lib
    return lib


def _close_engines() -> None:
    lib = _ENGINE.get("lib")
    if lib is None:
        return
    for api in _ENGINES.values():
        if api:
            lib.TessBaseAPIEnd(api)
            lib.TessBaseAPIDelete(api)
    _ENGINES.clear()


atexit.register(_close_engines)


def _engine_key(lang: str, psm: int, whitelist: str, best: bool) -> tuple[str, int, str, str | None]:
    return (lang, psm, whitelist, best_dir() if best else None)


def _engine(lang: str, psm: int, whitelist: str, best: bool):
    """Một bộ đọc giữ sẵn trong tiến trình. Nạp lại bộ chữ cho mỗi khung làm chậm hơn nhiều lần đọc."""
    key = _engine_key(lang, psm, whitelist, best)
    if key in _ENGINES:
        return _ENGINES[key]
    lib = _library()
    if lib is None:
        _ENGINES[key] = None
        return None
    api = lib.TessBaseAPICreate()
    tessdata = key[3]
    folder = tessdata.encode() if tessdata else None
    if not api or lib.TessBaseAPIInit2(api, folder, lang.encode(), 3) != 0:
        _ENGINES[key] = None
        return None
    lib.TessBaseAPISetPageSegMode(api, psm)
    if whitelist:
        lib.TessBaseAPISetVariable(api, b"tessedit_char_whitelist", whitelist.encode())
    _ENGINES[key] = api
    return api


def _words_from_tsv(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in text.splitlines():
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
            continue
        rows.append(parts)
    return rows


def _recognize(api: int, image: Image.Image) -> list[list[str]]:
    lib = _library()
    if lib is None:
        return []
    if image.mode == "L":
        pixels, channels = image.tobytes(), 1
    else:
        rgb = image if image.mode == "RGB" else image.convert("RGB")
        pixels, channels = rgb.tobytes(), 3
        image = rgb
    buffer = ctypes.create_string_buffer(pixels)
    saved = os.dup(2)
    quiet = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(quiet, 2)
        lib.TessBaseAPISetImage(api, buffer, image.width, image.height, channels, image.width * channels)
        lib.TessBaseAPISetSourceResolution(api, 300)
        pointer = lib.TessBaseAPIGetTsvText(api, 0)
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(quiet)
    if not pointer:
        return []
    raw = ctypes.cast(pointer, ctypes.c_char_p).value or b""
    lib.TessDeleteText(pointer)
    return _words_from_tsv(raw.decode("utf-8", "replace"))


def _drop_engine(lang: str, psm: int, whitelist: str, best: bool) -> None:
    """Bỏ bộ đọc trong tiến trình khi nó vừa ném lỗi, để lần đọc này chuyển sang lệnh tesseract.

    Một bộ hỏng không được dùng lại cho các ảnh còn lại trong lô. Gọi đóng có thể
    thất bại; bỏ sót một bộ hỏng còn hơn để nó làm chết tiến trình đọc.
    """
    api = _ENGINES.pop(_engine_key(lang, psm, whitelist, best), None)
    lib = _ENGINE.get("lib")
    if not api or lib is None:
        return
    try:
        lib.TessBaseAPIEnd(api)
        lib.TessBaseAPIDelete(api)
    except Exception:
        return


def _run(images: list[Image.Image], lang: str, psm: int, whitelist: str, best: bool) -> list[list[list[str]]]:
    """Trả về, cho từng ảnh, danh sách dòng TSV (đã tách cột) ở mức từ."""
    if not images:
        return []
    api = _engine(lang, psm, whitelist, best)
    if api is not None:
        try:
            return [_recognize(api, image) for image in images]
        except Exception:
            _drop_engine(lang, psm, whitelist, best)
    return _run_cli(images, lang, psm, whitelist, best)


def _run_cli(images: list[Image.Image], lang: str, psm: int, whitelist: str, best: bool) -> list[list[list[str]]]:
    """Đọc bằng tiến trình tesseract khi không nạp được thư viện."""
    pages: list[list[list[str]]] = [[] for _ in images]
    with tempfile.TemporaryDirectory(prefix="docvideo-") as folder:
        paths = []
        for index, image in enumerate(images):
            path = Path(folder) / f"{index:05d}.png"
            image.save(path, compress_level=1, dpi=(300, 300))
            paths.append(str(path))
        listing = Path(folder) / "list.txt"
        # Windows mở danh sách ảnh bằng bảng mã hệ thống, không phải UTF-8.
        list_encoding = "mbcs" if os.name == "nt" else "utf-8"
        listing.write_text("\n".join(paths) + "\n", encoding=list_encoding)
        command = ["tesseract", str(listing), "stdout", "-l", lang, "--psm", str(psm)]
        tessdata = best_dir() if best else None
        if tessdata:
            command += ["--tessdata-dir", tessdata]
        if whitelist:
            command += ["-c", f"tessedit_char_whitelist={whitelist}"]
        command += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=_ONE_THREAD,
        )
    for raw in done.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
            continue
        try:
            page = int(parts[1]) - 1
        except ValueError:
            continue
        if 0 <= page < len(pages):
            pages[page].append(parts)
    return pages


def find_lines(image: Image.Image) -> list[TextLine]:
    """Đọc cả màn hình để biết mỗi dòng chữ nằm ở đâu. Chữ ở bước này chỉ để nhận dạng, chưa chính xác."""
    rows = _run([image], "vie", 11, "", best=False)[0]
    grouped: dict[tuple[str, str, str], list[list[str]]] = {}
    for parts in rows:
        grouped.setdefault((parts[2], parts[3], parts[4]), []).append(parts)
    lines = []
    for words in grouped.values():
        words.sort(key=lambda parts: int(parts[6]))
        if len(words) >= 2:
            # Vòng tròn chạm màn hình bị đọc thành một "từ" cao gấp mấy lần chữ. Bỏ nó khỏi dòng.
            ordered = sorted(int(p[9]) for p in words)
            median = ordered[(len(ordered) - 1) // 2]
            words = [p for p in words if int(p[9]) <= 1.8 * median] or words
        x0 = min(int(p[6]) for p in words)
        y0 = min(int(p[7]) for p in words)
        x1 = max(int(p[6]) + int(p[8]) for p in words)
        y1 = max(int(p[7]) + int(p[9]) for p in words)
        heights = sorted(int(p[9]) for p in words)
        word_h = heights[(len(heights) - 1) // 2]
        lines.append(TextLine(" ".join(p[11].strip() for p in words), x0, y0, x1, y1, word_h))
    lines.sort(key=lambda line: (line.y0, line.x0))
    return lines


def read_texts(images: list[Image.Image], lang: str, whitelist: str = "", best: bool = True) -> list[str]:
    """Đọc mỗi ảnh như một dòng chữ."""
    return [" ".join(p[11].strip() for p in words) for words in _run(images, lang, 7, whitelist, best)]
