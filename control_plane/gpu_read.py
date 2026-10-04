"""Đọc chữ bằng GPU trên PC có card NVIDIA. Hub vẫn dùng Tesseract khi không có GPU."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from control_plane.screen_people import TextLine

_MIN_CONF = 0.45
_LOCK = threading.Lock()
_INFER = threading.Lock()
_BATCH = 8
_reader: tuple[str, object] | None = None
_failed = False
_note_sent = False
_FALLBACK = "PC có card NVIDIA nhưng chưa cài bộ đọc GPU. Đang đọc bằng CPU."


def enabled() -> bool:
    return os.environ.get("CONTROL_OCR_ENGINE", "").strip().lower() == "gpu"


def nvidia_name() -> str:
    """Tên card NVIDIA, hoặc chuỗi rỗng khi máy không có card."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    text = (result.stdout or b"").decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return " ".join(lines[0].split())[:80]


def _unit_conf(value: float) -> float:
    if value > 1:
        return value / 100.0
    return value


def _bounds(points: object) -> tuple[float, float, float, float]:
    if not isinstance(points, (list, tuple)):
        return 0.0, 0.0, 0.0, 0.0
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        xs.append(float(point[0]))
        ys.append(float(point[1]))
    if not xs or not ys:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def lines_from_boxes(boxes: list[tuple[str, float, float, float, float, float]]) -> list[TextLine]:
    """Hộp chữ (text, độ tin 0–1, trái, trên, phải, dưới) thành dòng cho phần ghép tên sẵn có."""
    lines: list[TextLine] = []
    for text, conf, left, top, _right, bottom in boxes:
        if _unit_conf(float(conf)) < _MIN_CONF:
            continue
        cleaned = " ".join(str(text).split())
        if len(cleaned) < 2:
            continue
        lines.append(TextLine(cleaned, int(left), int(top), int(bottom)))
    lines.sort(key=lambda line: (line.top, line.left))
    return lines


def _paddle_boxes(raw: object) -> list[tuple[str, float, float, float, float, float]]:
    boxes: list[tuple[str, float, float, float, float, float]] = []
    if not raw:
        return boxes
    pages = raw if isinstance(raw, list) else [raw]
    for page in pages:
        if isinstance(page, dict):
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            polys = page.get("dt_polys") or page.get("rec_polys") or []
            for index, text in enumerate(texts):
                score = float(scores[index]) if index < len(scores) else 0.0
                poly = polys[index] if index < len(polys) else []
                left, top, right, bottom = _bounds(poly)
                boxes.append((str(text), score, left, top, right, bottom))
            continue
        if not isinstance(page, list):
            continue
        for line in page:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            poly, rec = line[0], line[1]
            if not isinstance(rec, (list, tuple)) or len(rec) < 2:
                continue
            left, top, right, bottom = _bounds(poly)
            boxes.append((str(rec[0]), float(rec[1]), left, top, right, bottom))
    return boxes


def _easy_boxes(raw: object) -> list[tuple[str, float, float, float, float, float]]:
    boxes: list[tuple[str, float, float, float, float, float]] = []
    if not isinstance(raw, list):
        return boxes
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        left, top, right, bottom = _bounds(item[0])
        boxes.append((str(item[1]), float(item[2]), left, top, right, bottom))
    return boxes


def _build_paddle() -> object | None:
    try:
        from paddleocr import PaddleOCR
    except Exception:
        return None
    for kwargs in (
        {"lang": "vi", "use_gpu": True, "show_log": False},
        {"lang": "vi", "device": "gpu"},
    ):
        try:
            return PaddleOCR(**kwargs)
        except TypeError:
            continue
        except Exception:
            return None
    return None


def _build_easy() -> object | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        import easyocr
        return easyocr.Reader(["vi", "en"], gpu=True)
    except Exception:
        return None


def _load_reader() -> tuple[str, object] | None:
    global _reader, _failed
    if _reader is not None or _failed:
        return _reader
    with _LOCK:
        if _reader is not None or _failed:
            return _reader
        paddle = _build_paddle()
        if paddle is not None:
            _reader = ("paddle", paddle)
            return _reader
        easy = _build_easy()
        if easy is not None:
            _reader = ("easy", easy)
            return _reader
        _failed = True
        return None


def reader_ready() -> bool:
    """True khi bộ đọc GPU đã nạp được. Không thử lại sau một lần thất bại."""
    if not enabled():
        return False
    return _load_reader() is not None


def prefers_single_worker() -> bool:
    """GPU đã nạp thì một card đọc theo lô, không chia khung cho nhiều luồng Tesseract."""
    return reader_ready()


def _one_image(kind: str, engine: object, image: Path) -> list[TextLine] | None:
    try:
        if kind == "paddle":
            raw = engine.ocr(str(image), cls=False) if hasattr(engine, "ocr") else engine.predict(str(image))
            boxes = _paddle_boxes(raw)
        else:
            boxes = _easy_boxes(engine.readtext(str(image)))
    except Exception:
        return None
    return lines_from_boxes(boxes)


def _easy_batch(engine: object, images: list[Path]) -> list[list[TextLine] | None] | None:
    batched = getattr(engine, "readtext_batched", None)
    if not callable(batched):
        return None
    try:
        raws = batched([str(image) for image in images])
    except Exception:
        return None
    if not isinstance(raws, list) or len(raws) != len(images):
        return None
    return [lines_from_boxes(_easy_boxes(raw)) for raw in raws]


def read_lines_batch(images: list[Path]) -> list[list[TextLine] | None]:
    """Một lô hình trên cùng một card. None ở vị trí nào thì khung đó đọc bằng Tesseract."""
    if not images:
        return []
    if not enabled():
        return [None] * len(images)
    reader = _load_reader()
    if reader is None:
        return [None] * len(images)
    kind, engine = reader
    found: list[list[TextLine] | None] = []
    with _INFER:
        for offset in range(0, len(images), _BATCH):
            chunk = images[offset : offset + _BATCH]
            batched = _easy_batch(engine, chunk) if kind == "easy" else None
            if batched is not None:
                found.extend(batched)
                continue
            for image in chunk:
                found.append(_one_image(kind, engine, image))
    return found


def read_lines(image: Path) -> list[TextLine] | None:
    """Dòng chữ từ GPU, hoặc None để khung đó đọc bằng Tesseract."""
    lines = read_lines_batch([image])
    if not lines:
        return None
    return lines[0]


def fallback_note() -> str:
    """Một câu khi đã xin GPU nhưng thư viện không nạp được."""
    global _note_sent
    if not enabled() or _reader is not None or not _failed or _note_sent:
        return ""
    _note_sent = True
    return _FALLBACK


def clear_reader() -> None:
    """Xóa bộ đọc đã nhớ. Dùng trong kiểm thử."""
    global _reader, _failed, _note_sent
    with _LOCK:
        _reader = None
        _failed = False
        _note_sent = False
