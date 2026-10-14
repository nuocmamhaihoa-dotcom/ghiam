"""Ảnh dải chữ khi một ô không đọc được. Không đoán ký tự bị che."""

from __future__ import annotations

import io


def jpeg_strip(image: object, box: object) -> bytes:
    """Cắt đúng dải chữ và nén JPEG nhỏ để gắn vào ô kết quả."""
    try:
        x0 = int(getattr(box, "x0"))
        y0 = int(getattr(box, "y0"))
        x1 = int(getattr(box, "x1"))
        y1 = int(getattr(box, "y1"))
    except (TypeError, ValueError):
        return b""
    width = int(getattr(image, "width", 0) or 0)
    height = int(getattr(image, "height", 0) or 0)
    x0 = max(0, min(width - 1, x0 - 2))
    y0 = max(0, min(height - 1, y0 - 1))
    x1 = max(x0 + 1, min(width, x1 + 2))
    y1 = max(y0 + 1, min(height, y1 + 1))
    if x1 - x0 < 8 or y1 - y0 < 4:
        return b""
    try:
        crop = image.crop((x0, y0, x1, y1))  # type: ignore[attr-defined]
        if crop.width > 520 and crop.width > 0:
            ratio = 520 / crop.width
            crop = crop.resize((520, max(1, int(crop.height * ratio))))
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="JPEG", quality=55)
        return buf.getvalue()
    except (OSError, ValueError, AttributeError):
        return b""
