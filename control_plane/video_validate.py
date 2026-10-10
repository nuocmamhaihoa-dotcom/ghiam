"""Kiểm tra file media trước khi đưa vào hàng đợi OCR.

Chặn file giả / tải dở (thiếu moov atom) — không để worker báo lỗi sau.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def validate_media_file(path: Path, *, original_name: str = "") -> None:
    """Raise ValueError nếu không mở được như ảnh/video thật.

    original_name: tên gốc (vd. clip.mp4) khi path là *.part trong lúc tải cắt khúc.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Không thấy file {path.name}.")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("File rỗng.")
    hint = Path(original_name or path.name)
    suffix = hint.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        _validate_image(path)
        return
    if suffix not in VIDEO_SUFFIXES:
        # *.part / không đuôi: vẫn thử ffprobe như video (upload cắt khúc).
        if path.suffix.lower() == ".part" or not suffix:
            _validate_video(path)
            return
        raise ValueError("Chỉ nhận video hoặc ảnh chụp màn hình.")
    _validate_video(path)


def _validate_image(path: Path) -> None:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError("Thiếu Pillow để kiểm tra ảnh.") from exc
    try:
        with Image.open(path) as image:
            ImageOps.exif_transpose(image).verify()
    except Exception as exc:
        raise ValueError(f"Ảnh không hợp lệ ({path.name}).") from exc


def _validate_video(path: Path) -> None:
    if shutil.which("ffprobe") is None:
        # Worker vẫn cần ffmpeg; thiếu ffprobe thì bỏ qua kiểm tra sớm.
        return
    done = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=180,
        check=False,
    )
    out = (done.stdout or "").strip().lower()
    err = (done.stderr or "").strip()
    if done.returncode == 0 and "video" in out:
        return
    detail = err.splitlines()[0] if err else out
    if "moov atom not found" in err.lower():
        raise ValueError(
            "File MP4/MOV chưa đầy đủ (thiếu moov) — thường do tải dở hoặc không phải video. "
            "Hãy tải lại bằng cắt khúc/VideoUp cho đến khi xong."
        )
    if "invalid data" in err.lower() or "invalid data found" in err.lower():
        raise ValueError(
            "File không phải video hợp lệ (hoặc bị hỏng). Không đưa vào hàng đợi đọc."
        )
    raise ValueError(
        f"Không mở được video để kiểm tra: {detail[:220]}" if detail else "Video không hợp lệ."
    )
