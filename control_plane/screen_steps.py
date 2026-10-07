"""Read the names, accounts, and words visible in an iPhone screen recording."""

from __future__ import annotations

import bisect
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
from typing import Any, Callable, NamedTuple, TypeVar

from PIL import Image, ImageChops, ImageOps, ImageStat

from control_plane import layout_learn, scroll_track, stage_timing
from control_plane.gpu_read import fallback_note, prefers_single_worker, read_lines, read_lines_batch
from control_plane.read_vote import forget_scope
from control_plane.screen_people import (
    _accepted_sighting,
    _list_frame,
    captions_from_sightings,
    lines_from_tsv,
    memo_scope,
    prepare_frame_image,
    propose_rows,
    reading_counts,
    read_frame_tsv,
    read_frame_tsv_standard,
    sightings_from_lines,
    tighten_frame_reading,
    tsv_word_counts,
)

# 4 hình mỗi giây trên suốt video, đủ 3 giờ. Dài hơn thì dàn đều trong trần này.
_MAX_FRAMES = 3 * 60 * 60 * 4
# Khung chỉ nhích vài điểm ảnh thì bỏ. Một dòng chữ đổi (khoảng 3) vẫn được đọc.
_MIN_DIFF = 2.0
# Trang cùng bố cục mà chỉ đổi tên và tài khoản thì trung bình đổi chưa tới 1, nhưng ô 10x10 đổi mạnh nhất lên
# khoảng 29. Nhiễu nén, một điểm ảnh, hay cả khung dịch 1 điểm ảnh chỉ tới 3.
_BLOCK_DIFF = 12.0
# Rộng tối đa 720. Đo trên video iPhone thật: rộng 1080 đọc chậm hơn và nhận ra ít tên hơn.
# Lượt đầu 4 hình mỗi giây. Đoạn có chữ mà chưa ra tên thì đọc lại đúng đoạn đó ở 8 hình mỗi giây.
# Đoạn trống dài, xa mọi chữ, lượt đầu đã đọc rồi nên không tách lại.
_SAMPLE_FPS = 4.0
_REREAD_FPS = 8.0
# Một khung quá ngần này mà vẫn chưa đọc được thì bỏ khung đó và làm bước sau.
_FRAME_READ_SECONDS = 30.0
_FRAME_EXT = ".png"
# Đoạn ffmpeg song song. PC nhiều lõi thì tách nhiều hơn.
_SEGMENT_MIN_FRAMES = 64
# Cửa sổ cũ để nhận vùng danh bạ dày. Lượt đọc lại không dùng tốc độ này.
_BOOST_FPS = 12.0
_BOOST_WINDOW = 45.0
_BOOST_MIN_CONTACTS = 3
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

    def note_learned(self, text: str) -> None:
        """Quy luật video này đã dạy: dải @, dải tên, chiều cao dòng. Là thông tin, không phải vấn đề."""
        del text


def _segment_count() -> int:
    """Số đoạn ffmpeg. CONTROL_FFMPEG_SEGMENTS ghi đè. Mặc định theo số lõi."""
    raw = os.environ.get("CONTROL_FFMPEG_SEGMENTS", "").strip()
    if raw.isdigit():
        return max(1, min(16, int(raw)))
    cpus = os.cpu_count() or 4
    if cpus >= 16:
        parts = 8
    elif cpus >= 8:
        parts = 6
    else:
        parts = 4
    threads = os.environ.get("CONTROL_FFMPEG_THREADS", "").strip()
    if threads.isdigit() and int(threads) > 0:
        parts = min(parts, int(threads))
    return max(1, parts)


def ocr_workers(frame_count: int, cpu_count: int, reserve: int | None = None) -> int:
    """Số tiến trình Tesseract. Máy chủ giữ 5% lõi. PC giữ 20% suốt thời gian nối."""
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
    """0 là ffmpeg tự dùng hết lõi. Máy chủ đặt 95% lõi. PC đặt 80% lõi."""
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
    frames: int | None = None,
) -> list[str]:
    """PNG (hoặc JPEG q=1 nếu đường dẫn .jpg). Không phóng to khung.

    first lớn hơn 1 là tách nối: bắt đầu từ đúng giây của khung đó, đánh số tiếp.
    frames là số khung của đoạn này. Mặc định là phần còn lại trong giới hạn.
    """
    count = _ffmpeg_thread_count() if threads is None else threads
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", count]
    if first > 1:
        argv += ["-ss", f"{(first - 1) / rate:.3f}"]
    limit = max(1, _MAX_FRAMES - (first - 1)) if frames is None else max(1, frames)
    argv += [
        "-i",
        str(path),
        "-vf",
        _extract_filter(rate),
        "-frames:v",
        str(limit),
    ]
    if first > 1:
        argv += ["-start_number", str(first)]
    argv += [*_frame_encode_args(pattern), "-progress", "pipe:1", str(pattern)]
    return argv


def _extract_filter(rate: float) -> str:
    return f"fps={rate:.4f},scale=min(720\\,iw):-2"


def _frame_encode_args(pattern: Path) -> list[str]:
    """PNG nén mức 1: từng điểm ảnh y hệt mức mặc định, ffmpeg tốn ít CPU hơn khoảng 40%. Đường .jpg cũ thì JPEG chất lượng 1."""
    if pattern.suffix.lower() in {".jpg", ".jpeg"}:
        return ["-c:v", "mjpeg", "-q:v", "1"]
    return ["-compression_level", "1"]


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
    forget_scope(str(work))
    layout_learn.forget_scope(str(work))
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


