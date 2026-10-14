"""VietOCR: bộ đọc dòng chữ tiếng Việt, đọc dấu chuẩn hơn tesseract.

Không bắt buộc. Thiếu torch, vietocr hoặc file trọng số thì available() trả về
False và phần đọc tên chỉ dùng tesseract. Không tải gì từ mạng lúc chạy:
cấu hình nằm trong vietocr_vgg_transformer.json, trọng số đặt sẵn ở
models/vgg_transformer.pth (hoặc DOC_VIDEO_VIETOCR_WEIGHTS).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image

_CONFIG = Path(__file__).with_name("vietocr_vgg_transformer.json")
_STATE: dict[str, object] = {}


def weights_path() -> Path:
    raw = os.environ.get("DOC_VIDEO_VIETOCR_WEIGHTS", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "models" / "vgg_transformer.pth"


def _load():
    if "predictor" in _STATE or "error" in _STATE:
        return _STATE.get("predictor")
    if os.environ.get("DOC_VIDEO_VIETOCR", "1").strip() == "0":
        _STATE["error"] = "tắt bằng DOC_VIDEO_VIETOCR=0"
        return None
    weights = weights_path()
    if not weights.is_file():
        _STATE["error"] = f"không có {weights}"
        return None
    try:
        import torch
        from vietocr.tool.config import Cfg
        from vietocr.tool.predictor import Predictor
    except ImportError as exc:
        _STATE["error"] = f"chưa cài vietocr: {exc}"
        return None
    # Mỗi tiến trình đọc chạy trên một nhân; torch không được tự mở thêm luồng.
    torch.set_num_threads(1)
    config = Cfg(json.loads(_CONFIG.read_text(encoding="utf-8")))
    config["weights"] = str(weights)
    config["device"] = "cpu"
    _STATE["predictor"] = Predictor(config)
    return _STATE["predictor"]


def available() -> bool:
    return _load() is not None


def unavailable_reason() -> str:
    _load()
    return str(_STATE.get("error", ""))


def read_lines(images: list[Image.Image]) -> list[str]:
    """Đọc mỗi ảnh như một dòng chữ. Không dùng được VietOCR thì trả về chuỗi rỗng cho mỗi ảnh."""
    predictor = _load()
    if predictor is None or not images:
        return ["" for _ in images]
    return [str(text or "") for text in predictor.predict_batch([image.convert("RGB") for image in images])]
