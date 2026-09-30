"""Read the names, accounts, and words visible in an iPhone screen recording."""

from __future__ import annotations

import io
import math
import os
import queue
import re
import shutil
import subprocess
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
    reading_counts,
    read_frame_tsv,
    sightings_from_lines,
    tsv_word_counts,
)

# 4 khung/giây cho video đến 30 phút. Video dài hơn thì dàn đều số khung này.
_MAX_FRAMES = 7200
# Khung chỉ nhích vài điểm ảnh thì bỏ. Một dòng chữ đổi (khoảng 3) vẫn được đọc.
_MIN_DIFF = 2.0
# Trang cùng bố cục mà chỉ đổi tên và tài khoản thì trung bình đổi chưa tới 1, nhưng ô 10x10 đổi mạnh nhất lên
# khoảng 29. Nhiễu nén, một điểm ảnh, hay cả khung dịch 1 điểm ảnh chỉ tới 3.
_BLOCK_DIFF = 12.0
# Rộng tối đa 720. Đo trên video iPhone thật: rộng 1080 đọc chậm hơn và nhận ra ít tên hơn.
_SAMPLE_FPS = 4.0
_PARTIAL_MARK = "extract.partial"
_PREVIEW_WIDTH = 420
_PREVIEW_CAP = 3
_PREVIEW_BYTES = 150_000
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

    def remembered(self) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
        """Khung đã đọc, khóa là giây làm tròn 3 số. Đọc tiếp thì bỏ qua các khóa này."""
        return {}

    def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
        del seconds, captions, sightings

    def staged_people(self) -> list[dict[str, str]] | None:
        """Người đã ghép xong. Có danh sách thì lần tiếp chỉ ghi lại, không đọc video."""
        return None

    def stage_people(self, people: list[dict[str, str]]) -> None:
        del people

    def note_tally(self, contacts: int, accounts: int, saved: int) -> None:
        """Số người thấy trong danh bạ, số người có tài khoản, số người đủ để ghi."""
        del contacts, accounts, saved

    def note_words(self, seen: int, kept: int) -> None:
        """Số từ Tesseract in ra và số từ giữ lại trước khi ghép tên."""
        del seen, kept

    def note_samples(self, images: list[bytes]) -> None:
        """Vài khung đã chọn, để trang chỉ đúng hình đang đọc."""
        del images

    def note_blank(self) -> None:
        """Mọi khung đã chọn đều không có chữ."""
        return


def ocr_workers(frame_count: int, cpu_count: int, reserve: int | None = None) -> int:
    """Số tiến trình Tesseract. Hub giữ một lõi. PC giữ phần lõi còn lại sau mức 80%."""
    if reserve is None:
        raw = os.environ.get("CONTROL_OCR_RESERVE", "1")
        try:
            reserve = int(raw)
        except ValueError:
            reserve = 1
    kept = max(0, reserve)
    cores = max(1, max(1, cpu_count) - kept)
    return max(1, min(max(1, frame_count), cores))


def _ffmpeg_thread_count() -> str:
    """0 là ffmpeg tự dùng hết lõi. PC đặt số lõi bằng mức 80%."""
    raw = os.environ.get("CONTROL_FFMPEG_THREADS", "0").strip()
    if raw.isdigit():
        return raw
    return "0"


def _ffmpeg_extract_command(
    path: Path,
    pattern: Path,
    rate: float,
    threads: str | None = None,
    first: int = 1,
) -> list[str]:
    """JPEG nén nhẹ. Không phóng to khung. Số luồng mặc định là hết lõi.

    first lớn hơn 1 là tách nối: bắt đầu từ đúng giây của khung đó, đánh số tiếp.
    """
    count = _ffmpeg_thread_count() if threads is None else threads
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", count]
    if first > 1:
        argv += ["-ss", f"{(first - 1) / rate:.3f}"]
    argv += [
        "-i",
        str(path),
        "-vf",
        f"fps={rate:.4f},scale=min(720\\,iw):-2,format=yuv420p",
        "-frames:v",
        str(max(1, _MAX_FRAMES - (first - 1))),
    ]
    if first > 1:
        argv += ["-start_number", str(first)]
    argv += ["-c:v", "mjpeg", "-q:v", "2", "-progress", "pipe:1", str(pattern)]
    return argv


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


