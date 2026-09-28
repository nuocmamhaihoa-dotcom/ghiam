from __future__ import annotations

import tempfile
from typing import Any

from tiktok_osint.errors import OcrUnavailable


class PaddleOcrEngine:
    """Lazy PaddleOCR wrapper. Install `paddleocr` only when OCR is required."""

    def __init__(self, lang: str = "vi") -> None:
        self.lang = lang
        self._engine: Any | None = None

    def recognize(self, image_bytes: bytes) -> str:
        engine = self._load()
        with tempfile.NamedTemporaryFile(suffix=".png") as handle:
            handle.write(image_bytes)
            handle.flush()
            try:
                raw = engine.ocr(handle.name, cls=True)
            except TypeError:
                raw = engine.ocr(handle.name)
        return flatten_paddle_result(raw)

    def _load(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OcrUnavailable("Cài paddleocr để bật OCR ảnh hồ sơ công khai") from exc
        self._engine = PaddleOCR(use_angle_cls=True, lang=self.lang, show_log=False)
        return self._engine


def flatten_paddle_result(raw: object) -> str:
    lines: list[str] = []
    _walk(raw, lines)
    return "\n".join(line for line in lines if line).strip()


def _walk(node: object, lines: list[str]) -> None:
    if isinstance(node, str):
        lines.append(node.strip())
        return
    if isinstance(node, tuple) and len(node) == 2 and isinstance(node[0], str):
        lines.append(node[0].strip())
        return
    if isinstance(node, dict):
        for key in ("text", "rec_text"):
            value = node.get(key)
            if isinstance(value, str):
                lines.append(value.strip())
        return
    if isinstance(node, list):
        for item in node:
            _walk(item, lines)
