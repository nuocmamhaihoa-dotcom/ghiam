"""Read the names, accounts, and words visible in an iPhone screen recording."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, ImageStat

from control_plane.gpu_read import fallback_note, prefers_single_worker, read_lines
from control_plane.screen_people import (
    captions_from_sightings,
    lines_from_tsv,
    propose_rows,
    read_frame_tsv,
    sightings_from_lines,
)

_MAX_FRAMES = 2400
_MAX_READS = 1000
_MIN_DIFF = 0.08
_SAMPLE_FPS = 8.0
_STEP_LIMIT = 400
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


class ReadProgress:
    """Nhận phần trăm và việc đang làm. Mặc định không làm gì."""

    def report(self, percent: int, task: str) -> None:
        del percent, task

    def problem(self, text: str) -> None:
        del text


def ocr_workers(frame_count: int, cpu_count: int, reserve: int | None = None) -> int:
    """Số tiến trình Tesseract. Hub giữ một lõi cho trang hỏi tiến trình. PC đặt reserve 0."""
    if reserve is None:
        raw = os.environ.get("CONTROL_OCR_RESERVE", "1")
        try:
            reserve = int(raw)
        except ValueError:
            reserve = 1
    kept = max(0, reserve)
    cores = max(1, max(1, cpu_count) - kept)
    return max(1, min(max(1, frame_count), cores))


def _ffmpeg_extract_command(path: Path, pattern: Path, rate: float) -> list[str]:
    """PNG nén nhẹ, dùng hết lõi để tách. Không phóng to khung."""
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        "0",
        "-i",
        str(path),
        "-vf",
        f"fps={rate:.4f},scale=min(1080\\,iw):-2",
        "-frames:v",
        str(_MAX_FRAMES),
        "-c:v",
        "png",
        "-compression_level",
        "1",
        "-progress",
        "pipe:1",
        str(pattern),
    ]


def _media_seconds(raw: str) -> float | None:
    """ffmpeg ghi out_time_us và out_time_ms bằng micro giây."""
    try:
        return max(0.0, int(raw) / 1_000_000)
    except ValueError:
        return None


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


def visible_steps(frames: list[tuple[float, list[str]]]) -> list[dict[str, Any]]:
    """Giữ từng dòng người. Dòng lặp của cùng một màn thì bỏ."""
    steps: list[dict[str, Any]] = []
    previous = ""
    for seconds, lines in frames:
        for raw in lines:
            line = " ".join(raw.split())[:180]
            if not line or same_caption(line, previous):
                continue
            previous = line
            steps.append({"t": round(float(seconds), 1), "caption": f"{clock_label(seconds)} — {line}"})
            if len(steps) == _STEP_LIMIT:
                return steps
    return steps


def analyze_screen_video(
    path: Path,
    progress: ReadProgress | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Đọc chữ nhìn thấy và đề xuất người đủ ba cột. Chưa ghi vào bảng."""
    sink = progress if progress is not None else ReadProgress()
    sink.report(8, "Đọc thời lượng")
    duration = _duration(path)
    rate = _sample_rate(duration)
    with tempfile.TemporaryDirectory(prefix="fb-screen-") as folder:
        work = Path(folder)
        images = _extract_frames(path, work, rate, duration, sink)
        if not images:
            raise ScreenVideoError("Video không có hình.")
        chosen = _changed_frames(images, sink)
        readings = _read_frames(chosen, sink)
    sink.report(94, "Ghép tên")
    frames = [(seconds, lines) for seconds, lines, _sightings in readings]
    sightings = [item for _seconds, _lines, found in readings for item in found]
    return visible_steps(frames), propose_rows(sightings)


def read_screen_video(path: Path) -> list[dict[str, Any]]:
    """Sample a video, keep frames that change, and read the words on them."""
    steps, _people = analyze_screen_video(path)
    return steps


def _sample_rate(duration: float | None) -> float:
    """Đọc 8 hình mỗi giây. Video dài thì dàn đều trong giới hạn khung."""
    if duration is None or duration <= 0:
        return _SAMPLE_FPS
    if duration * _SAMPLE_FPS <= _MAX_FRAMES:
        return _SAMPLE_FPS
    return _MAX_FRAMES / duration


def _media_env() -> dict[str, str]:
    """ffmpeg dùng hết lõi. Giới hạn một luồng chỉ dành cho từng bộ đọc chữ."""
    env = os.environ.copy()
    env.pop("OMP_THREAD_LIMIT", None)
    return env


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
            timeout=60,
            check=False,
            env=_media_env(),
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


