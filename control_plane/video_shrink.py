"""Thu nhỏ video chờ đọc: chỉ giữ 2 khung mỗi giây, đúng số khung bộ đọc lấy ra.

Video quay màn hình khoảng 45 khung/giây, bộ đọc chỉ dùng 2 khung/giây. Giữ lại đúng các
khung đó thì file nhỏ đi nhiều lần: PC tải nhanh hơn, ổ đĩa chứa được nhiều video chờ hơn.
Khung giữ lại được nén lại, nên chỉ bật khi cổng chống lùi cho thấy không mất dòng đủ 3 ô.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

FPS = 2
DEFAULT_CRF = 12
DEFAULT_PRESET = "veryfast"
# Hạn ffmpeg. File tạm up-shrink- phải sống lâu hơn hạn này, không dùng hạn 30 phút của file tải dở.
SHRINK_TIMEOUT_SECONDS = 3600
SHRINK_TEMP_PREFIX = "up-shrink-"
# File up-<mã> của lần tải một mạch. Bản đang ghi có mtime mới, nên hạn này không đụng nó.
UPLOAD_TEMP_SECONDS = 1800
# Thêm vài phút sau hạn ffmpeg để tiến trình kịp xóa file tạm của chính nó.
SHRINK_TEMP_GRACE_SECONDS = 300


def shrink_command(src: Path, dst: Path, crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET, threads: int = 2) -> list[str]:
    """Lấy khung bằng đúng bộ lọc fps=2 mà bộ đọc dùng, rồi nén H.264 chất lượng cao."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-threads",
        str(max(1, threads)),
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-vf",
        f"fps={FPS}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(dst),
    ]


def shrink(
    src: Path,
    dst: Path,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    threads: int = 2,
    nice: int = 15,
    timeout: float = SHRINK_TIMEOUT_SECONDS,
) -> bool:
    """Ghi bản thu nhỏ ra dst. Hỏng thì xoá dst và trả về False, file gốc không bị đụng."""
    if shutil.which("ffmpeg") is None:
        return False
    command = shrink_command(src, dst, crf, preset, threads)
    if nice and os.name != "nt" and shutil.which("nice"):
        command = ["nice", "-n", str(nice), *command]
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        dst.unlink(missing_ok=True)
        return False
    if done.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        return False
    return True


def drop_stale_temps(folder: Path, now: float | None = None) -> int:
    """Xóa file tạm đã hết hạn. File đang tải và bản đang thu nhỏ còn mới thì giữ.

    Bản thu nhỏ đang ghi không dùng hạn 30 phút. Xóa up-shrink- khi ffmpeg còn chạy
    thì lần thu nhỏ bị coi là hỏng và video đó không được thu nhỏ lại.
    """
    if not folder.is_dir():
        return 0
    moment = time.time() if now is None else now
    upload_cutoff = moment - UPLOAD_TEMP_SECONDS
    shrink_cutoff = moment - (SHRINK_TIMEOUT_SECONDS + SHRINK_TEMP_GRACE_SECONDS)
    removed = 0
    for path in folder.glob("up-*"):
        try:
            cutoff = shrink_cutoff if path.name.startswith(SHRINK_TEMP_PREFIX) else upload_cutoff
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed
