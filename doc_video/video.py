"""Tách khung hình từ video, bỏ khung trùng, đọc song song nhiều khung.

Đọc hai bước. Bước quét chạy trên mọi khung: loại màn hình, vị trí dòng, số
điện thoại. Mỗi người hiện trên cả chục khung khi cuộn danh bạ, nên bước đọc
kỹ tên và @id chỉ chạy trên vài khung rõ nhất của mỗi người: dòng không bị
vòng tròn chạm màn hình đè, rải đều theo thời gian để mỗi lần đọc thấy một
ảnh hơi khác. Nhanh hơn nhiều so với đọc kỹ mọi khung, và bỏ phiếu giữa các
lần đọc vẫn sửa được lỗi của từng lần.

Khung được quét ngay khi ffmpeg đóng file, trong lúc video còn đang tách.
Những khung được giữ và thời điểm của chúng vẫn đúng như tách xong rồi mới quét.
"""

from __future__ import annotations

import ctypes
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile

# ffmpeg đôi khi ghi khung thiếu vài byte cuối. Đọc phần còn lại, không hủy cả video.
ImageFile.LOAD_TRUNCATED_IMAGES = True

from doc_video.frame import Contact, FrameRead, Layout, read_layout, row_at, scan_frame
from doc_video.viet import weights_path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên chỉ bỏ khung gần như y hệt.
SAME_FRAME = 0.6
# Ước lượng chỗ đĩa cho một khung JPEG chất lượng cao. Khung thật thường nhỏ hơn,
# nên video dài được cắt từng đoạn trước khi đĩa thực sự chạm đáy.
FRAME_BYTES = 400 * 1024
# Chừa chỗ này sau khi cắt khung, để hệ điều hành và khung giữa không làm đầy đĩa.
ROOM_FLOOR = 2 * 1024 * 1024 * 1024
SLICE_SECONDS = 20.0
SLICE_OVERLAP = 1.0
ONE_SHOT_CAP = 8 * 1024 * 1024 * 1024
# Số khung đọc kỹ cho mỗi số điện thoại, và cho mỗi lần mở hồ sơ. Mỗi lần đọc
# kỹ @id đã gồm sáu lần đọc ở nhiều cỡ rồi bỏ phiếu, nên hồ sơ cần ít khung hơn.
READS_PER_PERSON = 3
READS_PER_PROFILE = 2
# Một tiến trình có VietOCR giữ khoảng này. Không có bộ dấu thì nhẹ hơn.
_RAM_WITH_VIET = 800 * 1024 * 1024
_RAM_TESSERACT = 256 * 1024 * 1024


