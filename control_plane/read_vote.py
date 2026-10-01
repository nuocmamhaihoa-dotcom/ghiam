"""Đối chiếu một dòng tên hoặc @ bằng ba hướng, rồi năm lần đọc lại khi lệch.

Hướng 1 là chữ Tesseract nhanh đã có. Hướng 2 là bộ chữ chuẩn, cắt dòng, phóng đôi.
Hướng 3 là RapidOCR trên đúng dòng đó. Chưa cài RapidOCR thì hướng 3 là một lần Tesseract khác.
Hai hướng trùng thì không gọi hướng 3. Cả ba khác nhau thì đọc lại năm kiểu ảnh.
"""

from __future__ import annotations

import os
import tempfile
import threading

from PIL import Image, ImageFilter, ImageOps

from control_plane.people import clean_name, clean_username, fold_name

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None  # type: ignore[misc, assignment]

_engine: object | None = None
_engine_failed = False
_engine_lock = threading.Lock()


def rapid_ready() -> bool:
    """RapidOCR nhập được. Lần khởi tạo lỗi thì coi như chưa có."""
    return RapidOCR is not None and not _engine_failed


def vote_key(text: str, kind: str) -> str:
    """Khóa so trùng. Tên gom dấu. @ không phân biệt hoa thường."""
    if kind == "handle":
        handle = clean_username(text)
        return handle.casefold()
    cleaned = clean_name(text)
    letters = [char for char in cleaned if char.isalpha()]
    if len(letters) < 2:
        return ""
    return fold_name(cleaned)


def _groups(reads: list[str], kind: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for text in reads:
        key = vote_key(text, kind)
        if not key:
            continue
        found.setdefault(key, []).append(text)
    return found


def _show(texts: list[str], kind: str) -> str:
    if kind == "handle":
        for text in texts:
            handle = clean_username(text)
            if handle:
                return handle
        return ""
    names = [clean_name(text) for text in texts if vote_key(text, "name")]
    if not names:
        return ""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    best = max(counts.values())
    for name in names:
        if counts[name] == best:
            return name
    return names[0]


def needs_reread(seed: str, second: str, third: str | None, kind: str) -> bool:
    """Hai hướng đã trùng thì thôi. Một hướng có chữ, các hướng kia trống, thì thôi."""
    reads = [seed, second] if third is None else [seed, second, third]
    groups = _groups(reads, kind)
    if any(len(items) >= 2 for items in groups.values()):
        return False
    nonempty = [text for text in reads if vote_key(text, kind)]
    return len(nonempty) >= 2


def vote_line(seed: str, second: str, third: str | None, reruns: list[str], *, kind: str) -> tuple[str, bool]:
    """Trả chữ được ghi và cờ đã có ít nhất hai hướng trùng.

    Cờ sai khi chỉ còn một hướng đọc được: giữ chữ hướng đó, bộ lọc conf cũ vẫn áp dụng.
    Năm lần đọc lại hòa, hoặc không chuỗi nào hơn một phiếu, thì trả chuỗi rỗng.
    """
    reads = [seed, second] if third is None else [seed, second, third]
    groups = _groups(reads, kind)
    if groups:
        winner = max(groups.values(), key=len)
        if len(winner) >= 2:
            return _show(winner, kind), True
    nonempty = [text for text in reads if vote_key(text, kind)]
    if len(nonempty) < 2:
        return _show([seed], kind), False
    rerun_groups = _groups(reruns, kind)
    if not rerun_groups:
        return "", False
    ranked = sorted(rerun_groups.values(), key=len, reverse=True)
    if len(ranked[0]) < 2:
        return "", False
    if len(ranked) > 1 and len(ranked[1]) == len(ranked[0]):
        return "", False
    return _show(ranked[0], kind), True


def five_variants(crop: Image.Image) -> list[Image.Image]:
    """Năm kiểu ảnh cho lần đọc lại. Cùng một ảnh đưa vào Tesseract thì ra cùng một chuỗi."""
    width = max(1, crop.width)
    height = max(1, crop.height)
    return [
        crop.copy(),
        crop.resize((width * 2, height * 2), Image.Resampling.LANCZOS),
        ImageOps.autocontrast(crop),
        crop.filter(ImageFilter.SHARPEN),
        crop.resize((width * 3, height * 3), Image.Resampling.LANCZOS),
    ]


def _rapid_text(out: object) -> str:
    payload = out[0] if isinstance(out, tuple) else out
    if payload is None:
        return ""
    txts = getattr(payload, "txts", None)
    if txts:
        return " ".join(str(item) for item in txts if item)
    if not isinstance(payload, list):
        return ""
    lines: list[str] = []
    for item in payload:
        if isinstance(item, str):
            lines.append(item)
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            lines.append(str(item[1]))
    return " ".join(line for line in lines if line)


def read_rapid(image: Image.Image) -> str | None:
    """None khi máy chưa có RapidOCR. Chuỗi rỗng khi đã đọc mà không thấy chữ."""
    global _engine, _engine_failed
    if RapidOCR is None or _engine_failed:
        return None
    with _engine_lock:
        if _engine is None:
            try:
                _engine = RapidOCR()
            except (OSError, RuntimeError, ValueError):
                _engine_failed = True
                return None
        engine = _engine
    if engine is None:
        return None
    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    path = handle.name
    handle.close()
    try:
        image.save(path)
        try:
            out = engine(path)
        except (OSError, RuntimeError, ValueError):
            return ""
    except OSError:
        return ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return _rapid_text(out)
