"""Turn an iPhone screen recording into a short list of visible steps."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

_MAX_SECONDS = 180
_MAX_FRAMES = 180
_MAX_READS = 24
_MIN_DIFF = 1.8


class ScreenVideoError(ValueError):
    """The file is not a usable screen recording."""


def clean_ocr(raw: str) -> str:
    """Keep a few readable lines from an OCR dump."""
    kept: list[str] = []
    for line in raw.splitlines():
        text = " ".join(line.split())
        letters = [ch for ch in text if ch.isalnum()]
        if len(letters) < 3:
            continue
        if kept and kept[-1].casefold() == text.casefold():
            continue
        kept.append(text[:80])
        if len(kept) == 4:
            break
    return " · ".join(kept)[:180]


def same_caption(left: str, right: str) -> bool:
    """True when two OCR strings are the same screen."""
    a = "".join(ch for ch in left.casefold() if ch.isalnum())
    b = "".join(ch for ch in right.casefold() if ch.isalnum())
    if not a or not b:
        return False
    return a == b or a in b or b in a


def clock_label(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def steps_from_text(frames: list[tuple[float, str]]) -> list[dict[str, Any]]:
    """Collapse OCR text from successive frames into steps."""
    steps: list[dict[str, Any]] = []
    previous = ""
    for seconds, raw in frames:
        text = clean_ocr(raw)
        if not text or same_caption(text, previous):
            continue
        previous = text
        steps.append({"t": round(float(seconds), 1), "caption": f"{clock_label(seconds)} — {text}"})
        if len(steps) == 40:
            break
    return steps


def read_screen_video(path: Path) -> list[dict[str, Any]]:
    """Sample a video, keep frames that change, and read the words on them."""
    duration = _duration(path)
    if duration <= 0.2:
        raise ScreenVideoError("Video không có hình.")
    if duration > _MAX_SECONDS:
        raise ScreenVideoError("Video dài quá 3 phút. Quay ngắn hơn rồi chọn lại.")
    with tempfile.TemporaryDirectory(prefix="fb-screen-") as folder:
        work = Path(folder)
        images = _extract_frames(path, work)
        if not images:
            raise ScreenVideoError("Không đọc được video.")
        chosen = _changed_frames(images)
        frames = [(seconds, _ocr(image)) for seconds, image in chosen]
    return steps_from_text(frames)


def _duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScreenVideoError("Không đọc được video.") from error
    try:
        return float((result.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


def _extract_frames(path: Path, work: Path) -> list[tuple[float, Path]]:
    pattern = work / "f-%03d.png"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-vf",
                "fps=1,scale=480:-2",
                "-frames:v",
                str(_MAX_FRAMES),
                str(pattern),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScreenVideoError("Không đọc được video.") from error
    images = sorted(work.glob("f-*.png"))
    return [(float(index), image) for index, image in enumerate(images)]


def _changed_frames(images: list[tuple[float, Path]]) -> list[tuple[float, Path]]:
    chosen: list[tuple[float, Image.Image, Path]] = []
    previous: Image.Image | None = None
    for seconds, image in images:
        full = Image.open(image).convert("L")
        width, height = full.size
        small = full.crop((0, int(height * 0.08), width, int(height * 0.92))).resize((80, 130))
        if previous is None:
            chosen.append((seconds, small, image))
            previous = small
            continue
        score = ImageStat.Stat(ImageChops.difference(previous, small)).mean[0]
        if score < _MIN_DIFF:
            continue
        chosen.append((seconds, small, image))
        previous = small
        if len(chosen) == _MAX_READS:
            break
    return [(seconds, image) for seconds, _small, image in chosen]


def _ocr(image: Path) -> str:
    for lang in ("vie+eng", "eng"):
        try:
            result = subprocess.run(
                ["tesseract", str(image), "stdout", "-l", lang, "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode == 0:
            return result.stdout or ""
    return ""