def _fraction(name: str) -> float | None:
    """Phần tài nguyên được phép dùng, từ 0 đến 1. Trống hoặc sai thì không giới hạn."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not 0 < value <= 1:
        return None
    return value


def _memory_bytes() -> tuple[int, int]:
    """(tổng RAM, RAM còn trống). (0, 0) nếu máy không cho biết."""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = (
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            )

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0, 0
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return 0, 0
    total = available = 0
    for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] == "MemTotal:":
            total = int(parts[1]) * 1024
        elif parts[0] == "MemAvailable:":
            available = int(parts[1]) * 1024
        elif parts[0] == "MemFree:" and available == 0:
            available = int(parts[1]) * 1024
    return total, available


def _bytes_per_worker() -> int:
    if os.environ.get("DOC_VIDEO_VIETOCR", "1").strip() == "0":
        return _RAM_TESSERACT
    if weights_path().is_file():
        return _RAM_WITH_VIET
    return _RAM_TESSERACT


def ffmpeg_thread_count() -> int:
    """0 nghĩa là ffmpeg dùng mọi nhân. Có mức trần thì không vượt trần đó."""
    fraction = _fraction("DOC_VIDEO_CPU_FRACTION")
    if fraction is None:
        return 0
    cores = max(1, os.cpu_count() or 1)
    return max(1, math.floor(cores * fraction))


def read_worker_count(requested: int | None = None) -> int:
    """Số tiến trình đọc. Không đặt mức trần thì bằng số nhân, hoặc đúng số được yêu cầu.

    Có mức trần thì lấy phần nhỏ hơn giữa trần CPU và chỗ RAM còn lại dưới trần.
    Luôn còn ít nhất một tiến trình để video vẫn được đọc.
    """
    cores = max(1, os.cpu_count() or 1)
    chosen = cores if requested is None else max(1, requested)
    cpu_fraction = _fraction("DOC_VIDEO_CPU_FRACTION")
    ram_fraction = _fraction("DOC_VIDEO_RAM_FRACTION")
    if cpu_fraction is None and ram_fraction is None:
        return chosen
    cpu_cap = cores if cpu_fraction is None else max(1, math.floor(cores * cpu_fraction))
    cap = cpu_cap
    if ram_fraction is not None:
        total, available = _memory_bytes()
        if total > 0:
            reserve = math.ceil(total * (1.0 - ram_fraction))
            headroom = available - reserve
            per = _bytes_per_worker()
            ram_cap = 1 if headroom < per else max(1, headroom // per)
            cap = min(cap, ram_cap)
    return max(1, min(chosen, cap))


def within_resource_ceiling() -> bool:
    """Còn đủ RAM trống dưới mức trần cho một tiến trình đọc hay không."""
    fraction = _fraction("DOC_VIDEO_RAM_FRACTION")
    if fraction is None:
        return True
    total, available = _memory_bytes()
    if total <= 0:
        return True
    reserve = math.ceil(total * (1.0 - fraction))
    return available >= reserve + _bytes_per_worker()


def _scan_file(path: str, at: float) -> Layout:
    try:
        with Image.open(path) as image:
            return scan_frame(image.convert("RGB"), at)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return Layout(at=at)


def _read_file(path: str, layout: Layout, rows: list[int] | None) -> FrameRead:
    try:
        with Image.open(path) as image:
            return read_layout(image.convert("RGB"), layout, rows)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return _unread(layout)


def _unread(layout: Layout) -> FrameRead:
    """Khung không đọc kỹ: danh bạ vẫn có số điện thoại, tên để trống; hồ sơ không có @id."""
    if layout.kind == "list":
        return FrameRead("list", [Contact(row.phone, "") for row in layout.rows], at=layout.at, tapped=layout.tapped)
    return FrameRead(layout.kind, at=layout.at)


def disk_full(exc: BaseException) -> bool:
    """Đĩa không còn chỗ. Nhận cả Errno 28 và câu tiếng Anh mà Python hoặc ffmpeg in ra."""
    if isinstance(exc, OSError) and exc.errno == 28:
        return True
    text = str(exc).lower()
    return "no space left" in text or "errno 28" in text


def _disk_text(text: str) -> bool:
    lowered = text.lower()
    return "no space left" in lowered or "errno 28" in lowered


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _empty_dir(folder: Path) -> None:
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            _unlink_quiet(child)


def _free_bytes(path: Path) -> int:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return 0


def probe_duration(video: Path) -> float | None:
    """Độ dài video, giây. Không đọc được thì None để lần cắt cả video vẫn chạy."""
    if shutil.which("ffprobe") is None:
        return None
    done = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if done.returncode != 0:
        return None
    try:
        value = float(done.stdout.strip())
    except ValueError:
        return None
    if value <= 0 or not math.isfinite(value):
        return None
    return value


def frame_budget(duration: float, fps: float) -> int:
    """Chỗ đĩa ước lượng để cắt cả video một lần, cộng phần đệm."""
    return int(duration * fps * FRAME_BYTES) + 512 * 1024 * 1024


def use_slices(budget: int, free: int) -> bool:
    """Cắt từng đoạn khi cả video không nhét vừa phần trống sau khi đã chừa sàn."""
    return budget > free - ROOM_FLOOR


def _ensure_room(path: Path, need: int, make_room: Callable[[int], None] | None) -> None:
    """Nhờ bên ngoài xóa video đã đọc xong khi phần trống chưa đủ need byte."""
    if make_room is None or _free_bytes(path) >= need:
        return
    try:
        make_room(max(0, int(need)))
    except Exception:
        return


def _open_work(
    prefix: str,
    work_dir: Path | None,
    make_room: Callable[[int], None] | None,
    need: int,
) -> tempfile.TemporaryDirectory[str]:
    """Tạo thư mục tạm. Hết chỗ thì giải phóng rồi thử một lần nữa."""
    anchor = work_dir if work_dir is not None else Path.cwd()
    if work_dir is not None:
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if not disk_full(exc):
                raise
            _ensure_room(anchor, need, make_room)
            work_dir.mkdir(parents=True, exist_ok=True)
    try:
        return tempfile.TemporaryDirectory(prefix=prefix, dir=work_dir)
    except OSError as exc:
        if not disk_full(exc):
            raise
        _ensure_room(anchor, need, make_room)
        return tempfile.TemporaryDirectory(prefix=prefix, dir=work_dir)


def _ffmpeg_frames(cmd: list[str], folder: Path, pattern: str) -> tuple[subprocess.CompletedProcess[str], list[Path]]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    except OSError as exc:
        if disk_full(exc):
            for path in folder.glob(pattern):
                _unlink_quiet(path)
        raise
    files = sorted(folder.glob(pattern))
    if _disk_text(done.stderr):
        for path in files:
            _unlink_quiet(path)
        raise OSError(28, "No space left on device", str(folder))
    return done, files


def _ffmpeg_command(
    video: Path,
    pattern: Path,
    fps: float,
    start: float | None = None,
    length: float | None = None,
) -> list[str]:
    """Lệnh tách khung. -ss đứng trước -i; ffmpeg tua tới đúng thời điểm rồi mới ghi."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        str(ffmpeg_thread_count()),
    ]
    if start is not None:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", str(video)]
    if length is not None:
        command += ["-t", f"{length:.3f}"]
    command += ["-vf", f"fps={fps}", "-q:v", "2", str(pattern)]
    return command