def _frame_key(seconds: float) -> str:
    return f"{float(seconds):.3f}"


def _frame_images(work: Path) -> list[Path]:
    images: list[Path] = []
    for image in work.glob("f-*"):
        if image.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        if len(image.stem) > 2 and image.stem[2:].isdigit():
            images.append(image)
    return sorted(images)


def _work_dir(path: Path) -> Path:
    return path.with_name(path.name + "-frames")


def discard_video_work(path: Path) -> None:
    """Xóa video và khung đã tách sau khi ghi xong, hoặc khi bỏ tiến trình."""
    work = _work_dir(path)
    if work.is_dir():
        shutil.rmtree(work, ignore_errors=True)
    path.unlink(missing_ok=True)


def _saved_frames(work: Path, rate: float) -> list[tuple[float, Path]] | None:
    """Khung đã tách ở lần trước. Khác tốc độ mẫu thì tách lại."""
    marker = work / "extract.done"
    if not marker.is_file():
        return None
    try:
        saved_rate = float(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if abs(saved_rate - rate) > 0.0001:
        return None
    images = _frame_images(work)
    if not images:
        return None
    return [(index / saved_rate, image) for index, image in enumerate(images)]


def analyze_screen_video(
    path: Path,
    progress: ReadProgress | None = None,
    *,
    threads: str | None = None,
    reserve: int | None = None,
    keep_open: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Đọc chữ nhìn thấy và đề xuất người đủ ba cột. Chưa ghi vào bảng.

    keep_open dùng khi file vẫn đang dài thêm: đọc phần đã có, chưa chốt danh sách người.
    """
    sink = progress if progress is not None else ReadProgress()
    if not keep_open:
        staged = sink.staged_people()
        if staged is not None:
            sink.report(94, "Ghép tên")
            return [], list(staged)
    sink.report(8, "Đọc thời lượng")
    duration = _duration(path)
    rate = _sample_rate(duration)
    work = _work_dir(path)
    work.mkdir(parents=True, exist_ok=True)
    images = None if keep_open else _saved_frames(work, rate)
    if images is None:
        marker = work / "extract.done"
        marker.unlink(missing_ok=True)
        have = _earlier_frames(work, rate)
        images = _extract_frames(
            path, work, rate, duration, sink, threads=threads, partial=keep_open, have=have
        )
        if not images:
            if keep_open:
                return [], []
            raise ScreenVideoError("Video không có hình.")
        if not keep_open:
            marker.write_text(f"{rate:.6f}", encoding="utf-8")
    else:
        sink.report(40, "Tách khung hình")
    chosen = _changed_frames(images, sink)
    if not keep_open:
        sink.note_samples(_sample_previews(chosen))
    readings = _read_frames(chosen, sink, reserve=reserve)
    sink.report(94, "Ghép tên")
    frames = [(seconds, lines) for seconds, lines, _sightings in readings]
    sightings = [item for _seconds, _lines, found in readings for item in found]
    rows = propose_rows(sightings)
    counts = reading_counts(sightings)
    sink.note_tally(counts["contacts"], counts["accounts"], counts["saved"])
    if not keep_open:
        sink.stage_people(rows)
    return visible_steps(frames), rows


def read_screen_video(path: Path) -> list[dict[str, Any]]:
    """Sample a video, keep frames that change, and read the words on them."""
    steps, _people = analyze_screen_video(path)
    return steps


def _earlier_frames(work: Path, rate: float) -> int:
    """Số khung đợt trước đã tách ở cùng tốc độ, đánh số liền nhau. Không khớp thì xóa, tách lại từ đầu."""
    mark = work / _PARTIAL_MARK
    images = _frame_images(work)
    saved: float | None = None
    if mark.is_file():
        try:
            saved = float(mark.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            saved = None
    if saved is not None and abs(saved - rate) <= 0.0001:
        expected = [f"f-{index:05d}" for index in range(1, len(images) + 1)]
        if [image.stem for image in images] == expected:
            return len(images)
    for old in images:
        old.unlink(missing_ok=True)
    mark.unlink(missing_ok=True)
    return 0


def _sample_rate(duration: float | None) -> float:
    """Đọc 4 hình mỗi giây. Video dài thì dàn đều trong giới hạn khung."""
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
    threads: str | None = None,
    partial: bool = False,
    have: int = 0,
) -> list[tuple[float, Path]]:
    """Tách tiếp từ khung have+1. partial là file còn đang tải: bỏ khoảng một giây cuối để đợt sau tách lại."""
    pattern = work / "f-%05d.jpg"
    progress.report(12, "Tách khung hình")
    if have >= _MAX_FRAMES:
        return [(index / rate, image) for index, image in enumerate(_frame_images(work))]
    start = have / rate
    try:
        proc = subprocess.Popen(
            _ffmpeg_extract_command(path, pattern, rate, threads, first=have + 1),
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
                percent = 12 + int(min(1.0, (start + seconds) / duration) * 28)
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
    images = _frame_images(work)
    if partial:
        fresh = images[have:]
        trim = min(len(fresh), max(2, math.ceil(rate)))
        for old in fresh[len(fresh) - trim :]:
            old.unlink(missing_ok=True)
        images = images[: len(images) - trim]
        (work / _PARTIAL_MARK).write_text(f"{rate:.6f}", encoding="utf-8")
    else:
        (work / _PARTIAL_MARK).unlink(missing_ok=True)
        if proc.returncode != 0 and not images:
            raise ScreenVideoError("Không đọc được video.")
        if proc.returncode != 0:
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
        if previous is not None and not _frame_changed(previous, small):
            continue
        chosen.append((seconds, image))
        previous = small
    if unopened:
        progress.problem(f"{unopened} khung không mở được.")
    progress.report(47, "Chọn khung đổi")
    return chosen


def _frame_changed(previous: Image.Image, current: Image.Image) -> bool:
    diff = ImageChops.difference(previous, current)
    if ImageStat.Stat(diff).mean[0] >= _MIN_DIFF:
        return True
    width, height = diff.size
    blocks = diff.resize((max(1, width // 10), max(1, height // 10)), Image.Resampling.BOX)
    return blocks.getextrema()[1] >= _BLOCK_DIFF


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


def _preview_jpeg(path: Path) -> bytes | None:
    """Ảnh nhỏ để xem trên trang. Khung gốc vẫn để Tesseract đọc."""
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            if width > _PREVIEW_WIDTH:
                height = max(1, int(height * _PREVIEW_WIDTH / width))
                rgb = rgb.resize((_PREVIEW_WIDTH, height))
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=60)
            data = buf.getvalue()
    except OSError:
        return None
    if not data or len(data) > _PREVIEW_BYTES:
        return None
    return data


def _sample_previews(chosen: list[tuple[float, Path]]) -> list[bytes]:
    """Khung đầu, giữa, và cuối trong các khung thực sự được đọc."""
    if not chosen:
        return []
    indexes = [0]
    if len(chosen) > 2:
        indexes.append(len(chosen) // 2)
    if len(chosen) > 1:
        indexes.append(len(chosen) - 1)
    unique: list[int] = []
    for index in indexes:
        if index not in unique:
            unique.append(index)
    images: list[bytes] = []
    for index in unique[:_PREVIEW_CAP]:
        data = _preview_jpeg(chosen[index][1])
        if data:
            images.append(data)
    return images


def _line_words(lines: list[object]) -> int:
    total = 0
    for line in lines:
        text = getattr(line, "text", "")
        total += len(str(text).split())
    return total


def _read_one(item: tuple[float, Path]) -> tuple[float, list[str], list[dict[str, str]], int, int]:
    seconds, image = item
    seen = 0
    kept = 0
    try:
        text_lines = read_lines(image)
    except Exception:
        text_lines = None
    if text_lines is None:
        tsv = read_frame_tsv(image)
        text_lines = lines_from_tsv(tsv) if tsv else []
        if tsv:
            seen, kept = tsv_word_counts(tsv)
    else:
        seen = kept = _line_words(text_lines)
    sightings = sightings_from_lines(text_lines)
    captions = captions_from_sightings(sightings)
    if not captions:
        raw = "\n".join(line.text for line in text_lines)
        text = clean_ocr(raw)
        fallback = seen_line(text) if text else ""
        if fallback:
            captions = [fallback]
    return seconds, captions, sightings, seen, kept


def _read_frames(
    chosen: list[tuple[float, Path]],
    progress: ReadProgress,
    reserve: int | None = None,
) -> list[tuple[float, list[str], list[dict[str, str]]]]:
    if not chosen:
        progress.report(92, "Đọc chữ")
        return []
    total = len(chosen)
    known = progress.remembered()
    results: list[tuple[float, list[str], list[dict[str, str]]] | None] = [None] * total
    blank = 0
    failed = 0
    done_count = 0
    word_seen = 0
    word_kept = 0
    for index, (seconds, _image) in enumerate(chosen):
        saved = known.get(_frame_key(seconds))
        if saved is None:
            continue
        captions, sightings = saved
        if not captions and not sightings:
            blank += 1
        results[index] = (seconds, list(captions), [dict(item) for item in sightings])
        done_count += 1
    pending = [index for index, item in enumerate(results) if item is None]
    if done_count:
        progress.report(
            min(92, 48 + int((done_count / total) * 44)),
            f"Đọc tiếp, khung {done_count}/{total}",
        )
    else:
        progress.report(48, "Đọc chữ")
    if pending:
        workers = 1 if prefers_single_worker() else ocr_workers(len(pending), os.cpu_count() or 1, reserve)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_read_one, chosen[index]): index for index in pending}
            for future in as_completed(futures):
                index = futures[future]
                done_count += 1
                try:
                    reading = future.result()
                except Exception:
                    failed += 1
                    results[index] = (chosen[index][0], [], [])
                else:
                    seconds, captions, sightings, seen, kept = reading
                    word_seen += seen
                    word_kept += kept
                    if not captions and not sightings:
                        blank += 1
                    results[index] = (seconds, captions, sightings)
                    progress.remember_frame(seconds, captions, sightings)
                percent = 48 + int((done_count / total) * 44)
                label = "Đọc tiếp" if known else "Đọc chữ"
                progress.report(min(92, percent), f"{label}, khung {done_count}/{total}")
    if pending or not known:
        progress.note_words(word_seen, word_kept)
    if failed:
        progress.problem(f"{failed} khung không đọc được.")
    if blank == total:
        progress.note_blank()
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


def video_duration(path: Path) -> float | None:
    """Thời lượng theo giây. None khi không đọc được."""
    try:
        return _duration(path)
    except ScreenVideoError:
        return None


def cut_video_part(path: Path, dest: Path, start: float, end: float | None) -> bool:
    """Cắt một đoạn bằng copy, không nén lại. Chỉ giữ hình. Mục lục để đầu file để PC đọc khi còn đang tải."""
    if shutil.which("ffmpeg") is None:
        return False
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        argv += ["-ss", f"{start:.3f}"]
    argv += ["-i", str(path)]
    if end is not None:
        argv += ["-t", f"{max(0.5, end - start):.3f}"]
    argv += ["-map", "0:v:0", "-c", "copy", "-an", "-movflags", "+faststart", "-avoid_negative_ts", "make_zero", str(dest)]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=300, check=False, env=_media_env())
    except (OSError, subprocess.TimeoutExpired):
        dest.unlink(missing_ok=True)
        return False
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        return False
    return True


def faststart_video(path: Path) -> Path:
    """Đưa mục lục mp4 lên đầu để đọc được khi file mới tải một phần. Lỗi thì giữ file gốc."""
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        return path
    if shutil.which("ffmpeg") is None:
        return path
    dest = path.with_name(path.stem + "-fast.mp4")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        dest.unlink(missing_ok=True)
        return path
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size < 32:
        dest.unlink(missing_ok=True)
        return path
    try:
        dest.replace(path)
    except OSError:
        dest.unlink(missing_ok=True)
        return path
    return path
