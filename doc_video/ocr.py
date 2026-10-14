"""Gọi tesseract. Mỗi lần gọi đọc cả loạt ảnh để không tốn thời gian nạp bộ chữ nhiều lần."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

DIGITS = "0123456789"
ASCII = "@._-0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ "
_ONE_THREAD = {**os.environ, "OMP_THREAD_LIMIT": "1", "OMP_NUM_THREADS": "1"}


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


def _run(images: list[Image.Image], lang: str, psm: int, whitelist: str, best: bool) -> list[list[list[str]]]:
    """Trả về, cho từng ảnh, danh sách dòng TSV (đã tách cột) ở mức từ."""
    pages: list[list[list[str]]] = [[] for _ in images]
    if not images:
        return pages
    with tempfile.TemporaryDirectory(prefix="docvideo-") as folder:
        paths = []
        for index, image in enumerate(images):
            path = Path(folder) / f"{index:05d}.png"
            image.save(path, compress_level=1, dpi=(300, 300))
            paths.append(str(path))
        listing = Path(folder) / "list.txt"
        listing.write_text("\n".join(paths) + "\n", encoding="utf-8")
        command = ["tesseract", str(listing), "stdout", "-l", lang, "--psm", str(psm)]
        tessdata = best_dir() if best else None
        if tessdata:
            command += ["--tessdata-dir", tessdata]
        if whitelist:
            command += ["-c", f"tessedit_char_whitelist={whitelist}"]
        command += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
        done = subprocess.run(command, capture_output=True, text=True, timeout=600, env=_ONE_THREAD)
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