def extract_frames(video: Path, folder: Path, fps: float) -> list[tuple[Path, float]]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Chưa cài ffmpeg. Ubuntu: sudo apt install ffmpeg")
    pattern = folder / "f_%06d.jpg"
    done, files = _ffmpeg_frames(_ffmpeg_command(video, pattern, fps), folder, "f_*.jpg")
    if done.returncode != 0 and not files:
        raise RuntimeError(f"Không mở được video {video.name}: {done.stderr.strip()[:300]}")
    return [(path, index / fps) for index, path in enumerate(files)]


def ready_jpegs(names: list[str], process_done: bool) -> list[str]:
    """Tên file ffmpeg đã đóng. File cuối có thể đang ghi khi tiến trình còn chạy."""
    if process_done:
        return list(names)
    if len(names) < 2:
        return []
    return names[:-1]


def jpeg_time(name: str, fps: float) -> float:
    """Mốc thời gian theo số ffmpeg ghi trong tên file. Xóa khung trùng không làm lệch mốc sau."""
    number = int(Path(name).stem.split("_", 1)[1])
    return (number - 1) / fps


def pending_jpegs(names: list[str], process_done: bool, seen: set[str]) -> list[str]:
    """Khung đã đóng và chưa đọc. Danh sách co lại sau khi xóa khung trùng vẫn còn khung kế tiếp."""
    return [name for name in ready_jpegs(names, process_done) if name not in seen]


def _capture_kept(
    video: Path,
    folder: Path,
    fps: float,
    on_kept: Callable[[Path, float], None],
) -> list[tuple[Path, float]]:
    """Tách cả video và gọi on_kept ngay khi một khung được giữ.

    Khung giữ lại và mốc thời gian trùng với extract_frames rồi drop_repeats.
    Hết chỗ trên đĩa thì xóa khung và ném OSError 28, giống lệnh tách một mạch.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Chưa cài ffmpeg. Ubuntu: sudo apt install ffmpeg")
    pattern = folder / "f_%06d.jpg"
    err_path = folder / ".ffmpeg-err"
    err_handle = err_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        _ffmpeg_command(video, pattern, fps),
        stdout=subprocess.DEVNULL,
        stderr=err_handle,
    )
    err_handle.close()
    kept: list[tuple[Path, float]] = []
    last: np.ndarray | None = None
    seen: set[str] = set()
    try:
        while True:
            names = sorted(path.name for path in folder.glob("f_*.jpg"))
            done = proc.poll() is not None
            for name in pending_jpegs(names, done, seen):
                seen.add(name)
                chosen, last = _select_frames([(folder / name, jpeg_time(name, fps))], last, True)
                for item in chosen:
                    kept.append(item)
                    on_kept(item[0], item[1])
            if done:
                break
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    try:
        err = err_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        err = ""
    _unlink_quiet(err_path)
    if _disk_text(err):
        for path in folder.glob("f_*.jpg"):
            _unlink_quiet(path)
        raise OSError(28, "No space left on device", str(folder))
    if proc.returncode not in (0, None) and not seen:
        raise RuntimeError(f"Không mở được video {video.name}: {err.strip()[:300]}")
    if seen and not kept:
        raise RuntimeError(f"Không đọc được khung hình nào của {video.name}. File có thể bị cắt hoặc hỏng.")
    return kept


def _extract_span(
    video: Path, folder: Path, fps: float, start: float, length: float
) -> list[tuple[Path, float]]:
    """Cắt một đoạn. -ss đứng trước -i; ffmpeg mặc định tua tới đúng thời điểm rồi mới ghi."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Chưa cài ffmpeg. Ubuntu: sudo apt install ffmpeg")
    pattern = folder / "f_%06d.jpg"
    done, files = _ffmpeg_frames(
        _ffmpeg_command(video, pattern, fps, start, length),
        folder,
        "f_*.jpg",
    )
    if done.returncode != 0 and not files:
        raise RuntimeError(f"Không mở được video {video.name}: {done.stderr.strip()[:300]}")
    return [(path, start + index / fps) for index, path in enumerate(files)]