class _AddWords(ReadProgress):
    """Cộng số từ của từng đoạn, và chỉ báo video không có chữ khi mọi đoạn đều trống."""

    def __init__(self, inner: ReadProgress) -> None:
        self._inner = inner
        self.seen = 0
        self.kept = 0
        self.blank = False

    def report(self, percent: int, task: str) -> None:
        self._inner.report(percent, task)

    def problem(self, text: str) -> None:
        if text == "Không đọc được chữ trên video.":
            self.blank = True
            return
        self._inner.problem(text)

    def remembered(self) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
        return self._inner.remembered()

    def remember_frame(self, seconds: float, captions: list[str], sightings: list[dict[str, str]]) -> None:
        self._inner.remember_frame(seconds, captions, sightings)

    def staged_people(self) -> list[dict[str, str]] | None:
        return self._inner.staged_people()

    def stage_people(self, people: list[dict[str, str]]) -> None:
        self._inner.stage_people(people)

    def note_tally(self, contacts: int, accounts: int, saved: int) -> None:
        self._inner.note_tally(contacts, accounts, saved)

    def note_words(self, seen: int, kept: int) -> None:
        self.seen += max(0, int(seen))
        self.kept += max(0, int(kept))

    def note_samples(self, images: list[bytes]) -> None:
        self._inner.note_samples(images)

    def note_blank(self) -> None:
        self.blank = True


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
    words = _AddWords(sink)
    if images is None:
        marker = work / "extract.done"
        marker.unlink(missing_ok=True)
        have = _earlier_frames(work, rate)
        chosen = []
        readings = []
        held: list[Image.Image] = []
        produced = have > 0
        for batch in _iter_segments(
            path, work, rate, duration, sink, threads=threads, partial=keep_open, have=have
        ):
            if batch:
                produced = True
            part = _drop_fades(_changed_frames(batch, sink, held))
            chosen.extend(part)
            if not part:
                continue
            lo, hi = _read_span(part, duration)
            readings.extend(
                _read_frames(
                    part,
                    words,
                    reserve=reserve,
                    percent_lo=lo,
                    percent_hi=hi,
                    label=_read_label(part, duration, continued=keep_open),
                )
            )
        if not produced:
            if keep_open:
                return [], []
            raise ScreenVideoError("Video không có hình.")
        if not keep_open:
            marker.write_text(f"{rate:.6f}", encoding="utf-8")
    else:
        sink.report(40, "Tách khung hình")
        chosen = _drop_fades(_changed_frames(images, sink))
        if chosen:
            lo, hi = _read_span(chosen, duration)
            readings = _read_frames(
                chosen,
                words,
                reserve=reserve,
                percent_lo=lo,
                percent_hi=hi,
                label=_read_label(chosen, duration),
            )
        else:
            readings = []
    if not keep_open:
        sink.note_samples(_sample_previews(chosen))
    seen_keys = {_frame_key(seconds) for seconds, _captions, _found in readings}
    for key, (captions, sightings) in sink.remembered().items():
        if key in seen_keys:
            continue
        readings.append((float(key), list(captions), [dict(item) for item in sightings]))
    readings.sort(key=lambda item: item[0])
    if not keep_open and readings:
        readings = _reread_unread(
            path,
            work,
            readings,
            sink,
            threads=threads,
            reserve=reserve,
        )
    sink.note_words(words.seen, words.kept)
    if not keep_open:
        learned = layout_learn.learner_for(str(work)).describe()
        if learned:
            sink.note_learned(learned)
    if words.blank and readings and all(not captions and not found for _seconds, captions, found in readings):
        sink.note_blank()
        sink.problem("Không đọc được chữ trên video.")
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
    """4 hình mỗi giây trên suốt video. Video dài hơn trần khung thì dàn đều."""
    if duration is None or duration <= 0:
        return _SAMPLE_FPS
    if duration * _SAMPLE_FPS <= _MAX_FRAMES:
        return _SAMPLE_FPS
    return _MAX_FRAMES / duration


def _read_span(frames: list[tuple[float, Path]], duration: float | None) -> tuple[int, int]:
    """Đặt đoạn khung này vào dải 48–92 theo vị trí trên cả video."""
    if not frames:
        return 48, 48
    start = min(seconds for seconds, _image in frames)
    end = max(seconds for seconds, _image in frames)
    whole = duration if duration is not None and duration > 0 else max(end, 0.001)
    width = 92 - 48

    def place(seconds: float) -> int:
        ratio = min(1.0, max(0.0, seconds / whole))
        return 48 + int(ratio * width)

    lo = place(start)
    hi = place(end)
    if hi <= lo:
        hi = min(92, lo + 1)
    return lo, hi


