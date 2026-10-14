"""Chọn backend OCR: tesserocr (API lâu dài) / CLI / GPU nếu có.

VPS không GPU thì mặc định tesserocr CPU — cùng engine Tesseract, bỏ spawn process.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path


def gpu_available() -> bool:
    """True khi có NVIDIA CUDA dùng được (nvidia-smi + device)."""
    if os.environ.get("CONTROL_FORCE_NO_GPU", "").strip() in {"1", "true", "yes"}:
        return False
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        done = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and "GPU" in (done.stdout or "")


@functools.lru_cache(maxsize=1)
def tesserocr_available() -> bool:
    try:
        import tesserocr  # noqa: F401
    except Exception:
        return False
    return True


def ocr_engine_name() -> str:
    """auto | tesserocr | cli | paddle(gpu)."""
    raw = (os.environ.get("CONTROL_OCR_ENGINE", "auto") or "auto").strip().lower()
    if raw in {"tesserocr", "cli", "paddle", "auto"}:
        return raw
    return "auto"


def resolve_ocr_engine() -> str:
    """Engine thực tế sẽ dùng."""
    choice = ocr_engine_name()
    if choice == "paddle":
        if gpu_available():
            return "paddle"
        # Không có GPU: không đổi sang engine CPU khác (độ chính xác lệch) — về tesserocr/cli.
        return "tesserocr" if tesserocr_available() else "cli"
    if choice == "auto":
        if gpu_available() and os.environ.get("CONTROL_OCR_ALLOW_GPU", "").strip() in {"1", "true", "yes"}:
            return "paddle"
        if tesserocr_available():
            return "tesserocr"
        return "cli"
    if choice == "tesserocr":
        return "tesserocr" if tesserocr_available() else "cli"
    return "cli"


@functools.lru_cache(maxsize=1)
def system_tessdata() -> str | None:
    """Thư mục tessdata hệ thống có eng+vie."""
    override = os.environ.get("TESSDATA_PREFIX", "").strip()
    candidates: list[Path] = []
    if override:
        path = Path(override)
        candidates.append(path if path.name == "tessdata" else path / "tessdata")
    candidates.extend(
        [
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tesseract-ocr/tessdata"),
            Path("/usr/share/tessdata"),
        ]
    )
    for folder in candidates:
        if (folder / "eng.traineddata").is_file() and (folder / "vie.traineddata").is_file():
            return str(folder)
    if tesserocr_available():
        try:
            import tesserocr

            path, langs = tesserocr.get_languages()
            if path and "eng" in langs and "vie" in langs:
                return str(Path(path))
        except Exception:
            return None
    return None


def describe_ocr() -> str:
    engine = resolve_ocr_engine()
    gpu = "có NVIDIA GPU" if gpu_available() else "không có GPU"
    return f"OCR={engine} ({gpu})"