def _thumb(path: Path) -> np.ndarray | None:
    """Ảnh thu nhỏ để so khung trùng. Khung hỏng trả về None để video đọc tiếp các khung còn lại."""
    try:
        with Image.open(path) as image:
            gray = image.convert("L").resize((90, 195), Image.Resampling.BOX)
            return np.asarray(gray, dtype=np.float32)
    except (OSError, ValueError) as exc:
        print(f"Bỏ khung {path.name}: {exc}", flush=True)
        return None


def _select_frames(
    frames: list[tuple[Path, float]],
    last: np.ndarray | None,
    unlink_dropped: bool,
) -> tuple[list[tuple[Path, float]], np.ndarray | None]:
    kept: list[tuple[Path, float]] = []
    for path, at in frames:
        thumb = _thumb(path)
        if thumb is None:
            if unlink_dropped:
                _unlink_quiet(path)
            continue
        if last is not None and float(np.abs(thumb - last).mean()) < SAME_FRAME:
            if unlink_dropped:
                _unlink_quiet(path)
            continue
        kept.append((path, at))
        last = thumb
    return kept, last


def drop_repeats(
    frames: list[tuple[Path, float]],
    *,
    unlink_dropped: bool = False,
) -> list[tuple[Path, float]]:
    kept, _last = _select_frames(frames, None, unlink_dropped)
    return kept


def _span_with_retry(
    video: Path,
    piece: Path,
    fps: float,
    start: float,
    length: float,
    anchor: Path,
    need: int,
    make_room: Callable[[int], None] | None,
) -> list[tuple[Path, float]]:
    try:
        return _extract_span(video, piece, fps, start, length)
    except OSError as exc:
        if not disk_full(exc):
            raise
        _empty_dir(piece)
        _ensure_room(anchor, need, make_room)
        return _extract_span(video, piece, fps, start, length)


def _replace_frame(
    frame: Path,
    dest: Path,
    anchor: Path,
    need: int,
    make_room: Callable[[int], None] | None,
) -> None:
    try:
        frame.replace(dest)
    except OSError as exc:
        if not disk_full(exc):
            raise
        _ensure_room(anchor, need, make_room)
        frame.replace(dest)


def _extract_slices(
    video: Path,
    folder: Path,
    fps: float,
    duration: float,
    make_room: Callable[[int], None] | None,
    on_kept: Callable[[Path, float], None] | None = None,
) -> list[tuple[Path, float]]:
    """Cắt 20 giây một, xóa khung trùng ngay, chỉ giữ khung khác ảnh ở thư mục chính.

    Đoạn sau chồng một giây lên đoạn trước để khung ở chỗ nối không bị mất.
    """
    kept: list[tuple[Path, float]] = []
    last: np.ndarray | None = None
    saw = False
    start = 0.0
    index = 0
    seq = 0
    while start < duration - 1e-3:
        remaining = duration - start
        extra = 0.0 if remaining <= SLICE_SECONDS else SLICE_OVERLAP
        length = min(remaining, SLICE_SECONDS + extra)
        need = int(length * fps * FRAME_BYTES) + 64 * 1024 * 1024
        _ensure_room(folder, need, make_room)
        piece = folder / f"part-{index:04d}"
        try:
            piece.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if not disk_full(exc):
                raise
            _ensure_room(folder, need, make_room)
            piece.mkdir(parents=True, exist_ok=True)
        extracted = _span_with_retry(video, piece, fps, start, length, folder, need, make_room)
        if extracted:
            saw = True
        chosen, last = _select_frames(extracted, last, True)
        for frame, at in chosen:
            seq += 1
            dest = folder / f"f_{seq:06d}.jpg"
            _replace_frame(frame, dest, folder, need, make_room)
            kept.append((dest, at))
            if on_kept is not None:
                on_kept(dest, at)
        shutil.rmtree(piece, ignore_errors=True)
        start += SLICE_SECONDS
        index += 1
    if saw and not kept:
        raise RuntimeError(f"Không đọc được khung hình nào của {video.name}. File có thể bị cắt hoặc hỏng.")
    return kept


