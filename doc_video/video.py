"""Tách khung hình từ video, bỏ khung trùng, đọc song song nhiều khung.

Đọc hai bước. Bước quét chạy trên mọi khung: loại màn hình, vị trí dòng, số
điện thoại. Mỗi người hiện trên cả chục khung khi cuộn danh bạ, nên bước đọc
kỹ tên và @id chỉ chạy trên vài khung rõ nhất của mỗi người: dòng không bị
vòng tròn chạm màn hình đè, rải đều theo thời gian để mỗi lần đọc thấy một
ảnh hơi khác. Nhanh hơn nhiều so với đọc kỹ mọi khung, và bỏ phiếu giữa các
lần đọc vẫn sửa được lỗi của từng lần.
"""

from __future__ import annotations

import ctypes
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

from doc_video.frame import Contact, FrameRead, Layout, read_layout, row_at, scan_frame
from doc_video.viet import weights_path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên chỉ bỏ khung gần như y hệt.
SAME_FRAME = 0.6
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


def extract_frames(video: Path, folder: Path, fps: float) -> list[tuple[Path, float]]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Chưa cài ffmpeg. Ubuntu: sudo apt install ffmpeg")
    pattern = folder / "f_%06d.jpg"
    done = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            str(ffmpeg_thread_count()),
            "-i",
            str(video),
            "-vf",
            f"fps={fps}",
            "-q:v",
            "2",
            str(pattern),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    files = sorted(folder.glob("f_*.jpg"))
    if done.returncode != 0 and not files:
        raise RuntimeError(f"Không mở được video {video.name}: {done.stderr.strip()[:300]}")
    return [(path, index / fps) for index, path in enumerate(files)]


def _thumb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L").resize((90, 195), Image.Resampling.BOX), dtype=np.float32)


def drop_repeats(frames: list[tuple[Path, float]]) -> list[tuple[Path, float]]:
    kept: list[tuple[Path, float]] = []
    last: np.ndarray | None = None
    for path, at in frames:
        thumb = _thumb(path)
        if last is not None and float(np.abs(thumb - last).mean()) < SAME_FRAME:
            continue
        kept.append((path, at))
        last = thumb
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
        if done.returncode != 0 and not files:
            return []
        times = [float(item) for item in _PTS_TIME.findall(done.stderr)]
        if len(times) < len(files):
            return []
        return _closest_midpoints(list(zip(files, times[: len(files)])), spans)
    except (OSError, ValueError, subprocess.SubprocessError):
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
            folder = tempfile.TemporaryDirectory(prefix="docvideo-mid-", dir=work_dir)
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
) -> list[FrameRead]:
    """Đọc ảnh và video theo đúng thứ tự đưa vào. Thứ tự này dùng để biết hồ sơ mở sau danh bạ nào.

    submit là executor.submit của một nhóm tiến trình có sẵn; không có thì tự mở nhóm riêng.
    task_timeout: chờ một khung lâu hơn chừng này giây thì coi nhóm tiến trình đã hỏng (BrokenExecutor).
    progress(bước, đã xong, tổng) chỉ để hiện tiến độ: extract, scan rồi read. Không đổi kết quả đọc.
    """
    jobs: list[tuple[str, float]] = []
    temps: list[tempfile.TemporaryDirectory[str]] = []
    video_slices: list[tuple[Path, int, int]] = []
    pool: ProcessPoolExecutor | None = None
    extracting = _reporter(progress, "extract")
    try:
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                jobs.append((str(path), -1.0))
            elif suffix in VIDEO_SUFFIXES:
                if extracting is not None:
                    extracting(0, 0)
                folder = tempfile.TemporaryDirectory(prefix="docvideo-frames-", dir=work_dir)
                temps.append(folder)
                start = len(jobs)
                frames = drop_repeats(extract_frames(path, Path(folder.name), fps))
                jobs.extend((str(frame), at) for frame, at in frames)
                if frames:
                    video_slices.append((path, start, len(jobs)))
            else:
                raise RuntimeError(f"Không đọc được {path.name}: cần ảnh hoặc video.")
        if submit is None:
            workers = read_worker_count(workers)
            if workers > 1 and len(jobs) > 1:
                pool = ProcessPoolExecutor(max_workers=workers)
                submit = pool.submit
        layouts = _run(submit, [(_scan_file, path, at) for path, at in jobs], task_timeout, _reporter(progress, "scan"))
        jobs, layouts = _insert_fast_frames(
            jobs,
            layouts,
            video_slices,
            temps,
            work_dir,
            submit,
            task_timeout,
            _reporter(progress, "scan"),
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


def _run(
    submit: Callable[..., Future] | None,
    calls: list[tuple],
    timeout: float | None = None,
    on_done: Callable[[int, int], None] | None = None,
) -> list:
    total = len(calls)
    if on_done is not None:
        on_done(0, total)
    if submit is None:
        results = []
        for call in calls:
            results.append(call[0](*call[1:]))
            if on_done is not None:
                on_done(len(results), total)
        return results
    futures = [submit(*call) for call in calls]
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