def _extract_frames(
    path: Path,
    work: Path,
    rate: float,
    duration: float | None,
    progress: ReadProgress,
) -> list[tuple[float, Path]]:
    pattern = work / "f-%05d.png"
    progress.report(12, "Tách khung hình")
    try:
        proc = subprocess.Popen(
            _ffmpeg_extract_command(path, pattern, rate),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_media_env(),
        )
    except OSError as error:
        raise ScreenVideoError("Không đọc được video.") from error
    lines: queue.Queue[str | None] = queue.Queue()

    def _read_stdout() -> None:
        if proc.stdout is None:
            lines.put(None)
            return
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        for _line in proc.stderr:
            pass

    reader = threading.Thread(target=_read_stdout, daemon=True)
    drain = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()
    drain.start()
    crept = 12
    shown = 12
    # Video dài bao lâu cũng được. Chỉ dừng khi ffmpeg im 3 phút.
    stall_seconds = 180
    deadline = time.monotonic() + stall_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise ScreenVideoError("Tách hình dừng vì không tiến thêm.")
            try:
                line = lines.get(timeout=min(1.0, remaining))
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if line is None:
                break
            deadline = time.monotonic() + stall_seconds
            raw = line.strip()
            seconds: float | None = None
            if raw.startswith("out_time_us="):
                seconds = _media_seconds(raw.split("=", 1)[1])
            elif raw.startswith("out_time_ms="):
                seconds = _media_seconds(raw.split("=", 1)[1])
            if seconds is None:
                continue
            if duration and duration > 0:
                percent = 12 + int(min(1.0, seconds / duration) * 28)
            else:
                crept = min(39, crept + 1)
                percent = crept
            shown = max(shown, min(40, percent))
            progress.report(shown, "Tách khung hình")
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise ScreenVideoError("Không đọc được video.") from None
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=2)
        drain.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()
    images = sorted(image for image in work.glob("f-*.png") if image.stem[2:].isdigit())
    if proc.returncode != 0 and not images:
        raise ScreenVideoError("Không đọc được video.")
    if proc.returncode != 0 and images:
        progress.problem("Tách hình dừng sớm.")
    if images:
        progress.report(40, "Tách khung hình")
    return [(index / rate, image) for index, image in enumerate(images)]


def _load_thumbs(images: list[Path]) -> list[Image.Image | None]:
    total = len(images)
    if not total:
        return []
    workers = ocr_workers(total, os.cpu_count() or 1)
    found: list[Image.Image | None] = [None] * total
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_thumb, image): index for index, image in enumerate(images)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                found[index] = future.result()
            except Exception:
                found[index] = None
    return found


def _changed_frames(images: list[tuple[float, Path]], progress: ReadProgress) -> list[tuple[float, Path]]:
    chosen: list[tuple[float, Path]] = []
    previous: Image.Image | None = None
    unopened = 0
    total = len(images)
    progress.report(42, "Chọn khung đổi")
    thumbs = _load_thumbs([image for _seconds, image in images])
    for index, (seconds, image) in enumerate(images):
        if index % 8 == 0:
            progress.report(42 + int((index / max(total, 1)) * 5), "Chọn khung đổi")
        small = thumbs[index]
        if small is None:
            unopened += 1
            continue
        if previous is not None:
            score = ImageStat.Stat(ImageChops.difference(previous, small)).mean[0]
            if score < _MIN_DIFF:
                continue
        chosen.append((seconds, image))
        previous = small
        if len(chosen) == _MAX_READS:
            if index + 1 < total:
                progress.problem("Đã đọc 1000 khung đổi. Phần sau của video chưa xử lý.")
            break
    if unopened:
        progress.problem(f"{unopened} khung không mở được.")
    progress.report(47, "Chọn khung đổi")
    return chosen


def _thumb(image: Path) -> Image.Image | None:
    try:
        with Image.open(image) as full:
            gray = full.convert("L")
            width, height = gray.size
            small = gray.crop((0, int(height * 0.08), width, int(height * 0.92))).resize((80, 130))
            small.load()
            return small
    except OSError:
        return None


def _read_one(item: tuple[float, Path]) -> tuple[float, list[str], list[dict[str, str]]]:
    seconds, image = item
    try:
        text_lines = read_lines(image)
    except Exception:
        text_lines = None
    if text_lines is None:
        tsv = read_frame_tsv(image)
        text_lines = lines_from_tsv(tsv) if tsv else []
    sightings = sightings_from_lines(text_lines)
    captions = captions_from_sightings(sightings)
    if not captions:
        raw = "\n".join(line.text for line in text_lines)
        text = clean_ocr(raw)
        fallback = seen_line(text) if text else ""
        if fallback:
            captions = [fallback]
    return seconds, captions, sightings


def _read_frames(
    chosen: list[tuple[float, Path]],
    progress: ReadProgress,
) -> list[tuple[float, list[str], list[dict[str, str]]]]:
    if not chosen:
        progress.report(92, "Đọc chữ")
        return []
    total = len(chosen)
    workers = 1 if prefers_single_worker() else ocr_workers(total, os.cpu_count() or 1)
    results: list[tuple[float, list[str], list[dict[str, str]]] | None] = [None] * total
    blank = 0
    failed = 0
    done_count = 0
    progress.report(48, "Đọc chữ")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_read_one, item): index for index, item in enumerate(chosen)}
        for future in as_completed(futures):
            index = futures[future]
            done_count += 1
            try:
                reading = future.result()
            except Exception:
                failed += 1
                results[index] = (chosen[index][0], [], [])
            else:
                _seconds, captions, sightings = reading
                if not captions and not sightings:
                    blank += 1
                results[index] = reading
            percent = 48 + int((done_count / total) * 44)
            progress.report(min(92, percent), f"Đọc chữ, khung {done_count}/{total}")
    if failed:
        progress.problem(f"{failed} khung không đọc được.")
    if blank == total:
        progress.problem("Không đọc được chữ trên video.")
    elif blank:
        progress.problem(f"{blank} khung không có chữ.")
    note = fallback_note()
    if note:
        progress.problem(note)
    return [item for item in results if item is not None]


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