def _frames_for(
    path: Path,
    folder: Path,
    fps: float,
    make_room: Callable[[int], None] | None,
    on_kept: Callable[[Path, float], None] | None = None,
) -> list[tuple[Path, float]]:
    duration = probe_duration(path)
    budget = frame_budget(duration, fps) if duration is not None else ONE_SHOT_CAP
    need = min(budget, ONE_SHOT_CAP)
    _ensure_room(folder, need, make_room)
    if duration is not None and use_slices(budget, _free_bytes(folder)):
        print(f"Tách khung từng đoạn {path.name} vì đĩa không đủ chỗ cho cả video.", flush=True)
        return _extract_slices(path, folder, fps, duration, make_room, on_kept)
    published = False

    def publish(frame: Path, at: float) -> None:
        nonlocal published
        published = True
        if on_kept is not None:
            on_kept(frame, at)

    try:
        if on_kept is None:
            extracted = extract_frames(path, folder, fps)
        else:
            return _capture_kept(path, folder, fps, publish)
    except OSError as exc:
        if not disk_full(exc) or duration is None or published:
            raise
        print(f"Tách khung từng đoạn {path.name} vì đĩa không đủ chỗ cho cả video.", flush=True)
        _ensure_room(folder, need, make_room)
        _empty_dir(folder)
        return _extract_slices(path, folder, fps, duration, make_room, on_kept)
    kept = drop_repeats(extracted, unlink_dropped=True)
    if extracted and not kept:
        raise RuntimeError(f"Không đọc được khung hình nào của {path.name}. File có thể bị cắt hoặc hỏng.")
    return kept


