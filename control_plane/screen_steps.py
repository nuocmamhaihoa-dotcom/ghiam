"""Turn an iPhone screen recording into a short scenario of visible steps."""

from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, ImageStat

_MAX_SECONDS = 180
_MAX_FRAMES = 120
_MAX_READS = 30
_MIN_DIFF = 1.8
_APP_WORDS = {"tiktok", "facebook", "instagram", "zalo", "danh", "ba", "follow", "da", "thich", "follower"}
_MIXED_OK = {"tiktok", "iphone", "facebook", "instagram", "youtube", "zalo"}
_STOP = {"though", "there", "which", "would", "could", "should", "about", "their", "other", "these", "those"}


class ScreenVideoError(ValueError):
    """The file is not a usable screen recording."""


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.casefold()


def _strong(word: str) -> bool:
    if re.fullmatch(r"@[A-Za-z0-9._]{3,30}", word):
        return True
    folded = _fold(word)
    if folded in _STOP:
        return False
    if folded in _MIXED_OK or folded in {"follow", "follower", "thich"}:
        return True
    letters = [ch for ch in word if ch.isalpha()]
    if len(letters) < 4 or len(letters) != len(word):
        return False
    if any(ch.isupper() for ch in word[1:]):
        return word.isupper() and len(word) >= 6
    return True


_SHORT_OK = {
    "anh", "ban", "ba", "be", "cho", "cua", "da", "doi", "ma", "moi", "nam",
    "suc", "ten", "thi", "tim", "tru", "van", "voi", "xem", "xung", "quanh",
}


def _short(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    return 2 <= len(letters) <= 3 and len(letters) == len(word) and _fold(word) in _SHORT_OK


def _words(line: str) -> list[str]:
    return re.findall(r"@[A-Za-z0-9._]{3,30}|[^\W_]{2,}", line, flags=re.UNICODE)


def clean_ocr(raw: str) -> str:
    """Keep readable words and drop symbol noise from an OCR dump."""
    lines: list[str] = []
    for line in raw.splitlines():
        found = _words(line)
        if not any(_strong(word) for word in found):
            continue
        kept = [word for word in found if _strong(word) or _short(word)]
        if not kept:
            continue
        text = " ".join(kept[:10])
        if lines and lines[-1].casefold() == text.casefold():
            continue
        lines.append(text)
        if len(lines) == 3:
            break
    text = " · ".join(lines)[:180]
    if not _has_signal(text):
        return ""
    return text


def _has_signal(text: str) -> bool:
    strong = [word for word in text.replace("·", " ").split() if _strong(word)]
    folded = _fold(text).replace(" ", "")
    if any(name in folded for name in ("tiktok", "facebook", "instagram", "zalo", "danhba", "follow", "thich")):
        return True
    if any(word.startswith("@") for word in strong):
        return True
    if len(strong) >= 2:
        return True
    return len(strong) == 1 and len(strong[0]) >= 10


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


def scenario_line(text: str) -> str:
    """Turn readable screen words into one action line."""
    folded = _fold(text)
    compact = folded.replace(" ", "")
    labels: list[str] = []
    if "danhba" in compact or "danh ba" in folded:
        labels.append("Mở Danh bạ")
    for key, label in (
        ("tiktok", "Mở TikTok"),
        ("facebook", "Mở Facebook"),
        ("instagram", "Mở Instagram"),
        ("zalo", "Mở Zalo"),
    ):
        if key in compact:
            labels.append(label)
    handles = re.findall(r"@[A-Za-z0-9._]{3,30}", text)
    if "foll" in compact:
        labels.append("Follow " + " ".join(handles) if handles else "Follow")
    elif handles:
        labels.append(" ".join(handles))
    if "thich" in compact:
        labels.append("Thích")
    extra_words = [
        word
        for word in text.replace("·", " ").split()
        if _fold(word) not in _APP_WORDS and not _fold(word).startswith("foll") and word not in handles
    ]
    extra = " ".join(extra_words[:8])
    parts: list[str] = []
    for label in labels:
        if label not in parts:
            parts.append(label)
    if extra and extra not in " ".join(parts):
        parts.append(extra)
    if not parts:
        return text
    return " · ".join(parts)[:180]


def steps_from_text(frames: list[tuple[float, str]]) -> list[dict[str, Any]]:
    """Collapse OCR text from successive frames into a scenario."""
    steps: list[dict[str, Any]] = []
    previous = ""
    for seconds, raw in frames:
        text = clean_ocr(raw)
        line = scenario_line(text) if text else ""
        if not line or same_caption(line, previous):
            continue
        previous = line
        steps.append({"t": round(float(seconds), 1), "caption": f"{clock_label(seconds)} — {line}"})
        if len(steps) == 20:
            break
    return steps


def read_screen_video(path: Path) -> list[dict[str, Any]]:
    """Sample a video, keep frames that change, and read the words on them."""
    duration = _duration(path)
    if duration is not None and duration > _MAX_SECONDS:
        raise ScreenVideoError("Video dài quá 3 phút. Quay ngắn hơn rồi chọn lại.")
    with tempfile.TemporaryDirectory(prefix="fb-screen-") as folder:
        work = Path(folder)
        images = _extract_frames(path, work)
        if not images:
            raise ScreenVideoError("Video không có hình.")
        chosen = _changed_frames(images)
        frames = [(seconds, _ocr(image)) for seconds, image in chosen]
    return steps_from_text(frames)


def _duration(path: Path) -> float | None:
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
    raw = (result.stdout or "").strip()
    if not raw or raw.upper() == "N/A":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


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
                "fps=1,scale=720:-2",
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
    prepared = image.with_name(image.stem + "-ocr.png")
    with Image.open(image) as full:
        width, height = full.size
        cropped = full.crop((0, int(height * 0.08), width, int(height * 0.92)))
        ImageOps.autocontrast(cropped.convert("L")).save(prepared)
    for lang in ("vie+eng", "eng"):
        try:
            result = subprocess.run(
                ["tesseract", str(prepared), "stdout", "-l", lang, "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode == 0:
            return result.stdout or ""
    return ""
