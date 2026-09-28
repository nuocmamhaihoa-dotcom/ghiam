"""Read the names, accounts, and words visible in an iPhone screen recording."""

from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, ImageStat

from control_plane.screen_people import propose_rows, sightings_from_image

_MAX_SECONDS = 600
_MAX_FRAMES = 120
_MAX_READS = 30
_MIN_DIFF = 1.8
_APP_WORDS = {"tiktok", "facebook", "instagram", "zalo", "danh", "ba", "follow", "da", "thich", "follower"}
_MIXED_OK = {"tiktok", "iphone", "facebook", "instagram", "youtube", "zalo"}
_KEEP_LOWER = {"tiktok", "facebook", "instagram", "zalo", "follow", "follower"}
_STOP = {"though", "there", "which", "would", "could", "should", "about", "their", "other", "these", "those"}
_JUNK = {
    "bank", "block", "digibank", "topcv", "beko", "ecord", "panh", "foal", "eral",
    "recgen", "though", "daily", "record", "screen",
}
_PLACES = (
    ("danhba", "Danh bạ"),
    ("tiktok", "TikTok"),
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("zalo", "Zalo"),
)


class ScreenVideoError(ValueError):
    """The file is not a usable screen recording."""


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.casefold().replace("đ", "d")


def _marked(word: str) -> bool:
    return _fold(word) != word.casefold()


def _strong(word: str) -> bool:
    if re.fullmatch(r"@[A-Za-z0-9._]{3,30}", word):
        return True
    folded = _fold(word)
    if folded in _STOP or folded in _JUNK:
        return False
    if folded in _MIXED_OK or folded in _KEEP_LOWER or folded == "thich":
        return True
    letters = [ch for ch in word if ch.isalpha()]
    if len(letters) != len(word):
        return False
    if _marked(word) and len(letters) >= 2:
        return True
    if len(letters) < 4:
        return False
    if word.isupper():
        return len(word) >= 4
    return word[0].isupper() and word[1:].islower()


_SHORT_OK = {
    "anh", "ban", "ba", "be", "cho", "cua", "da", "doi", "ma", "moi", "nam",
    "suc", "ten", "thi", "tim", "tru", "van", "voi", "xem", "xung", "quanh", "he",
}


def _plain(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    folded = _fold(word)
    return (
        len(letters) >= 4
        and len(letters) == len(word)
        and word.islower()
        and not _marked(word)
        and folded not in _JUNK
        and folded not in _STOP
    )


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
        marked_line = any(_marked(word) for word in found)
        kept = [
            word
            for word in found
            if _strong(word) or _short(word) or (marked_line and _plain(word))
        ]
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
    words = text.replace("·", " ").split()
    folded = _fold(text).replace(" ", "")
    if any(word.startswith("@") for word in words):
        return True
    if any(key in folded for key, _label in _PLACES):
        return True
    if sum(1 for word in words if _marked(word)) >= 3:
        return True
    titled = [
        word for word in words
        if len(word) >= 4 and word[0].isupper() and (word[1:].islower() or word.isupper())
    ]
    return len(titled) >= 2


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


def seen_line(text: str) -> str:
    """Keep the place, account, and words that were on screen."""
    folded = _fold(text)
    compact = folded.replace(" ", "")
    parts: list[str] = []
    for key, label in _PLACES:
        if key in compact and label not in parts:
            parts.append(label)
    handles = list(dict.fromkeys(re.findall(r"@[A-Za-z0-9._]{3,30}", text)))
    if handles:
        parts.append(" ".join(handles))
    if "dafoll" in compact or "dafollow" in compact:
        parts.append("Đã follow")
    pieces = text.replace("·", " ").split()
    marked_text = any(_marked(word) for word in pieces)
    extra_words = [
        word
        for word in pieces
        if _fold(word) not in _APP_WORDS
        and not _fold(word).startswith("foll")
        and _fold(word) not in {"thich", "da"}
        and word not in handles
        and (
            _strong(word)
            or _marked(word)
            or (word[:1].isupper() and _short(word))
            or (marked_text and _plain(word))
        )
    ]
    extra = " ".join(extra_words[:8])
    if extra and extra not in " ".join(parts):
        parts.append(extra)
    if not parts:
        return text
    return " · ".join(parts)[:180]


def steps_from_text(frames: list[tuple[float, str]]) -> list[dict[str, Any]]:
    """Collapse OCR text from successive frames into what was visible."""
    steps: list[dict[str, Any]] = []
    previous = ""
    for seconds, raw in frames:
        text = clean_ocr(raw)
        line = seen_line(text) if text else ""
        if not line or same_caption(line, previous):
            continue
        previous = line
        steps.append({"t": round(float(seconds), 1), "caption": f"{clock_label(seconds)} — {line}"})
        if len(steps) == 20:
            break
    return steps


def read_screen_image(path: Path) -> str:
    """Read the names and words visible in one screen photo."""
    text = clean_ocr(_ocr(path))
    if not text:
        return ""
    return seen_line(text)


def analyze_screen_video(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Đọc chữ nhìn thấy và đề xuất người đủ ba cột. Chưa ghi vào bảng."""
    duration = _duration(path)
    if duration is not None and duration > _MAX_SECONDS:
        raise ScreenVideoError("Video dài quá 10 phút. Dừng ghi rồi chọn lại.")
    rate = _sample_rate(duration)
    with tempfile.TemporaryDirectory(prefix="fb-screen-") as folder:
        work = Path(folder)
        images = _extract_frames(path, work, rate)
        if not images:
            raise ScreenVideoError("Video không có hình.")
        chosen = _changed_frames(images)
        frames = [(seconds, _ocr(image)) for seconds, image in chosen]
        sightings = [item for _seconds, image in chosen for item in sightings_from_image(image)]
    return steps_from_text(frames), propose_rows(sightings)


def read_screen_video(path: Path) -> list[dict[str, Any]]:
    """Sample a video, keep frames that change, and read the words on them."""
    steps, _people = analyze_screen_video(path)
    return steps


def _sample_rate(duration: float | None) -> float:
    """Spread a fixed number of frames across a long recording."""
    if duration is None or duration <= 0 or duration <= _MAX_FRAMES:
        return 1.0
    return _MAX_FRAMES / duration


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


def _extract_frames(path: Path, work: Path, rate: float) -> list[tuple[float, Path]]:
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
                f"fps={rate:.4f},scale=720:-2",
                "-frames:v",
                str(_MAX_FRAMES),
                str(pattern),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScreenVideoError("Không đọc được video.") from error
    images = sorted(work.glob("f-*.png"))
    return [(index / rate, image) for index, image in enumerate(images)]


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
    try:
        with Image.open(image) as full:
            width, height = full.size
            cropped = full.crop((0, int(height * 0.08), width, int(height * 0.92)))
            ImageOps.autocontrast(cropped.convert("L")).save(prepared)
    except OSError:
        return ""
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