def _spread(items: list, count: int) -> list:
    """count phần tử rải đều trong danh sách, giữ thứ tự."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1) if count > 1 else 0
    return [items[round(index * step)] for index in range(count)]


def _profile_runs(layouts: list[Layout]) -> list[list[int]]:
    """Các khung của từng lần mở hồ sơ. Hết lượt khi quay lại danh bạ, hoặc @id thấy lúc quét khác hẳn."""
    runs: list[list[int]] = []
    run: list[int] = []
    for index, layout in enumerate(layouts):
        if layout.kind == "list":
            if run:
                runs.append(run)
            run = []
            continue
        if layout.kind != "profile":
            continue
        if run:
            last = layouts[run[-1]].handle_hint
            hint = layout.handle_hint
            if last and hint and SequenceMatcher(None, last, hint, autojunk=False).ratio() < 0.6:
                runs.append(run)
                run = []
        run.append(index)
    if run:
        runs.append(run)
    return runs


def _same_scroll(left: Layout, right: Layout) -> bool:
    """Hai khung danh bạ ở cùng vị trí cuộn: ít nhất hai số nằm đúng cùng độ cao."""
    pitch = left.row_pitch or right.row_pitch
    if pitch <= 0:
        return False
    tops = {row.phone: row.top for row in left.rows}
    same = [row for row in right.rows if row.phone in tops and abs(tops[row.phone] - row.top) <= 0.15 * pitch]
    return len(same) >= 2


def resolve_taps(layouts: list[Layout], reach: int = 4) -> None:
    """Vòng tròn chạm đè lên chữ nên dòng bị bấm biến mất khỏi chính khung đó.

    Tìm số ở đúng độ cao vòng tròn trên khung danh bạ gần nhất có cùng vị trí cuộn.
    """
    for index, layout in enumerate(layouts):
        if layout.kind != "list" or layout.tapped or layout.tap_y < 0:
            continue
        neighbours = sorted(
            (other for other in range(max(0, index - reach), min(len(layouts), index + reach + 1)) if other != index),
            key=lambda other: abs(other - index),
        )
        for other in neighbours:
            candidate = layouts[other]
            if candidate.kind != "list" or not _same_scroll(layout, candidate):
                continue
            phone = row_at(candidate.rows, layout.tap_y, layout.row_pitch or candidate.row_pitch)
            if phone:
                layout.tapped = phone
                break


def plan_reads(
    layouts: list[Layout], per_person: int = READS_PER_PERSON, per_profile: int = READS_PER_PROFILE
) -> dict[int, list[int] | None]:
    """Khung nào cần đọc kỹ. Giá trị là các dòng danh bạ cần đọc tên, hoặc None với khung hồ sơ."""
    by_phone: dict[str, list[tuple[int, int, bool]]] = {}
    for frame_index, layout in enumerate(layouts):
        if layout.kind != "list":
            continue
        for row_index, row in enumerate(layout.rows):
            by_phone.setdefault(row.phone, []).append((frame_index, row_index, row.clear))
    plan: dict[int, list[int] | None] = {}
    for seen in by_phone.values():
        clear = [item for item in seen if item[2]]
        for frame_index, row_index, _ in _spread(clear or seen, per_person):
            rows = plan.setdefault(frame_index, [])
            if rows is not None and row_index not in rows:
                rows.append(row_index)
    for run in _profile_runs(layouts):
        # Ưu tiên khung mà lần quét đã thấy @id (hồ sơ đã tải xong, không còn trượt vào), rồi tới
        # khung không bị vòng tròn chạm đè. Khung đầu thường là lúc hồ sơ đang trượt vào màn hình.
        loaded = [index for index in run if layouts[index].handle_hint]
        neat = [index for index in loaded if layouts[index].clear]
        pool = neat if len(neat) >= per_profile else loaded if len(loaded) >= per_profile else run
        if pool is run and len(pool) > per_profile:
            pool = pool[1:]
        # Lượt dài bất thường có thể là hai hồ sơ liền nhau mà lần quét không tách được.
        count = max(per_profile, len(pool) // 4)
        for index in _spread(pool, count):
            plan[index] = None
    for rows in plan.values():
        if rows is not None:
            rows.sort()
    return plan


# Khoảng cách hai khung danh bạ 2 hình/giây. Ngắn hơn là đứng yên, dài hơn là dừng lại.
_FAST_GAP_MIN = 0.2
_FAST_GAP_MAX = 0.75
# Khung giữa cách khung đã có ít hơn chừng này thì bỏ, vì gần như cùng một ảnh.
_MIDPOINT_GAP = 0.12
_PTS_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def fast_scroll_spans(layouts: list[Layout]) -> list[tuple[float, float]]:
    """Các khoảng danh bạ vuốt nhanh: trong một nhịp 2 hình/giây, ít nhất hai số đi và hai số tới.

    Hồ sơ nằm giữa thì đó là lúc mở người, không phải một nhịp cuộn. Khung không
    phải danh bạ cũng không phải hồ sơ thì bỏ qua, vẫn so hai danh bạ kề nhau.
    """
    spans: list[tuple[float, float]] = []
    previous: Layout | None = None
    for layout in layouts:
        if layout.at < 0:
            previous = None
            continue
        if layout.kind == "profile":
            previous = None
            continue
        if layout.kind != "list":
            continue
        if previous is not None:
            gap = layout.at - previous.at
            before = {row.phone for row in previous.rows}
            after = {row.phone for row in layout.rows}
            if (
                _FAST_GAP_MIN <= gap <= _FAST_GAP_MAX
                and len(before) >= 2
                and len(after) >= 2
                and len(before - after) >= 2
                and len(after - before) >= 2
            ):
                spans.append((previous.at, layout.at))
        previous = layout
    return _merge_spans(spans)


def _merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _mid_targets(spans: list[tuple[float, float]]) -> list[float]:
    """Mốc giữa mỗi nửa giây nằm trong khoảng vuốt, tức nhịp 4 hình/giây."""
    targets: list[float] = []
    for start, end in spans:
        moment = math.floor(start / 0.5) * 0.5 + 0.25
        if moment <= start + 1e-3:
            moment += 0.5
        while moment < end - 1e-3:
            targets.append(moment)
            moment += 0.5
    return targets


def extract_midpoints(video: Path, folder: Path, spans: list[tuple[float, float]]) -> list[tuple[Path, float]]:
    """Lấy đúng khung giữa của các khoảng vuốt nhanh. Lỗi ffmpeg thì trả về rỗng, lần đọc vẫn tiếp tục."""
    spans = _merge_spans(spans)
    if not spans or shutil.which("ffmpeg") is None:
        return []
    try:
        windows = "+".join(f"between(t\\,{start:.3f}\\,{end:.3f})" for start, end in spans)
        select = f"gt({windows}\\,0)*lt(abs(mod(t\\,0.5)-0.25)\\,0.08)"
        pattern = folder / "m_%06d.jpg"
        done = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "info",
                "-threads",
                str(ffmpeg_thread_count()),
                "-i",
                str(video),
                "-vf",
                f"select={select},showinfo",
                "-fps_mode",
                "vfr",
                "-q:v",
                "2",
                str(pattern),
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        files = sorted(folder.glob("m_*.jpg"))
        if _disk_text(done.stderr):
            for path in files:
                _unlink_quiet(path)
            return []
        if done.returncode != 0 and not files:
            return []
        times = [float(item) for item in _PTS_TIME.findall(done.stderr)]
        if len(times) < len(files):
            return []
        return _closest_midpoints(list(zip(files, times[: len(files)])), spans)
    except (OSError, ValueError, subprocess.SubprocessError):
        for path in folder.glob("m_*.jpg"):
            _unlink_quiet(path)
        return []


def _closest_midpoints(
    found: list[tuple[Path, float]], spans: list[tuple[float, float]]
) -> list[tuple[Path, float]]:
    """Mỗi mốc giữa chỉ giữ một khung, khung gần mốc nhất."""
    chosen: list[tuple[Path, float]] = []
    used: set[int] = set()
    for target in _mid_targets(spans):
        best: tuple[int, Path, float] | None = None
        best_gap = 0.08
        for index, (path, at) in enumerate(found):
            if index in used:
                continue
            gap = abs(at - target)
            if gap < best_gap or (best is not None and gap == best_gap and at < best[2]):
                best_gap = gap
                best = (index, path, at)
        if best is None:
            continue
        used.add(best[0])
        chosen.append((best[1], best[2]))
    chosen.sort(key=lambda item: item[1])
    return chosen


def _insert_fast_frames(
    jobs: list[tuple[str, float]],
    layouts: list[Layout],
    video_slices: list[tuple[Path, int, int]],
    temps: list[tempfile.TemporaryDirectory[str]],
    work_dir: Path | None,
    submit: Callable[..., Future] | None,
    task_timeout: float | None,
    on_done: Callable[[int, int], None] | None,
    make_room: Callable[[int], None] | None = None,
) -> tuple[list[tuple[str, float]], list[Layout]]:
    """Quét thêm khung giữa các nhịp vuốt nhanh, rồi xếp lại theo thời gian của từng video."""
    if not video_slices:
        return jobs, layouts
    rebuilt_jobs: list[tuple[str, float]] = []
    rebuilt: list[Layout] = []
    cursor = 0
    indexed = {start: (path, end) for path, start, end in video_slices}
    while cursor < len(jobs):
        if cursor not in indexed:
            rebuilt_jobs.append(jobs[cursor])
            rebuilt.append(layouts[cursor])
            cursor += 1
            continue
        path, end = indexed[cursor]
        segment_jobs = list(jobs[cursor:end])
        segment_layouts = list(layouts[cursor:end])
        spans = fast_scroll_spans(segment_layouts)
        fresh: list[tuple[Path, float]] = []
        if spans:
            try:
                folder = _open_work("docvideo-mid-", work_dir, make_room, 1024 * 1024 * 1024)
            except OSError as exc:
                if not disk_full(exc):
                    raise
                print("Bỏ khung giữa vì đĩa không còn chỗ. Video vẫn đọc tiếp.", flush=True)
                folder = None
            if folder is not None:
                temps.append(folder)
                existing = [at for _, at in segment_jobs]
                for frame, at in extract_midpoints(path, Path(folder.name), spans):
                    if all(abs(at - old) >= _MIDPOINT_GAP for old in existing):
                        fresh.append((frame, at))
        extra: list[Layout] = []
        if fresh:
            extra = _run(
                submit,
                [(_scan_file, str(frame), at) for frame, at in fresh],
                task_timeout,
                on_done,
            )
        paired = list(zip(segment_jobs, segment_layouts))
        paired.extend(((str(frame), at), layout) for (frame, at), layout in zip(fresh, extra))
        paired.sort(key=lambda item: item[0][1])
        for job, layout in paired:
            rebuilt_jobs.append(job)
            rebuilt.append(layout)
        cursor = end
    return rebuilt_jobs, rebuilt


def _reporter(progress: Callable[[str, int, int], None] | None, stage: str) -> Callable[[int, int], None] | None:
    """Báo tiến độ ra ngoài. Lỗi ở chỗ báo không được làm hỏng lần đọc."""
    if progress is None:
        return None

    def report(done: int, total: int) -> None:
        try:
            progress(stage, done, total)
        except Exception:
            pass

    return report


def read_inputs(
    paths: list[Path],
    fps: float = 2.0,
    workers: int | None = None,
    submit: Callable[..., Future] | None = None,
    work_dir: Path | None = None,
    task_timeout: float | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    make_room: Callable[[int], None] | None = None,
) -> list[FrameRead]:
    """Đọc ảnh và video theo đúng thứ tự đưa vào. Thứ tự này dùng để biết hồ sơ mở sau danh bạ nào.

    submit là executor.submit của một nhóm tiến trình có sẵn; không có thì tự mở nhóm riêng.
    task_timeout: chờ một khung lâu hơn chừng này giây thì coi nhóm tiến trình đã hỏng (BrokenExecutor).
    progress(bước, đã xong, tổng) chỉ để hiện tiến độ: extract, scan rồi read. Không đổi kết quả đọc.
    make_room(số byte cần trống) được gọi khi đĩa chặt, trước khi cắt tiếp. Không có thì cắt với chỗ đang có.
    """
    jobs: list[tuple[str, float]] = []
    prescan: list[Future | None] = []
    temps: list[tempfile.TemporaryDirectory[str]] = []
    video_slices: list[tuple[Path, int, int]] = []
    pool: ProcessPoolExecutor | None = None
    extracting = _reporter(progress, "extract")

    def open_pool() -> None:
        nonlocal pool, submit
        if submit is not None or pool is not None:
            return
        count = read_worker_count(workers)
        if count <= 1:
            return
        pool = ProcessPoolExecutor(max_workers=count)
        submit = pool.submit

    try:
        try:
            for path in paths:
                suffix = path.suffix.lower()
                if suffix in IMAGE_SUFFIXES:
                    jobs.append((str(path), -1.0))
                    prescan.append(None)
                elif suffix in VIDEO_SUFFIXES:
                    if extracting is not None:
                        extracting(0, 0)
                    open_pool()
                    for index, (queued, at) in enumerate(jobs):
                        if prescan[index] is None and submit is not None:
                            prescan[index] = submit(_scan_file, queued, at)
                    folder = _open_work("docvideo-frames-", work_dir, make_room, ONE_SHOT_CAP)
                    temps.append(folder)
                    start = len(jobs)

                    def keep(frame: Path, at: float) -> None:
                        fut = submit(_scan_file, str(frame), at) if submit is not None else None
                        prescan.append(fut)

                    frames = _frames_for(
                        path,
                        Path(folder.name),
                        fps,
                        make_room,
                        keep if submit is not None else None,
                    )
                    jobs.extend((str(frame), at) for frame, at in frames)
                    if submit is None:
                        prescan.extend([None] * len(frames))
                    elif len(prescan) != len(jobs):
                        raise RuntimeError("Số khung đã đưa đi quét không khớp số khung giữ lại.")
                    if frames:
                        video_slices.append((path, start, len(jobs)))
                else:
                    raise RuntimeError(f"Không đọc được {path.name}: cần ảnh hoặc video.")
        except BaseException:
            for fut in prescan:
                if fut is not None:
                    fut.cancel()
            raise
        if submit is None:
            count = read_worker_count(workers)
            if count > 1 and len(jobs) > 1:
                pool = ProcessPoolExecutor(max_workers=count)
                submit = pool.submit
        if submit is None:
            layouts = _run(
                None,
                [(_scan_file, path, at) for path, at in jobs],
                task_timeout,
                _reporter(progress, "scan"),
            )
        else:
            waiting = [
                fut if fut is not None else submit(_scan_file, path, at)
                for (path, at), fut in zip(jobs, prescan, strict=True)
            ]
            layouts = _finish(waiting, task_timeout, _reporter(progress, "scan"))
        jobs, layouts = _insert_fast_frames(
            jobs,
            layouts,
            video_slices,
            temps,
            work_dir,
            submit,
            task_timeout,
            _reporter(progress, "scan"),
            make_room,
        )
        resolve_taps(layouts)
        plan = plan_reads(layouts)
        order = sorted(plan)
        reads = _run(
            submit,
            [(_read_file, jobs[index][0], layouts[index], plan[index]) for index in order],
            task_timeout,
            _reporter(progress, "read"),
        )
        done = dict(zip(order, reads))
        return [done[index] if index in done else _unread(layout) for index, layout in enumerate(layouts)]
    finally:
        if pool is not None:
            try:
                pool.shutdown(cancel_futures=True)
            except Exception:
                pass
        for folder in temps:
            try:
                folder.cleanup()
            except OSError:
                pass


def _finish(
    futures: list[Future],
    timeout: float | None = None,
    on_done: Callable[[int, int], None] | None = None,
) -> list:
    total = len(futures)
    if on_done is not None:
        on_done(0, total)
    try:
        collected = []
        for future in futures:
            collected.append(future.result(timeout=timeout))
            if on_done is not None:
                on_done(len(collected), total)
        return collected
    except FutureTimeout as exc:
        for future in futures:
            future.cancel()
        raise BrokenExecutor(f"Một khung không đọc xong sau {timeout:.0f} giây.") from exc
    except BaseException:
        for future in futures:
            future.cancel()
        raise


def _run(
    submit: Callable[..., Future] | None,
    calls: list[tuple],
    timeout: float | None = None,
    on_done: Callable[[int, int], None] | None = None,
) -> list:
    if submit is None:
        total = len(calls)
        if on_done is not None:
            on_done(0, total)
        results = []
        for call in calls:
            results.append(call[0](*call[1:]))
            if on_done is not None:
                on_done(len(results), total)
        return results
    return _finish([submit(*call) for call in calls], timeout, on_done)