def _read_label(frames: list[tuple[float, Path]], duration: float | None, continued: bool = False) -> str:
    """Nhãn đọc chữ kèm phút hiện tại trên tổng số phút của video."""
    prefix = "Đọc tiếp" if continued else "Đọc chữ"
    if not frames:
        return prefix
    end = max(seconds for seconds, _image in frames)
    minute = int(end // 60)
    if duration is not None and duration > 0:
        total = max(1, math.ceil(duration / 60))
    else:
        total = max(1, minute)
    return f"{prefix}, phút {minute}/{total}"


def _frame_has_person(found: list[dict[str, str]]) -> bool:
    for item in found:
        if _accepted_sighting(item) is not None:
            return True
    return False


def _within_second(seconds: float, anchors: list[float]) -> bool:
    """True khi mốc gần nhất nằm trong một giây. anchors đã sắp tăng dần."""
    if not anchors:
        return False
    index = bisect.bisect_left(anchors, seconds)
    if index < len(anchors) and anchors[index] - seconds <= 1.0:
        return True
    return index > 0 and seconds - anchors[index - 1] <= 1.0


def _unread_windows(readings: list[tuple[float, list[str], list[dict[str, str]]]]) -> list[tuple[float, float]]:
    """Đoạn chưa ra tên, nới một giây ở hai đầu.

    Khung có chữ mà chưa ra người vẫn đọc lại ở 8 hình mỗi giây. Khung không có
    chữ chỉ đọc lại khi cách khung có chữ hoặc đã ra người không quá một giây.
    Đoạn trống dài không tách lại: lượt 4 hình mỗi giây đã đọc chúng, đọc lại
    chỉ để bắt tên lọt giữa hai khung có nội dung.
    """
    if not readings:
        return []
    ordered = sorted(readings, key=lambda item: item[0])
    anchors = [seconds for seconds, captions, found in ordered if captions or found]
    leads: list[float] = []
    for seconds, captions, found in ordered:
        if _frame_has_person(found):
            continue
        if captions or found or _within_second(seconds, anchors):
            leads.append(seconds)
    if not leads:
        return []
    windows: list[tuple[float, float]] = []
    start = previous = leads[0]
    for seconds in leads[1:]:
        if seconds - previous <= 1.0:
            previous = seconds
            continue
        windows.append((max(0.0, start - 1.0), previous + 1.0))
        start = previous = seconds
    windows.append((max(0.0, start - 1.0), previous + 1.0))
    return windows


def _contact_keys_between(
    readings: list[tuple[float, list[str], list[dict[str, str]]]],
    lo: float,
    hi: float,
) -> set[str]:
    keys: set[str] = set()
    for seconds, _captions, found in readings:
        if seconds < lo or seconds > hi:
            continue
        for item in found:
            accepted = _accepted_sighting(item)
            if accepted is not None and accepted[1] == "contact":
                keys.add(accepted[0])
    return keys


def _dense_contact_windows(
    readings: list[tuple[float, list[str], list[dict[str, str]]]],
) -> list[tuple[float, float]]:
    """Cửa sổ 45 giây có ít nhất 3 tên danh bạ khác nhau."""
    if not readings:
        return []
    half = _BOOST_WINDOW / 2.0
    candidates: list[tuple[float, float]] = []
    seen_centers: set[int] = set()
    for seconds, _captions, _found in readings:
        center = int(seconds)
        if center in seen_centers:
            continue
        seen_centers.add(center)
        lo = max(0.0, seconds - half)
        hi = seconds + half
        if len(_contact_keys_between(readings, lo, hi)) >= _BOOST_MIN_CONTACTS:
            candidates.append((lo, hi))
    candidates.sort(key=lambda item: item[0])
    merged: list[tuple[float, float]] = []
    for lo, hi in candidates:
        if not merged or lo > merged[-1][1] + 1.0:
            merged.append((lo, hi))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
    return merged[:6]


def _ffmpeg_extract_range(
    path: Path,
    pattern: Path,
    rate: float,
    start_sec: float,
    end_sec: float,
    threads: str | None = None,
) -> list[str]:
    count = _ffmpeg_thread_count() if threads is None else threads
    duration = max(0.5, end_sec - start_sec)
    limit = min(_MAX_FRAMES, max(1, int(math.ceil(duration * rate))))
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        count,
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-vf",
        _extract_filter(rate),
        "-frames:v",
        str(limit),
        "-start_number",
        "1",
        *_frame_encode_args(pattern),
        str(pattern),
    ]


def _merge_readings(
    readings: list[tuple[float, list[str], list[dict[str, str]]]],
    extra: list[tuple[float, list[str], list[dict[str, str]]]],
) -> list[tuple[float, list[str], list[dict[str, str]]]]:
    by_key: dict[str, tuple[float, list[str], list[dict[str, str]]]] = {}
    for seconds, captions, found in readings + extra:
        key = _frame_key(seconds)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = (seconds, list(captions), [dict(item) for item in found])
            continue
        _prev_sec, prev_caps, prev_found = prev
        merged_found = list(prev_found)
        signatures = {repr(sorted(item.items())) for item in prev_found}
        for item in found:
            sig = repr(sorted(item.items()))
            if sig not in signatures:
                merged_found.append(dict(item))
                signatures.add(sig)
        merged_caps = list(prev_caps)
        for caption in captions:
            if caption not in merged_caps:
                merged_caps.append(caption)
        by_key[key] = (seconds, merged_caps, merged_found)
    return sorted(by_key.values(), key=lambda item: item[0])


class _HoldPercent(ReadProgress):
    """Giữ thanh không tụt khi tách lại đoạn hỏng. Việc đó vẫn là bước đọc lại."""

    def __init__(self, inner: ReadProgress, floor: int, task: str) -> None:
        self._inner = inner
        self._floor = floor
        self._task = task

    def report(self, percent: int, task: str) -> None:
        if int(percent) < self._floor:
            self._inner.report(self._floor, self._task)
            return
        self._inner.report(int(percent), task)

    def problem(self, text: str) -> None:
        self._inner.problem(text)


