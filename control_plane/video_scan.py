"""Đọc video hoặc ảnh chụp màn hình danh bạ, xuất bảng số điện thoại, tên, username.

Lượt một quét ảnh xám nhỏ @30fps (A1), biết lúc đứng yên / chuyển cảnh.
Quanh chuyển cảnh tách thêm burst @60fps (A3) để không bỏ hồ sơ mở rất ngắn.
Chỉ lấy luồng hình — không phân tích audio. ROI theo máy (B2) + calibrate đầu video (B3).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from control_plane.screen_layout import (
    LayoutProfile,
    calibrate_from_paths,
    load_layout,
    merge_layout,
    save_layout,
)
from control_plane.screen_read import read_image, read_image_aggressive, read_tap
from control_plane.screen_table import FrameObs, Table, build_table

Progress = Callable[[str], None]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

# A1: 30fps xem trước. A3: burst 60fps ±0.12s quanh chuyển cảnh.
SCAN_FPS = 30.0
BURST_FPS = 60.0
BURST_RADIUS_SEC = 0.12
THUMB_W = 48
THUMB_H = 104
# ~0.03–0.07s @30fps: hai khung liên tiếp giống nhau là đủ đưa vào hàng đọc.
SETTLE_LAG = 1
SETTLED = 3.5
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên ngưỡng "đã đổi cảnh" phải rất thấp.
CHANGED = 1.2
MOVING = 2.5
# Hồ sơ vs danh bạ lệch rất mạnh; ngưỡng hơi thấp hơn để bắt flash nhạt / chuyển cảnh mờ.
FLASH_CHANGED = 9.0
# ~0.23s trước chuyển cảnh @30fps.
TAP_WINDOW = 7
# Chỉ OCR vài khung sát lúc thoát (hồ sơ ngắn).
BRIEF_EXIT_READS = 3

Submit = Callable[..., Future]


@dataclass
class VideoPlan:
    """Khung cần đọc, tính theo số thứ tự ở nhịp SCAN_FPS."""

    reads: list[int] = field(default_factory=list)
    taps: list[list[int]] = field(default_factory=list)
    # Thời điểm chuyển cảnh (số khung @SCAN_FPS) — dùng cho burst 60fps.
    transitions: list[int] = field(default_factory=list)
    count: int = 0

    def needed(self) -> list[int]:
        frames = set(self.reads)
        for window in self.taps:
            frames.update(window)
        return sorted(frames)

    def transition_times(self, fps: float = SCAN_FPS) -> list[float]:
        times = {number / fps for number in self.transitions}
        for window in self.taps:
            if window:
                times.add(window[-1] / fps)
        return sorted(times)


# Một khung hỏng hoặc một lần đọc chữ quá hạn chỉ làm mất khung đó, không làm hỏng cả video.
_FRAME_ERRORS = (OSError, ValueError, subprocess.SubprocessError)


def read_frame_at(path: str, at: float, layout: dict[str, object] | None = None) -> FrameObs:
    try:
        return dataclasses.replace(read_image(path, layout=layout), at=at)
    except _FRAME_ERRORS as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return FrameObs("unknown", at=at)


def read_frame_aggressive_at(path: str, at: float, layout: dict[str, object] | None = None) -> FrameObs:
    """Vòng 2 OCR — khung lượt một unknown/lỗi."""
    try:
        return dataclasses.replace(read_image_aggressive(path, layout=layout), at=at)
    except _FRAME_ERRORS as exc:
        print(f"Bỏ khung vòng 2 {Path(path).name}: {exc}", flush=True)
        return FrameObs("unknown", at=at)


def read_tap_at(paths: list[str], at: float) -> FrameObs:
    try:
        return dataclasses.replace(read_tap(paths), at=at)
    except _FRAME_ERRORS as exc:
        print(f"Bỏ khung bấm {Path(paths[-1]).name}: {exc}", flush=True)
        return FrameObs("unknown", at=at)


def burst_frame_numbers(times: list[float], burst_fps: float = BURST_FPS, radius: float = BURST_RADIUS_SEC) -> list[int]:
    """A3: các số khung @burst_fps quanh mỗi thời điểm chuyển cảnh."""
    picks: set[int] = set()
    for moment in times:
        start = max(0, int((moment - radius) * burst_fps))
        end = int((moment + radius) * burst_fps)
        picks.update(range(start, end + 1))
    return sorted(picks)


def scan_paths(
    paths: list[Path],
    fps: float = SCAN_FPS,
    submit: Submit | None = None,
    work_dir: Path | None = None,
    on_progress: Progress | None = None,
    device: str = "",
    data_dir: Path | None = None,
) -> tuple[Table, list[tuple[Path, FrameObs]]]:
    """Đọc ảnh và video theo thứ tự. submit là executor.submit để đọc nhiều khung cùng lúc."""
    for path in paths:
        suffix = path.suffix.lower()
        if suffix not in IMAGE_SUFFIXES and suffix not in VIDEO_SUFFIXES:
            raise RuntimeError(f"Không đọc được {path.name}. Dùng ảnh hoặc video.")

    temps: list[tempfile.TemporaryDirectory[str]] = []
    layout = load_layout(data_dir, device) if data_dir is not None else LayoutProfile(device=device or "default")
    layout_dict = layout.to_dict()
    try:
        image_paths = [(index, path) for index, path in enumerate(paths) if path.suffix.lower() in IMAGE_SUFFIXES]
        image_hits: dict[int, FrameObs] = {}
        if image_paths:
            if on_progress is not None:
                on_progress(f"Đang đọc {len(image_paths)} ảnh")
            if submit is None:
                for index, path in image_paths:
                    image_hits[index] = read_image(path, layout=layout_dict)
            else:
                futures = [
                    (index, path, submit(read_image, str(path), layout_dict)) for index, path in image_paths
                ]
                for index, path, future in futures:
                    image_hits[index] = future.result()

        ordered: list[tuple[Path, FrameObs]] = []
        learned: LayoutProfile | None = None
        for index, path in enumerate(paths):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                ordered.append((path, image_hits[index]))
                continue
            folder = tempfile.TemporaryDirectory(prefix="danhba-frames-", dir=work_dir)
            temps.append(folder)
            chunk, learned = _scan_video(
                path,
                Path(folder.name),
                fps,
                submit,
                on_progress,
                device=device,
                data_dir=data_dir,
                layout=layout,
            )
            ordered.extend(chunk)
        if data_dir is not None and learned is not None and learned.samples > 0:
            merged = merge_layout(layout, learned, weight=0.45)
            merged.device = device or layout.device or "default"
            save_layout(data_dir, merged)
            if on_progress is not None:
                on_progress(f"Đã lưu bố cục máy «{merged.device}» ({merged.samples} mẫu)")
        return build_table([obs for _, obs in ordered]), ordered
    finally:
        for folder in temps:
            folder.cleanup()


def _scan_video(
    path: Path,
    folder: Path,
    fps: float,
    submit: Submit | None,
    on_progress: Progress | None = None,
    device: str = "",
    data_dir: Path | None = None,
    layout: LayoutProfile | None = None,
) -> tuple[list[tuple[Path, FrameObs]], LayoutProfile | None]:
    if on_progress is not None:
        on_progress(f"Đang xem trước {path.name} @ {fps:.0f}fps")
    plan = plan_video(path, fps, on_progress=on_progress)
    needed = plan.needed()
    if on_progress is not None:
        on_progress(f"Đang tách {len(needed)} khung từ {path.name}")
    files = extract_frames(path, folder, needed, fps, on_progress=on_progress)

    # B3: calibrate ROI từ vài khung đầu (đo nút hồng, không OCR chữ).
    base = layout or LayoutProfile(device=device or "default")
    cal_paths = [files[number] for number in plan.reads[:24] if number in files]
    measured = calibrate_from_paths(cal_paths)
    locked = merge_layout(base, measured, weight=0.75) if measured is not None else base
    locked.device = device or base.device or "default"
    if measured is not None and on_progress is not None:
        on_progress(f"Đã khóa bố cục đầu video («{locked.device}»)")
    layout_dict = locked.to_dict()

    # A3: burst 60fps quanh chuyển cảnh — bỏ khung trùng thời điểm đã có @30fps.
    burst_files: dict[int, Path] = {}
    times = plan.transition_times(fps)
    burst_numbers = burst_frame_numbers(times)
    if burst_numbers:
        existing_times = {number / fps for number in needed}
        filtered = [
            number
            for number in burst_numbers
            if not any(abs(number / BURST_FPS - moment) < (0.5 / fps) for moment in existing_times)
        ]
        if filtered:
            if on_progress is not None:
                on_progress(f"Đang tách burst {len(filtered)} khung @ {BURST_FPS:.0f}fps")
            burst_files = extract_frames(
                path,
                folder,
                filtered,
                BURST_FPS,
                on_progress=on_progress,
                prefix="b",
            )

    jobs: list[tuple[float, int, Path, Callable[..., FrameObs], tuple]] = []
    read_numbers = set(plan.reads)
    for number in plan.reads:
        jobs.append(
            (
                number / fps,
                0,
                files[number],
                read_frame_at,
                (str(files[number]), number / fps, layout_dict),
            )
        )
    for window in plan.taps:
        last = window[-1]
        paths = [str(files[number]) for number in window]
        jobs.append((last / fps, 1, files[last], read_tap_at, (paths, last / fps)))
        for number in window[-BRIEF_EXIT_READS:]:
            if number in read_numbers:
                continue
            read_numbers.add(number)
            jobs.append(
                (
                    number / fps,
                    0,
                    files[number],
                    read_frame_at,
                    (str(files[number]), number / fps, layout_dict),
                )
            )
    for number, burst_path in burst_files.items():
        jobs.append(
            (
                number / BURST_FPS,
                0,
                burst_path,
                read_frame_at,
                (str(burst_path), number / BURST_FPS, layout_dict),
            )
        )
    jobs.sort(key=lambda job: (job[0], job[1]))
    total = len(jobs)
    progress_every_sec = 10.0
    step = max(1, min(100, total // 50)) if total else 1
    if on_progress is not None:
        on_progress(f"Đang đọc chữ 0/{total} khung")
    if submit is None:
        results: list[FrameObs] = []
        last_beat = time.monotonic()
        for index, (_, _, _, fn, args) in enumerate(jobs, start=1):
            results.append(fn(*args))
            now = time.monotonic()
            if on_progress is not None and (
                index == total or index % step == 0 or now - last_beat >= progress_every_sec
            ):
                on_progress(f"Đang đọc chữ {index}/{total} khung")
                last_beat = now
    else:
        batch_size = 500
        results = [FrameObs("unknown")] * total
        done = 0
        last_beat = time.monotonic()
        try:
            for start in range(0, total, batch_size):
                chunk = jobs[start : start + batch_size]
                future_map = {
                    submit(fn, *args): start + offset for offset, (_, _, _, fn, args) in enumerate(chunk)
                }
                for future in as_completed(future_map):
                    index = future_map[future]
                    results[index] = future.result()
                    done += 1
                    now = time.monotonic()
                    if on_progress is not None and (
                        done == total or done % step == 0 or now - last_beat >= progress_every_sec
                    ):
                        on_progress(f"Đang đọc chữ {done}/{total} khung")
                        last_beat = now
        except BaseException:
            raise
    # R2: vòng 2 OCR các khung unknown (không đụng job tap — chỉ khung đơn).
    results = _round2_unknowns(jobs, results, submit=submit, on_progress=on_progress, layout_dict=layout_dict)
    learned = measured if measured is not None else None
    return [(job[2], obs) for job, obs in zip(jobs, results)], learned


def _round2_unknowns(
    jobs: list[tuple[float, int, Path, Callable[..., FrameObs], tuple]],
    results: list[FrameObs],
    *,
    submit: Submit | None,
    on_progress: Progress | None,
    layout_dict: dict[str, object],
) -> list[FrameObs]:
    """Đọc lại khung unknown bằng OCR mạnh hơn; giữ nguyên nếu vẫn không ra."""
    targets: list[tuple[int, str, float]] = []
    for index, (at, kind, path, fn, _args) in enumerate(jobs):
        if kind != 0 or fn is not read_frame_at:
            continue
        if index >= len(results) or results[index].kind != "unknown":
            continue
        targets.append((index, str(path), at))
    if not targets:
        return results
    total = len(targets)
    if on_progress is not None:
        on_progress(f"Vòng 2 OCR {total} khung lỗi/unknown")
    updated = list(results)
    if submit is None:
        for offset, (index, path, at) in enumerate(targets, start=1):
            obs = read_frame_aggressive_at(path, at, layout_dict)
            if obs.kind != "unknown":
                updated[index] = obs
            if on_progress is not None and (offset == total or offset % 25 == 0):
                on_progress(f"Vòng 2 OCR {offset}/{total} khung")
        return updated
    future_map = {
        submit(read_frame_aggressive_at, path, at, layout_dict): index for index, path, at in targets
    }
    done = 0
    for future in as_completed(future_map):
        index = future_map[future]
        try:
            obs = future.result()
        except Exception as exc:
            print(f"Vòng 2 OCR lỗi: {exc}", flush=True)
            done += 1
            continue
        if obs.kind != "unknown":
            updated[index] = obs
        done += 1
        if on_progress is not None and (done == total or done % 25 == 0):
            on_progress(f"Vòng 2 OCR {done}/{total} khung")
    return updated


def plan_video(path: Path, fps: float = SCAN_FPS, on_progress: Progress | None = None) -> VideoPlan:
    """Lượt một: ảnh xám nhỏ đi qua ống, chỉ giữ vài khung gần nhất trong bộ nhớ."""
    _require_ffmpeg(path)
    # Chỉ lấy luồng hình — video quay màn hình không cần audio.
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "0",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"fps={fps},scale={THUMB_W}:{THUMB_H},format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    size = THUMB_W * THUMB_H
    planner = _Planner()
    # Báo tiến độ mỗi ~5 giây video để watchdog không tưởng bộ đọc chết khi xem trước file dài.
    report_every = max(1, int(fps * 5))
    last_report = 0
    # Lỗi của ffmpeg ghi ra file: video hỏng có thể in rất nhiều dòng lỗi, ống đầy thì ffmpeg đứng chờ mãi.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as proc:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(size)
                if len(chunk) < size:
                    break
                # uint8 + copy: buffer ống bị ghi đè; so khớp ngưỡng vẫn trên thang 0–255 như cũ.
                thumb = np.frombuffer(chunk, dtype=np.uint8).reshape(THUMB_H, THUMB_W).copy()
                planner.push(thumb)
                if on_progress is not None and planner.count - last_report >= report_every:
                    on_progress(f"Đang xem trước {path.name}: {planner.count / fps:.0f}s")
                    last_report = planner.count
            code = proc.wait()
        errors.seek(0)
        error = errors.read(8192).decode("utf-8", "replace")
    if code != 0 and planner.count == 0:
        detail = error.strip().splitlines()[0] if error.strip() else ""
        message = "Không mở được video: file hỏng, chưa tải xong, hoặc không phải video."
        raise RuntimeError(f"{message} ({detail})" if detail else message)
    plan = planner.finish()
    if plan.count == 0:
        raise RuntimeError(f"Video {path.name} không có khung hình.")
    if on_progress is not None:
        on_progress(f"Đã xem trước {path.name}: {plan.count / fps:.0f}s, chọn {len(plan.needed())} khung")
    return plan


class _Planner:
    """Chọn khung khi các ảnh nhỏ đi qua lần lượt.

    Đứng yên: khung giống khung SETTLE_LAG bước sau. Giữ khi khác khung đã giữ.
    Bắt đầu chuyển: bước đầu tiên có độ khác lớn. Lấy TAP_WINDOW khung ngay trước đó.
    Hồ sơ chỉ ló 1 khung (danh bạ → hồ sơ → danh bạ) bắt bằng spike giữa hai cạnh chuyển động.
    """

    def __init__(self) -> None:
        self.ring: deque[np.ndarray] = deque(maxlen=SETTLE_LAG + 1)
        # Giữ thêm vài khung để spike/hồ sơ ngắn còn lấy được khung lân cận.
        self.history: deque[tuple[int, np.ndarray]] = deque(maxlen=TAP_WINDOW + 5)
        self.last_kept: np.ndarray | None = None
        self.was_moving = False
        self.spike_kept = False
        self.count = 0
        self.plan = VideoPlan()

    def push(self, thumb: np.ndarray) -> None:
        number = self.count
        self.count += 1
        if self.history:
            prev_number, prev = self.history[-1]
            moving = _mean_abs(prev, thumb) >= MOVING
            if len(self.history) >= 2:
                pre_number, pre = self.history[-2]
                self._keep_spike(prev_number, prev, pre, thumb)
            if moving and not self.was_moving:
                self._tap_window(prev_number)
                self._keep_brief_scene(prev_number)
                self.plan.transitions.append(prev_number)
                self.spike_kept = False
            if not moving and self.was_moving:
                self.spike_kept = False
            self.was_moving = moving
        self.history.append((number, thumb))
        self.ring.append(thumb)
        if len(self.ring) == SETTLE_LAG + 1:
            self._consider(number - SETTLE_LAG, self.ring[0], thumb)

    def finish(self) -> VideoPlan:
        first = self.count - len(self.ring) + 1
        tail = list(self.ring)
        for offset, thumb in enumerate(tail[1:-1] if len(tail) > 2 else []):
            self._consider(first + offset, thumb, tail[-1])
        if self.count and not self.plan.reads:
            self.plan.reads.append(0)
        self.plan.count = self.count
        return self.plan

    def _consider(self, number: int, thumb: np.ndarray, later: np.ndarray) -> None:
        if _mean_abs(thumb, later) >= SETTLED:
            return
        if self.last_kept is not None and _mean_abs(thumb, self.last_kept) < CHANGED:
            return
        self.plan.reads.append(number)
        self.last_kept = thumb

    def _keep_brief_scene(self, number: int) -> None:
        """Hồ sơ mở ~0.1s: đọc các khung đứng yên vừa có trước lúc chuyển cảnh."""
        if number < 0:
            return
        for frame_number, frame in self.history:
            if frame_number > number:
                continue
            if self.last_kept is not None and _mean_abs(frame, self.last_kept) < CHANGED:
                continue
            if frame_number in self.plan.reads:
                continue
            if self.plan.reads and frame_number < self.plan.reads[-1]:
                continue
            self.plan.reads.append(frame_number)
            self.last_kept = frame

    def _keep_spike(self, mid_number: int, mid: np.ndarray, left: np.ndarray, right: np.ndarray) -> None:
        """Hó hồ sơ 1 khung: hai bên giống nhau (về lại danh bạ), khung giữa khác hẳn."""
        if self.spike_kept or mid_number < 0:
            return
        if _mean_abs(mid, left) < MOVING or _mean_abs(mid, right) < MOVING:
            return
        # Cuộn danh bạ: trái/phải cũng đang đổi — không phải mở rồi đóng hồ sơ.
        if _mean_abs(left, right) >= SETTLED:
            return
        if self.last_kept is not None and _mean_abs(mid, self.last_kept) < FLASH_CHANGED:
            return
        self._force_read(mid_number, mid)
        self.plan.transitions.append(mid_number)
        # Khung trước/sau flash đôi khi rõ @ hơn khung giữa (đang chuyển cảnh).
        by_number = {number: frame for number, frame in self.history}
        by_number[mid_number] = mid
        for neighbor in (mid_number - 1, mid_number + 1):
            frame = by_number.get(neighbor)
            if frame is not None:
                self._force_read(neighbor, frame)
        self.spike_kept = True

    def _force_read(self, number: int, thumb: np.ndarray) -> None:
        if number < 0 or number in self.plan.reads:
            return
        self.plan.reads.append(number)
        self.last_kept = thumb

    def _tap_window(self, last: int) -> None:
        if last < 0:
            return
        start = max(0, last - TAP_WINDOW + 1)
        if self.plan.taps and self.plan.taps[-1][-1] >= start:
            start = self.plan.taps[-1][-1] + 1
        if start > last:
            return
        self.plan.taps.append(list(range(start, last + 1)))


def extract_frames(
    path: Path,
    folder: Path,
    numbers: list[int],
    fps: float = SCAN_FPS,
    on_progress: Progress | None = None,
    prefix: str = "f",
) -> dict[int, Path]:
    """Lượt hai: chỉ ghi ra đĩa các khung đã chọn, đủ độ phân giải. Chỉ luồng hình."""
    _require_ffmpeg(path)
    if not numbers:
        return {}
    script = folder / f"select_{prefix}.txt"
    picks = "+".join(f"eq(n,{number})" for number in numbers)
    script.write_text(f"fps={fps},select='{picks}'", encoding="utf-8")
    pattern = folder / f"{prefix}_%06d.jpg"
    stop = threading.Event()
    watcher: threading.Thread | None = None
    if on_progress is not None:
        expected = len(numbers)
        glob_pat = f"{prefix}_*.jpg"

        def _watch() -> None:
            while not stop.wait(5.0):
                try:
                    written = sum(1 for _ in folder.glob(glob_pat))
                    on_progress(f"Đang tách khung {written}/{expected}")
                except Exception as exc:
                    print(f"Theo dõi tách khung: {exc}", flush=True)

        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()
    try:
        done = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-threads",
                "0",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-filter_script:v",
                str(script),
                "-vsync",
                "0",
                "-q:v",
                "2",
                str(pattern),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=3 * 3600,
        )
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(timeout=1.0)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"ffmpeg không đọc được {path.name}.")
    written = sorted(folder.glob(f"{prefix}_*.jpg"))
    if len(written) < len(numbers):
        raise RuntimeError(f"Video {path.name} chỉ ra {len(written)}/{len(numbers)} khung cần đọc.")
    if on_progress is not None:
        on_progress(f"Đã tách {len(written)} khung")
    return dict(zip(numbers, written))


def _require_ffmpeg(path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Cần cài ffmpeg để đọc video.")
    if not path.exists():
        raise RuntimeError(f"Không thấy file {path}.")


def _mean_abs(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        return 255.0
    # int16 tránh wrap-around của uint8 khi trừ.
    return float(np.abs(left.astype(np.int16, copy=False) - right.astype(np.int16, copy=False)).mean())


def write_table(table: Table, output: Path) -> list[Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    written = [output]
    _write_csv(
        output,
        ["Số điện thoại", "Tên", "Username"],
        [[row.phone, row.name, row.username] for row in table.rows],
    )
    review_path = output.with_name(f"{output.stem}.can-xem{output.suffix}")
    unopened_path = output.with_name(f"{output.stem}.chua-mo{output.suffix}")
    if table.review:
        _write_csv(
            review_path,
            ["Số điện thoại", "Tên danh bạ", "Tên hồ sơ", "Username", "Lý do"],
            [
                [item.phone, item.contact_name, item.profile_name, item.username, item.reason]
                for item in table.review
            ],
        )
        written.append(review_path)
    elif review_path.exists():
        review_path.unlink()
    if table.unopened:
        _write_csv(
            unopened_path,
            ["Số điện thoại", "Tên"],
            [[item.phone, item.name] for item in table.unopened],
        )
        written.append(unopened_path)
    elif unopened_path.exists():
        unopened_path.unlink()
    return written


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def format_summary(table: Table) -> str:
    lines = [f"Bảng chính: {len(table.rows)} hàng đủ số, tên và username."]
    if table.rows:
        lines.append("Số điện thoại | Tên | Username")
        for row in table.rows:
            lines.append(f"{row.phone} | {row.name} | {row.username}")
    lines.append(f"Cần xem: {len(table.review)} hàng.")
    for item in table.review:
        lines.append(
            f"- {item.phone or '—'} | {item.contact_name or item.profile_name} | {item.username or '—'} | {item.reason}"
        )
    lines.append(f"Chưa mở hồ sơ: {len(table.unopened)} số.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Đọc video danh bạ TikTok và xuất bảng 3 cột.")
    parser.add_argument("inputs", nargs="+", help="Video hoặc ảnh, theo đúng thứ tự trong video.")
    parser.add_argument("-o", "--output", default="bang.csv", help="File CSV bảng chính. Mặc định bang.csv.")
    parser.add_argument("--fps", type=float, default=3.0, help="Số khung lấy mỗi giây trước khi lọc khung đứng yên.")
    parser.add_argument("--chi-tiet", action="store_true", help="In những gì đọc được trên từng khung.")
    args = parser.parse_args(argv)
    paths = [Path(item) for item in args.inputs]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        print("Không thấy file: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        table, frames = scan_paths(paths, fps=args.fps)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    written = write_table(table, Path(args.output))
    if args.chi_tiet:
        for path, obs in frames:
            if obs.kind == "list":
                picked = [hit.phone for hit in obs.contacts if hit.selected]
                print(f"{path.name}: danh bạ, {len(obs.contacts)} dòng, đang chọn {picked or 'không'}", file=sys.stderr)
                for hit in obs.contacts:
                    mark = " *" if hit.selected else ""
                    print(f"  {hit.phone} {hit.name}{mark}", file=sys.stderr)
            elif obs.kind == "profile":
                print(f"{path.name}: hồ sơ {obs.profile_name} {obs.profile_username}", file=sys.stderr)
            else:
                print(f"{path.name}: bỏ qua", file=sys.stderr)
    print(format_summary(table))
    print("Đã ghi " + ", ".join(str(path) for path in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
