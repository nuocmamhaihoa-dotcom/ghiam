"""Tách khung hình từ video, bỏ khung trùng, đọc song song nhiều khung.

Đọc hai bước. Bước quét chạy trên mọi khung: loại màn hình, vị trí dòng, số
điện thoại. Mỗi người hiện trên cả chục khung khi cuộn danh bạ, nên bước đọc
kỹ tên và @id chỉ chạy trên vài khung rõ nhất của mỗi người: dòng không bị
vòng tròn chạm màn hình đè, rải đều theo thời gian để mỗi lần đọc thấy một
ảnh hơi khác. Nhanh hơn nhiều so với đọc kỹ mọi khung, và bỏ phiếu giữa các
lần đọc vẫn sửa được lỗi của từng lần.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

from doc_video.frame import Contact, FrameRead, Layout, read_layout, row_at, scan_frame

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên chỉ bỏ khung gần như y hệt.
SAME_FRAME = 0.6
# Số khung đọc kỹ cho mỗi số điện thoại, và cho mỗi lần mở hồ sơ. Mỗi lần đọc
# kỹ @id đã gồm sáu lần đọc ở nhiều cỡ rồi bỏ phiếu, nên hồ sơ cần ít khung hơn.
READS_PER_PERSON = 3
READS_PER_PROFILE = 2


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
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps}", "-q:v", "2", str(pattern)],
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
        clear = [index for index in run if layouts[index].clear] or run
        # Khung đầu thường là lúc hồ sơ đang trượt vào màn hình.
        if len(clear) > per_profile:
            clear = clear[1:]
        # Lượt dài bất thường có thể là hai hồ sơ liền nhau mà lần quét không tách được.
        count = max(per_profile, len(clear) // 4)
        for index in _spread(clear, count):
            plan[index] = None
    for rows in plan.values():
        if rows is not None:
            rows.sort()
    return plan


def read_inputs(
    paths: list[Path],
    fps: float = 2.0,
    workers: int | None = None,
    submit: Callable[..., Future] | None = None,
    work_dir: Path | None = None,
) -> list[FrameRead]:
    """Đọc ảnh và video theo đúng thứ tự đưa vào. Thứ tự này dùng để biết hồ sơ mở sau danh bạ nào.

    submit là executor.submit của một nhóm tiến trình có sẵn; không có thì tự mở nhóm riêng.
    """
    jobs: list[tuple[str, float]] = []
    temps: list[tempfile.TemporaryDirectory[str]] = []
    pool: ProcessPoolExecutor | None = None
    try:
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                jobs.append((str(path), -1.0))
            elif suffix in VIDEO_SUFFIXES:
                folder = tempfile.TemporaryDirectory(prefix="docvideo-frames-", dir=work_dir)
                temps.append(folder)
                frames = drop_repeats(extract_frames(path, Path(folder.name), fps))
                jobs.extend((str(frame), at) for frame, at in frames)
            else:
                raise RuntimeError(f"Không đọc được {path.name}: cần ảnh hoặc video.")
        if submit is None:
            workers = workers or max(1, (os.cpu_count() or 2) - 1)
            if workers > 1 and len(jobs) > 1:
                pool = ProcessPoolExecutor(max_workers=workers)
                submit = pool.submit
        layouts = _run(submit, [(_scan_file, path, at) for path, at in jobs])
        resolve_taps(layouts)
        plan = plan_reads(layouts)
        order = sorted(plan)
        reads = _run(submit, [(_read_file, jobs[index][0], layouts[index], plan[index]) for index in order])
        done = dict(zip(order, reads))
        return [done[index] if index in done else _unread(layout) for index, layout in enumerate(layouts)]
    finally:
        if pool is not None:
            pool.shutdown(cancel_futures=True)
        for folder in temps:
            folder.cleanup()


def _run(submit: Callable[..., Future] | None, calls: list[tuple]) -> list:
    if submit is None:
        return [call[0](*call[1:]) for call in calls]
    futures = [submit(*call) for call in calls]
    try:
        return [future.result() for future in futures]
    except BaseException:
        for future in futures:
            future.cancel()
        raise