def _reread_unread(
    path: Path,
    work: Path,
    readings: list[tuple[float, list[str], list[dict[str, str]]]],
    progress: ReadProgress,
    *,
    threads: str | None = None,
    reserve: int | None = None,
) -> list[tuple[float, list[str], list[dict[str, str]]]]:
    """Đọc lại 8 hình mỗi giây ở đoạn có chữ mà chưa ra người, và một giây sát đó.

    Đoạn trống dài không tách lại. Lượt 4 hình mỗi giây đã đọc những khung đó.
    """
    windows = _unread_windows(readings)
    if not windows:
        return readings
    known = {_frame_key(seconds) for seconds, _captions, _found in readings}
    extra: list[tuple[float, list[str], list[dict[str, str]]]] = []
    words = _AddWords(progress)
    used = 0
    for index, (start, end) in enumerate(windows):
        task = f"Đọc lại đoạn chưa ra chữ, 8 hình/giây, đoạn {index + 1}/{len(windows)}"
        held_bar = _HoldPercent(progress, 93, task)
        held_bar.report(93, task)
        sub = work / f"reread-{index}"
        sub.mkdir(parents=True, exist_ok=True)
        for old in sub.glob("r-*.*"):
            if old.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                old.unlink(missing_ok=True)
        pattern = sub / f"r-%05d{_FRAME_EXT}"
        argv = _ffmpeg_extract_range(path, pattern, _REREAD_FPS, start, end, threads)
        code = _run_ffmpeg(argv, end - start, held_bar, start)
        boosted = sorted(item for item in sub.glob("r-*.*") if item.suffix.lower() in {".png", ".jpg", ".jpeg"})
        if code != 0 and not boosted:
            continue
        timed: list[tuple[float, Path]] = []
        for image in boosted:
            try:
                number = int(image.stem.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            seconds = start + (number - 1) / _REREAD_FPS
            if _frame_key(seconds) in known:
                continue
            timed.append((seconds, image))
        if not timed:
            continue
        used += 1
        held: list[Image.Image] = []
        chosen = _changed_frames(timed, held_bar, held)
        extra.extend(
            _read_frames(
                chosen,
                words,
                reserve=reserve,
                percent_lo=93,
                percent_hi=93,
                label="Đọc lại đoạn chưa ra chữ, 8 hình/giây",
            )
        )
    if not extra:
        return readings
    progress.problem(f"Đọc lại {used} đoạn chưa ra tên ở 8 khung/giây.")
    return _merge_readings(readings, extra)


def _apply_dense_boost(
    path: Path,
    work: Path,
    readings: list[tuple[float, list[str], list[dict[str, str]]]],
    base_rate: float,
    progress: ReadProgress,
    *,
    threads: str | None = None,
    reserve: int | None = None,
) -> list[tuple[float, list[str], list[dict[str, str]]]]:
    windows = _dense_contact_windows(readings)
    if not windows or abs(_BOOST_FPS - base_rate) < 0.01:
        return readings
    boost_rate = _BOOST_FPS
    extra: list[tuple[float, list[str], list[dict[str, str]]]] = []
    words = _AddWords(progress)
    for index, (start, end) in enumerate(windows):
        sub = work / f"boost-{index}"
        sub.mkdir(parents=True, exist_ok=True)
        for old in sub.glob("b-*.*"):
            if old.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                old.unlink(missing_ok=True)
        pattern = sub / f"b-%05d{_FRAME_EXT}"
        argv = _ffmpeg_extract_range(path, pattern, boost_rate, start, end, threads)
        code = _run_ffmpeg(argv, end - start, progress, start)
        boosted = sorted(path for path in sub.glob("b-*.*") if path.suffix.lower() in {".png", ".jpg", ".jpeg"})
        if code != 0 and not boosted:
            continue
        timed: list[tuple[float, Path]] = []
        for image in boosted:
            try:
                number = int(image.stem.split("-", 1)[1])
            except (IndexError, ValueError):
                continue
            timed.append((start + (number - 1) / boost_rate, image))
        if not timed:
            continue
        held: list[Image.Image] = []
        chosen = _changed_frames(timed, progress, held)
        extra.extend(_read_frames(chosen, words, reserve=reserve))
    if not extra:
        return readings
    progress.problem(f"Đọc thêm {len(windows)} vùng danh bạ dày ở {int(boost_rate)} khung/giây.")
    return _merge_readings(readings, extra)


def _media_env() -> dict[str, str]:
    """ffmpeg dùng hết lõi. Giới hạn một luồng chỉ dành cho từng bộ đọc chữ."""
    env = os.environ.copy()
    env.pop("OMP_THREAD_LIMIT", None)
    return env


def _duration(path: Path) -> float | None:
    duration, _bitrate = _media_facts(path)
    return duration


def _media_facts(path: Path) -> tuple[float | None, int]:
    """Thời lượng theo giây và bitrate. Bitrate 0 khi không đọc được."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=0",
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
    duration: float | None = None
    bitrate = 0
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key == "duration" and value and value.upper() != "N/A":
            try:
                duration = float(value)
            except ValueError:
                duration = None
        elif key == "bit_rate" and value.isdigit():
            bitrate = int(value)
    return duration, bitrate


def _readable_seconds(path: Path, duration: float | None, partial: bool, bitrate: int) -> float | None:
    """Giây đã có đủ dữ liệu để tách. File còn đang tải thì chừa 15% theo bitrate, kẻo tua vào khúc chưa tới."""
    if duration is None or duration <= 0:
        return None
    if not partial:
        return duration
    if bitrate <= 0:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    estimated = size * 8 / bitrate
    return min(duration, max(0.0, estimated * 0.85))


def _segment_ranges(total: int, have: int) -> list[tuple[int, int]]:
    """Khoảng khung 1-based còn phải tách. Phần còn lại ngắn thì một tiến trình."""
    if total <= have:
        return []
    if total - have < _SEGMENT_MIN_FRAMES:
        return [(have + 1, total)]
    ranges: list[tuple[int, int]] = []
    parts = _segment_count()
    for index in range(parts):
        start = index * total // parts + 1
        end = (index + 1) * total // parts
        start = max(start, have + 1)
        if end >= start:
            ranges.append((start, end))
    return ranges


def _segment_threads(threads: str | None, parts: int) -> str | None:
    """Chia số luồng cho từng đoạn. 0 là ffmpeg tự chia, giữ nguyên cho mỗi đoạn."""
    raw = _ffmpeg_thread_count() if threads is None else threads
    if parts <= 1 or not raw.isdigit() or int(raw) <= 0:
        return threads
    return str(max(1, int(raw) // parts))


def _numbered_frames(images: list[Path], rate: float) -> list[tuple[float, Path]]:
    timed: list[tuple[float, Path]] = []
    for image in images:
        try:
            number = int(image.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        timed.append(((number - 1) / rate, image))
    return timed


def _run_ffmpeg(
    argv: list[str],
    duration: float | None,
    progress: ReadProgress,
    origin: float,
) -> int:
    """Chạy một ffmpeg tách khung và cộng giờ vào đồng hồ ffmpeg."""
    with stage_timing.timed("ffmpeg"):
        return _run_ffmpeg_process(argv, duration, progress, origin)


def _run_ffmpeg_process(
    argv: list[str],
    duration: float | None,
    progress: ReadProgress,
    origin: float,
) -> int:
    """Chạy một ffmpeg tách khung. origin là giây của khung đầu đoạn, để phần trăm tính trên cả video."""
    try:
        proc = subprocess.Popen(
            argv,
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
                percent = 12 + int(min(1.0, (origin + seconds) / duration) * 28)
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
    return proc.returncode if proc.returncode is not None else 1


def _numbered_frame(work: Path, number: int) -> Path | None:
    for ext in (_FRAME_EXT, ".png", ".jpg", ".jpeg"):
        image = work / f"f-{number:05d}{ext}"
        if image.is_file():
            return image
    return None


def _files_in_range(work: Path, start: int, end: int) -> list[Path]:
    found: list[Path] = []
    for number in range(start, end + 1):
        image = _numbered_frame(work, number)
        if image is not None:
            found.append(image)
    return found


def _trim_tail(images: list[Path], rate: float) -> list[Path]:
    trim = min(len(images), max(2, math.ceil(rate)))
    for old in images[len(images) - trim :]:
        old.unlink(missing_ok=True)
    return images[: len(images) - trim]


def _iter_segments(
    path: Path,
    work: Path,
    rate: float,
    duration: float | None,
    progress: ReadProgress,
    threads: str | None = None,
    partial: bool = False,
    have: int = 0,
) -> Any:
    """Tách các đoạn còn thiếu. Đoạn sau tách trong lúc đoạn trước đang được đọc chữ."""
    pattern = work / f"f-%05d{_FRAME_EXT}"
    progress.report(12, "Tách khung hình")
    if have >= _MAX_FRAMES:
        return
    _known, bitrate = _media_facts(path)
    whole = duration if duration is not None else _known
    readable = _readable_seconds(path, whole, partial, bitrate)
    ranges: list[tuple[int, int]] = []
    if readable is not None and readable > 0:
        total = min(_MAX_FRAMES, max(have, math.ceil(readable * rate - 1e-3)))
        ranges = _segment_ranges(total, have)
    if len(ranges) <= 1:
        code = _run_ffmpeg(
            _ffmpeg_extract_command(path, pattern, rate, threads, first=have + 1),
            whole,
            progress,
            have / rate,
        )
        images = _frame_images(work)
        fresh = images[have:]
        if partial:
            fresh = _trim_tail(fresh, rate)
            (work / _PARTIAL_MARK).write_text(f"{rate:.6f}", encoding="utf-8")
        else:
            (work / _PARTIAL_MARK).unlink(missing_ok=True)
            if code != 0 and not images:
                raise ScreenVideoError("Không đọc được video.")
            if code != 0:
                progress.problem("Tách hình dừng sớm.")
        if fresh or images:
            progress.report(40, "Tách khung hình")
        yield _numbered_frames(fresh, rate)
        return
    results: list[list[Path] | BaseException | None] = [None] * len(ranges)
    finished: queue.Queue[int] = queue.Queue()
    segment_threads = _segment_threads(threads, len(ranges))

    def _run(index: int, start: int, end: int) -> None:
        try:
            code = _run_ffmpeg(
                _ffmpeg_extract_command(
                    path,
                    pattern,
                    rate,
                    segment_threads,
                    first=start,
                    frames=end - start + 1,
                ),
                whole,
                progress,
                (start - 1) / rate,
            )
            files = _files_in_range(work, start, end)
            if code != 0 and not files:
                results[index] = []
            else:
                if code != 0:
                    progress.problem("Tách hình dừng sớm.")
                results[index] = files
        except BaseException as error:  # noqa: BLE001 - đưa lỗi về luồng gọi để không mất các đoạn khác
            results[index] = error
        finished.put(index)

    workers = [
        threading.Thread(target=_run, args=(index, start, end), daemon=True)
        for index, (start, end) in enumerate(ranges)
    ]
    for worker in workers:
        worker.start()
    sent = 0
    try:
        while sent < len(ranges):
            finished.get()
            while sent < len(ranges) and results[sent] is not None:
                item = results[sent]
                last = sent == len(ranges) - 1
                sent += 1
                if isinstance(item, BaseException):
                    if sent == 1 and have == 0:
                        raise item
                    progress.problem("Tách hình dừng sớm.")
                    yield []
                    continue
                files = list(item)
                if last and partial:
                    files = _trim_tail(files, rate)
                    (work / _PARTIAL_MARK).write_text(f"{rate:.6f}", encoding="utf-8")
                elif last:
                    (work / _PARTIAL_MARK).unlink(missing_ok=True)
                if files:
                    progress.report(40, "Tách khung hình")
                yield _numbered_frames(files, rate)
    finally:
        for worker in workers:
            worker.join(timeout=2)


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
    for _batch in _iter_segments(
        path, work, rate, duration, progress, threads=threads, partial=partial, have=have
    ):
        pass
    return [(index / rate, image) for index, image in enumerate(_frame_images(work))]


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


def _changed_frames(
    images: list[tuple[float, Path]],
    progress: ReadProgress,
    held: list[Image.Image] | None = None,
) -> list[tuple[float, Path]]:
    chosen: list[tuple[float, Path]] = []
    previous: Image.Image | None = held[0] if held else None
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
    if held is not None and previous is not None:
        held[:] = [previous]
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
    with stage_timing.timed("thumb"):
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


def _suspicious_read(seen: int, kept: int, captions: list[str], sightings: list[dict[str, str]]) -> bool:
    if seen <= 0:
        return not captions and not sightings
    ratio = kept / seen
    if seen >= 8 and ratio < 0.12:
        return True
    if not sightings and seen >= 15:
        return True
    if not captions and not sightings and seen >= 3:
        return True
    return False


def _prefer_reading(
    text_lines: list[Any],
    sightings: list[dict[str, str]],
    captions: list[str],
    seen: int,
    kept: int,
    candidate_lines: list[Any],
    candidate_sightings: list[dict[str, str]],
    candidate_captions: list[str],
    candidate_seen: int,
    candidate_kept: int,
) -> tuple[list[Any], list[dict[str, str]], list[str], int, int]:
    if candidate_kept > kept:
        return candidate_lines, candidate_sightings, candidate_captions, candidate_seen, candidate_kept
    if len(candidate_sightings) > len(sightings):
        return candidate_lines, candidate_sightings, candidate_captions, candidate_seen, candidate_kept
    if candidate_captions and not captions:
        return candidate_lines, candidate_sightings, candidate_captions, candidate_seen, candidate_kept
    return text_lines, sightings, captions, seen, kept


class _FrameRead(NamedTuple):
    seconds: float
    captions: list[str]
    sightings: list[dict[str, str]]
    seen: int
    kept: int
    lines: list[Any]
    height: int
    strip: bool
    timed_out: bool = False


def _drop_fades(chosen: list[tuple[float, Path]]) -> list[tuple[float, Path]]:
    """Bỏ khung mờ lúc chuyển trang hồ sơ. Danh bạ đang cuộn giữ nguyên."""
    if len(chosen) < 3:
        return chosen
    thumbs = [scroll_track.thumb(path) for _seconds, path in chosen]
    drop = set(scroll_track.fade_indexes(thumbs))
    if not drop:
        return chosen
    stage_timing.bump("scroll.fades", len(drop))
    return [item for index, item in enumerate(chosen) if index not in drop]


def _try_strip(item: tuple[float, Path], thumb_dy: int, *, seconds: float | None = None) -> _FrameRead | None:
    """Đọc dải mới khi danh bạ cuộn. None thì khung này đọc cả ảnh như thường."""
    seconds_left = None if seconds is None else max(0.0, seconds)
    if seconds_left is not None and seconds_left <= 0.05:
        return None
    seconds, image = item
    read_started = time.perf_counter()
    loaded = prepare_frame_image(image)
    if loaded is None or thumb_dy == 0:
        return None
    dy = scroll_track.prepared_dy(thumb_dy, loaded.height)
    top, bottom = scroll_track.strip_bounds(loaded.height, dy)
    if dy == 0 or bottom - top < 8 or bottom - top >= int(loaded.height * 0.85):
        return None
    crop = loaded.crop((0, top, loaded.width, bottom))
    tsv = scroll_track.offset_tsv(read_frame_tsv(image, crop, seconds=seconds_left), top)
    text_lines = lines_from_tsv(tsv) if tsv else []
    stage_timing.add("read", time.perf_counter() - read_started)
    if not text_lines:
        return None
    seen, kept = tsv_word_counts(tsv)
    with stage_timing.timed("vote"):
        text_lines, sightings = tighten_frame_reading(
            image,
            list(text_lines),
            [],
            tsv=tsv,
            prepared=True,
            loaded=loaded,
            scroll_list=True,
        )
    if not text_lines:
        return None
    captions = captions_from_sightings(sightings)
    return _FrameRead(seconds, captions, sightings, seen, kept, list(text_lines), loaded.height, True)


_T = TypeVar("_T")


class _StillReading:
    """Lần đọc chưa xong khi hết giờ của khung."""


_STILL_READING = _StillReading()


def _finish_in(seconds: float, work: Callable[[], _T]) -> _T | _StillReading:
    """Chạy work. Hết giờ thì trả về ngay; lần đọc đó không được dùng nữa."""
    if seconds <= 0.05:
        return _STILL_READING
    box: list[_T | Exception] = []
    done = threading.Event()

    def run() -> None:
        try:
            box.append(work())
        except Exception as exc:
            box.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, name="frame-read", daemon=True).start()
    if not done.wait(seconds):
        return _STILL_READING
    if not box:
        return _STILL_READING
    outcome = box[0]
    if isinstance(outcome, Exception):
        raise outcome
    return outcome


def _caption_list(lines: list[Any], found: list[dict[str, str]]) -> list[str]:
    captions = captions_from_sightings(found)
    if captions:
        return captions
    raw = "\n".join(line.text for line in lines)
    text = clean_ocr(raw)
    fallback = seen_line(text) if text else ""
    return [fallback] if fallback else []


def _read_one(
    item: tuple[float, Path],
    thumb_dy: int | None = None,
    preset_lines: list[Any] | None = None,
    *,
    use_preset: bool = False,
    clock: Callable[[], float] | None = None,
) -> _FrameRead:
    """Đọc một khung. Quá 30 giây mà chưa ra chữ thì trả khung trống, không đọc thêm."""
    now = clock or time.monotonic
    started = now()
    stamp, image = item

    def left() -> float:
        return _FRAME_READ_SECONDS - (now() - started)

    def give_up() -> _FrameRead:
        return _FrameRead(stamp, [], [], 0, 0, [], 0, False, True)

    if thumb_dy:
        if left() <= 0.05:
            return give_up()
        stripped = _finish_in(left(), lambda: _try_strip(item, thumb_dy, seconds=left()))
        if stripped is _STILL_READING:
            return give_up()
        if stripped is not None:
            return stripped
        if left() <= 0.05:
            return give_up()
    seen = 0
    kept = 0
    used_tesseract = False
    tsv = ""
    loaded: Image.Image | None = None
    read_started = time.perf_counter()
    if use_preset:
        text_lines = preset_lines
    else:
        if left() <= 0.05:
            return give_up()
        try:
            text_lines = _finish_in(left(), lambda: read_lines(image))
        except Exception:
            text_lines = None
        if text_lines is _STILL_READING:
            return give_up()
    if text_lines is None:
        if left() <= 0.05:
            return give_up()
        used_tesseract = True
        loaded = prepare_frame_image(image)
        tsv_read = _finish_in(
            left(),
            lambda: read_frame_tsv(image, loaded, seconds=max(0.0, left())),
        )
        if tsv_read is _STILL_READING:
            return give_up()
        tsv = tsv_read or ""
        text_lines = lines_from_tsv(tsv) if tsv else []
        if tsv:
            seen, kept = tsv_word_counts(tsv)
    else:
        seen = kept = _line_words(text_lines or [])
    text_lines = list(text_lines or [])
    sightings = sightings_from_lines(text_lines)
    captions = _caption_list(text_lines, sightings)
    if left() <= 0.05 and not captions and not sightings:
        return give_up()
    if used_tesseract and _suspicious_read(seen, kept, captions, sightings) and left() > 0.05:
        std_read = _finish_in(
            left(),
            lambda: read_frame_tsv_standard(image, loaded, seconds=max(0.0, left())),
        )
        if std_read is _STILL_READING:
            if not captions and not sightings:
                return give_up()
        elif std_read:
            std_lines = lines_from_tsv(std_read)
            std_seen, std_kept = tsv_word_counts(std_read)
            std_sightings = sightings_from_lines(std_lines)
            std_captions = _caption_list(std_lines, std_sightings)
            chosen = _prefer_reading(
                text_lines,
                sightings,
                captions,
                seen,
                kept,
                std_lines,
                std_sightings,
                std_captions,
                std_seen,
                std_kept,
            )
            if chosen[0] is std_lines:
                tsv = std_read
            text_lines, sightings, captions, seen, kept = chosen
    if left() <= 0.05 and not captions and not sightings:
        return give_up()
    stage_timing.add("read", time.perf_counter() - read_started)
    if left() > 0.05:
        with stage_timing.timed("vote"):
            tightened = _finish_in(
                left(),
                lambda: tighten_frame_reading(
                    image,
                    list(text_lines),
                    sightings,
                    tsv=tsv,
                    prepared=used_tesseract,
                    loaded=loaded,
                ),
            )
        if tightened is _STILL_READING:
            if not captions and not sightings:
                return give_up()
        else:
            text_lines, sightings = tightened
            captions = _caption_list(text_lines, sightings)
    height = loaded.height if loaded is not None else 0
    return _FrameRead(stamp, captions, sightings, seen, kept, list(text_lines), height, False, False)


def _apply_scroll(
    chosen: list[tuple[float, Path]],
    reads: list[_FrameRead | None],
    shifts: list[scroll_track.Shift],
) -> None:
    """Ghép dòng đã đọc của khung trước vào dải mới. Không có dòng cũ thì đọc lại cả khung."""
    carried: list[Any] = []
    prev_list = False
    for index, item in enumerate(reads):
        if item is None:
            prev_list = False
            carried = []
            continue
        if item.strip and not (prev_list and carried and shifts[index].confident):
            try:
                item = _read_one((item.seconds, chosen[index][1]))
            except Exception:
                pass
            else:
                reads[index] = item
        lines = list(item.lines)
        captions = list(item.captions)
        sightings = [dict(found) for found in item.sightings]
        if item.strip and prev_list and carried and item.height > 0 and shifts[index].confident:
            dy = scroll_track.prepared_dy(shifts[index].dy, item.height)
            top, bottom = scroll_track.strip_bounds(item.height, dy)
            kept = [
                line
                for line in scroll_track.shift_lines(carried, dy, item.height)
                if scroll_track.in_safe(line, item.height) and scroll_track.outside_strip(line, top, bottom)
            ]
            fresh = [line for line in item.lines if not any(scroll_track.overlaps(line, old) for old in kept)]
            lines = sorted([*kept, *fresh], key=lambda line: (line.top, line.left))
            sightings = sightings_from_lines(lines)
            captions = captions_from_sightings(sightings)
            stage_timing.bump("scroll.strips")
            item = item._replace(captions=captions, sightings=sightings, lines=lines)
            reads[index] = item
        prev_list = _list_frame(lines) or any(found.get("kind") == "contact" for found in sightings)
        carried = []
        if prev_list and item.height:
            carried = [line for line in lines if scroll_track.in_safe(line, item.height)]


def _rescue_empty(
    chosen: list[tuple[float, Path]],
    results: list[tuple[float, list[str], list[dict[str, str]]] | None],
    progress: ReadProgress,
    skip: set[int] | None = None,
) -> tuple[int, int]:
    """Khung đã đọc mà không thấy ai, nhất là các trang hồ sơ đọc sớm khi video chưa dạy dải @.

    Dải học được ở cuối lượt này đọc lại được chúng. Mỗi khung chỉ được cứu một lần trong cả video.
    Trả số khung cứu được và số khung trong đó trước đó hoàn toàn trống.
    """
    if not chosen:
        return 0, 0
    learner = layout_learn.learner_for(memo_scope(chosen[0][1]))
    if learner.handle_zone() is None:
        return 0, 0
    rescued = 0
    unblanked = 0
    skipped = skip or set()
    for index, item in enumerate(results):
        if item is None or index in skipped:
            continue
        seconds, captions, sightings = item
        # Khung trống, hoặc khung chỉ có hồ sơ đọc lúc video chưa dạy dải: đọc lại bằng dải. Khung danh bạ giữ nguyên.
        if any(found.get("kind") != "profile" for found in sightings):
            continue
        path = chosen[index][1]
        loaded = prepare_frame_image(path)
        if loaded is None:
            continue
        _lines, found = tighten_frame_reading(path, [], [], tsv="", loaded=loaded)
        if not found:
            continue
        better = captions_from_sightings(found)
        results[index] = (seconds, better, found)
        progress.remember_frame(seconds, better, found)
        rescued += 1
        unblanked += 0 if captions or sightings else 1
    return rescued, unblanked


# Đọc lại cả khung theo nhịp này. Lệch đo được còn sai khoảng một điểm ảnh, cộng dồn sẽ kéo rời hai dòng của một người.
_SCROLL_ANCHOR = 5


def _strip_dy(index: int, shift: scroll_track.Shift) -> int | None:
    """Lệch để đọc dải. Khung mốc và khung không chắc là một nhịp cuộn thì đọc cả ảnh."""
    if not shift.confident or index % _SCROLL_ANCHOR == 0:
        return None
    return shift.dy


def _read_frames(
    chosen: list[tuple[float, Path]],
    progress: ReadProgress,
    reserve: int | None = None,
    *,
    percent_lo: int = 48,
    percent_hi: int = 92,
    label: str = "",
) -> list[tuple[float, list[str], list[dict[str, str]]]]:
    if not chosen:
        progress.report(percent_hi, label or "Đọc chữ")
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
    def _span(done: int) -> int:
        if total <= 0 or percent_hi <= percent_lo:
            return percent_hi
        return min(percent_hi, percent_lo + int((done / total) * (percent_hi - percent_lo)))

    def _task(done: int, continued: bool) -> str:
        name = label or ("Đọc tiếp" if continued else "Đọc chữ")
        return f"{name}, khung {done}/{total}"

    if done_count:
        progress.report(_span(done_count), _task(done_count, True))
    else:
        progress.report(percent_lo, label or "Đọc chữ")
    reads: list[_FrameRead | None] = [None] * total
    shifts = [scroll_track.Shift(0, False)] * total
    skipped = 0
    skip_rescue: set[int] = set()

    def _note_done() -> None:
        nonlocal done_count
        done_count += 1
        progress.report(_span(done_count), _task(done_count, bool(known)))

    def _skip_frame(index: int, *, report: bool = True) -> None:
        nonlocal skipped
        if index in skip_rescue:
            return
        skipped += 1
        skip_rescue.add(index)
        stamp = chosen[index][0]
        results[index] = (stamp, [], [])
        progress.remember_frame(stamp, [], [])
        if report:
            _note_done()

    if pending and not prefers_single_worker():
        shifts = scroll_track.plan([path for _seconds, path in chosen])
    if pending and prefers_single_worker():
        presets = read_lines_batch([chosen[index][1] for index in pending])
        for offset, index in enumerate(pending):
            lines = presets[offset] if offset < len(presets) else None
            try:
                reads[index] = _read_one(chosen[index], None, lines, use_preset=True)
            except Exception:
                failed += 1
                results[index] = (chosen[index][0], [], [])
            _note_done()
    elif pending:
        workers = ocr_workers(len(pending), os.cpu_count() or 1, reserve)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_read_one, chosen[index], _strip_dy(index, shifts[index])): index
                for index in pending
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    reads[index] = future.result()
                except Exception:
                    failed += 1
                    results[index] = (chosen[index][0], [], [])
                else:
                    item = reads[index]
                    if item is not None and item.timed_out:
                        reads[index] = None
                        _skip_frame(index)
                        continue
                _note_done()
        _apply_scroll(chosen, reads, shifts)
    for index in pending:
        if index in skip_rescue or results[index] is not None:
            continue
        item = reads[index]
        if item is None:
            continue
        if item.timed_out:
            _skip_frame(index, report=False)
            continue
        word_seen += item.seen
        word_kept += item.kept
        if not item.captions and not item.sightings:
            blank += 1
        results[index] = (item.seconds, list(item.captions), [dict(found) for found in item.sightings])
        progress.remember_frame(item.seconds, item.captions, item.sightings)
    if pending:
        _rescued, unblanked = _rescue_empty(chosen, results, progress, skip_rescue)
        blank = max(0, blank - unblanked)
    if skipped:
        progress.problem(f"{skipped} khung quá 30 giây, bỏ qua.")
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
