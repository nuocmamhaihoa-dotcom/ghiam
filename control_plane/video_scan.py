"""Đọc video hoặc ảnh chụp màn hình danh bạ, xuất bảng số điện thoại, tên, username.

Lượt một quét ảnh xám nhỏ @30fps (A1), biết lúc đứng yên / chuyển cảnh.
Quanh chuyển cảnh tách thêm burst @60fps (A3) để không bỏ hồ sơ mở rất ngắn.
Chỉ lấy luồng hình — không phân tích audio. ROI theo máy (B2) + calibrate đầu video (B3).
Khi có nhiều nhân: giải mã khung chính và burst cùng lúc, OCR ngay khi từng ảnh đã ghi xong.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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
from control_plane.screen_table import FrameObs, Table, pair_by_same_name

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
# Ba ô Số + Tên + @ của một hàng nằm trong 4 giây. Chỗ thiếu ô thì đọc lại ±4 giây.
REFILL_RADIUS_SEC = 4.0
REFILL_FPS = 10.0
# Mỗi luồng đọc tự đặt số luồng ffmpeg. 0 = ffmpeg tự lấy hết nhân.
_FFMPEG_THREADS = threading.local()


def use_ffmpeg_threads(count: int) -> None:
    """Giới hạn luồng giải mã của luồng đang đọc. Nhiều video cùng lúc thì chia nhân."""
    _FFMPEG_THREADS.n = max(0, int(count))


def _ffmpeg_threads_arg() -> str:
    count = int(getattr(_FFMPEG_THREADS, "n", 0) or 0)
    if count <= 0:
        return "0"
    return str(count)

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
        ordered = _attach_optional_vision(ordered, paths)
        frames = [obs for _, obs in ordered]
        # Cùng tên khách thì một hàng: số, tên, @. Không ghép theo thời gian hay độ giống.
        return pair_by_same_name(frames), ordered
    finally:
        for folder in temps:
            folder.cleanup()


def _attach_optional_vision(
    ordered: list[tuple[Path, FrameObs]],
    paths: list[Path],
) -> list[tuple[Path, FrameObs]]:
    """Chữ Vision gửi kèm là phần thêm. Thiếu module hoặc file hỏng thì giữ kết quả máy chủ."""
    try:
        from control_plane.vision_sidecar import attach_vision
    except ImportError as exc:
        print(f"Không có vision_sidecar, giữ kết quả đã đọc: {exc}", flush=True)
        return ordered
    try:
        return attach_vision(ordered, paths)
    except Exception as exc:
        print(f"Bỏ qua chữ Vision kèm theo, giữ kết quả đã đọc: {exc}", flush=True)
        return ordered


def _apply_optional_fallbacks(table: Table, ordered: list[tuple[Path, FrameObs]], on_progress: Progress | None) -> Table:
    """Cách 2 chỉ bổ sung. Nếu bước này lỗi, hàng cách 3 vẫn được lưu."""
    try:
        from control_plane.read_fallback import apply_read_fallbacks

        return apply_read_fallbacks(table, ordered, on_progress=on_progress)
    except Exception as exc:
        print(f"Cách 2 bỏ qua, giữ hàng đã ghép: {exc}", flush=True)
        return table


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
    # submit is None giữ đường cũ (tách xong rồi mới đọc) để test giả lập extract không bị thêm tham số.
    if submit is None:
        jobs, results, layout_dict, measured = _scan_sequential(
            path, folder, plan, fps, on_progress, device, layout,
        )
    else:
        jobs, results, layout_dict, measured = _scan_overlapped(
            path, folder, plan, fps, submit, on_progress, device, layout,
        )
    results = _round2_unknowns(jobs, results, submit=submit, on_progress=on_progress, layout_dict=layout_dict)
    learned = measured if measured is not None else None
    observations = [(job[2], obs) for job, obs in zip(jobs, results)]
    observations = _refill_missing_fields(
        path,
        folder,
        observations,
        submit=submit,
        on_progress=on_progress,
        layout_dict=layout_dict,
    )
    return observations, learned


def _scan_sequential(
    path: Path,
    folder: Path,
    plan: VideoPlan,
    fps: float,
    on_progress: Progress | None,
    device: str,
    layout: LayoutProfile | None,
) -> tuple[list[tuple[float, int, Path, Callable[..., FrameObs], tuple]], list[FrameObs], dict[str, object], LayoutProfile | None]:
    """Tách hết khung rồi mới OCR. Dùng khi không có pool (và các test giả lập extract)."""
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
    # Khung đơn gần như giống khung vừa đọc (cùng màn hình giữ yên) không OCR lại.
    copy_from = _alias_single_frames(jobs)
    ocr_total = len(jobs) - len(copy_from)
    progress_every_sec = 10.0
    step = max(1, min(100, ocr_total // 50)) if ocr_total else 1
    skipped_note = f" (bỏ {len(copy_from)} khung trùng)" if copy_from else ""
    if on_progress is not None:
        on_progress(f"Đang đọc chữ 0/{ocr_total} khung{skipped_note}")
    results: list[FrameObs] = [FrameObs("unknown")] * len(jobs)
    last_beat = time.monotonic()
    done = 0
    for index, (_, _, _, fn, args) in enumerate(jobs):
        if index in copy_from:
            continue
        results[index] = fn(*args)
        done += 1
        now = time.monotonic()
        if on_progress is not None and (
            done == ocr_total or done % step == 0 or now - last_beat >= progress_every_sec
        ):
            on_progress(f"Đang đọc chữ {done}/{ocr_total} khung{skipped_note}")
            last_beat = now
    for dest, src in copy_from.items():
        obs = results[src]
        at = jobs[dest][0]
        results[dest] = FrameObs("unknown", at=at) if obs.kind == "unknown" else dataclasses.replace(obs, at=at)
    return jobs, results, layout_dict, measured



def _ffmpeg_budget() -> int:
    """Số luồng ffmpeg của video đang đọc. 0 nghĩa là ffmpeg tự lấy hết nhân."""
    count = int(getattr(_FFMPEG_THREADS, "n", 0) or 0)
    if count <= 0:
        return max(1, os.cpu_count() or 2)
    return count


def _decode_ocr_budget(budget: int, decodes: int, cpus: int) -> tuple[int, int]:
    """Luồng ffmpeg mỗi lần giải mã, và số OCR chạy song song khi ffmpeg chưa xong.

    Một luồng H.264 chỉ ăn khoảng 4–5 nhân. Phần còn lại đọc chữ ngay, không chờ
    giải mã hết video. `decodes` là 2 khi có burst, 1 khi chỉ có lượt khung chính.
    """
    decodes = max(1, int(decodes))
    budget = max(1, int(budget))
    cpus = max(1, int(cpus))
    each = budget if decodes == 1 else max(2, budget // 2)
    # Không xếp nhiều việc OCR hơn số nhân trong lúc ffmpeg còn chạy.
    slots = min(cpus, max(2, budget - decodes * 4))
    return each, slots


@dataclass(frozen=True)
class _FrameJob:
    at: float
    kind: int
    source: str
    number: int
    window: tuple[int, ...] = ()


def _plan_ocr_jobs(plan: VideoPlan, burst_numbers: list[int], fps: float) -> list[_FrameJob]:
    """Cùng thứ tự job với đường tách-xong-rồi-đọc: khung đọc, cửa sổ bấm, burst, rồi sort theo thời gian."""
    jobs: list[_FrameJob] = []
    read_numbers = set(plan.reads)
    for number in plan.reads:
        jobs.append(_FrameJob(number / fps, 0, "main", number))
    for window in plan.taps:
        last = window[-1]
        jobs.append(_FrameJob(last / fps, 1, "tap", last, tuple(window)))
        for number in window[-BRIEF_EXIT_READS:]:
            if number in read_numbers:
                continue
            read_numbers.add(number)
            jobs.append(_FrameJob(number / fps, 0, "main", number))
    for number in burst_numbers:
        jobs.append(_FrameJob(number / BURST_FPS, 0, "burst", number))
    jobs.sort(key=lambda job: (job.at, job.kind))
    return jobs


class _EarlierScreens:
    """Chỉ lấy kết quả của một khung ĐÃ có, thời điểm sớm hơn, trong 2 giây.

    Khung đến sau nhưng có mốc thời gian sớm hơn không được dùng làm nguồn —
    đọc thừa thì được, gán nhầm kết quả của khung sau cho khung trước thì không.
    """

    def __init__(self) -> None:
        self.recent: list[tuple[float, tuple[int, int], int]] = []

    def match(self, at: float, digest: tuple[int, int] | None) -> int | None:
        if digest is None or not self.recent:
            return None
        lo, hi = 0, len(self.recent)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.recent[mid][0] <= at:
                lo = mid + 1
            else:
                hi = mid
        index = lo - 1
        while index >= 0:
            prev_at, prev_hash, slot = self.recent[index]
            if at - prev_at > _DHASH_WINDOW_SEC:
                break
            if _same_screen(digest, prev_hash):
                return slot
            index -= 1
        return None

    def remember(self, at: float, digest: tuple[int, int] | None, slot: int) -> None:
        if digest is None:
            return
        lo, hi = 0, len(self.recent)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.recent[mid][0] <= at:
                lo = mid + 1
            else:
                hi = mid
        self.recent.insert(lo, (at, digest, slot))


class _LiveOcr:
    """OCR ngay khi JPEG đã đóng, trong lúc ffmpeg còn đang giải mã."""

    def __init__(
        self,
        plan: VideoPlan,
        burst_numbers: list[int],
        fps: float,
        submit: Submit,
        on_progress: Progress | None,
        device: str,
        layout: LayoutProfile | None,
        *,
        ocr_slots: int,
        cpus: int,
        decode_count: int,
    ) -> None:
        self.jobs = _plan_ocr_jobs(plan, burst_numbers, fps)
        self.submit = submit
        self.on_progress = on_progress
        self.device = device
        self.base = layout or LayoutProfile(device=device or "default")
        self.cal_numbers = list(plan.reads[:24])
        self.ocr_slots = max(1, int(ocr_slots))
        self.cpus = max(1, int(cpus))
        self.decode_count = max(1, int(decode_count))
        self.slots = threading.Semaphore(self.ocr_slots)
        self.slot_lock = threading.Lock()
        self.exits = 0
        self.opened = False
        self.cv = threading.Condition()
        self.progress_lock = threading.Lock()
        self.main_files: dict[int, Path] = {}
        self.burst_files: dict[int, Path] = {}
        self.bound: dict[int, Path] = {}
        self.main_wait: dict[int, list[int]] = {}
        self.burst_wait: dict[int, list[int]] = {}
        self.tap_by_frame: dict[int, list[int]] = {}
        for index, job in enumerate(self.jobs):
            if job.source == "main":
                self.main_wait.setdefault(job.number, []).append(index)
            elif job.source == "burst":
                self.burst_wait.setdefault(job.number, []).append(index)
            else:
                for number in job.window:
                    self.tap_by_frame.setdefault(number, []).append(index)
        self.alias = _EarlierScreens()
        self.copy_from: dict[int, int] = {}
        self.dispatched: set[int] = set()
        self.claiming: set[int] = set()
        self.futures: dict[int, Future] = {}
        self.results: list[FrameObs | None] = [None] * len(self.jobs)
        self.got: set[int] = set()
        self.errors: list[BaseException] = []
        self.reported_error = False
        self.flushed = False
        self.calibrated = False
        self.calibrating = False
        self.measured: LayoutProfile | None = None
        self.layout_dict: dict[str, object] = self.base.to_dict()
        self.announced = False
        self.done = 0
        self.alias_count = 0
        self.goal = len(self.jobs)
        self.step = max(1, min(100, len(self.jobs) // 50)) if self.jobs else 1
        self.last_beat = time.monotonic()
        if not self.cal_numbers:
            self._apply_calibration([])

    def on_main(self, number: int, path: Path) -> None:
        self._arrive("main", number, path)

    def on_burst(self, number: int, path: Path) -> None:
        self._arrive("burst", number, path)

    def on_decoded(self) -> None:
        """Một lần giải mã đã thoát. Cả hai xong thì nhả hết chỗ OCR để dùng đầy CPU."""
        announce = False
        with self.slot_lock:
            self.exits += 1
            if self.exits >= self.decode_count and not self.opened:
                self.opened = True
                extra = max(0, self.cpus - self.ocr_slots)
                for _ in range(extra):
                    self.slots.release()
                announce = extra > 0
        if announce and self.on_progress is not None:
            self.on_progress("Đã giải mã xong, đọc chữ dùng hết nhân")

    def open_slots(self) -> None:
        with self.slot_lock:
            if self.opened:
                return
            self.opened = True
            extra = max(0, self.cpus - self.ocr_slots)
            for _ in range(extra):
                self.slots.release()

    def assemble(
        self,
    ) -> tuple[list[tuple[float, int, Path, Callable[..., FrameObs], tuple]], list[FrameObs], dict[str, object], LayoutProfile | None]:
        self._ensure_calibrated()
        with self.cv:
            claimed = self._claim_all()
            self.flushed = True
        self._dispatch_claimed(claimed)
        self.open_slots()
        self.wait_all(raise_error=True)
        results: list[FrameObs] = []
        for index, job in enumerate(self.jobs):
            if index in self.copy_from:
                src = self.copy_from[index]
                obs = self.results[src]
                if obs is None or obs.kind == "unknown":
                    results.append(FrameObs("unknown", at=job.at))
                else:
                    results.append(dataclasses.replace(obs, at=job.at))
                continue
            obs = self.results[index]
            results.append(obs if obs is not None else FrameObs("unknown", at=job.at))
        return self._materialize(), results, self.layout_dict, self.measured

    def wait_all(self, *, raise_error: bool) -> None:
        with self.cv:
            items = list(self.futures.items())
        for index, future in items:
            if index in self.got:
                continue
            try:
                self.results[index] = future.result()
            except BaseException as exc:
                self.errors.append(exc)
            self.got.add(index)
        if not self.errors:
            return
        if raise_error:
            raise self.errors[0]
        if not self.reported_error:
            self.reported_error = True
            print(f"OCR nền lỗi: {self.errors[0]}", flush=True)

    def _arrive(self, source: str, number: int, path: Path) -> None:
        claim_calibration = False
        cal_paths: list[Path] = []
        with self.cv:
            store = self.main_files if source == "main" else self.burst_files
            store[number] = path
            if not self.calibrated and not self.calibrating and self._cal_ready():
                self.calibrating = True
                claim_calibration = True
                cal_paths = [self.main_files[n] for n in self.cal_numbers]
        if claim_calibration:
            try:
                self._apply_calibration(cal_paths)
            except BaseException:
                with self.cv:
                    self.calibrating = False
                    self.cv.notify_all()
                raise
            with self.cv:
                claimed = self._claim_all()
                self.flushed = True
                self.cv.notify_all()
            self._dispatch_claimed(claimed)
            return
        with self.cv:
            while self.calibrating:
                if not self.cv.wait(timeout=3600):
                    raise RuntimeError("Hết hạn chờ khóa bố cục video.")
            if not self.calibrated:
                return
            if not self.flushed:
                claimed = self._claim_all()
                self.flushed = True
            else:
                claimed = self._claim_one(source, number)
        self._dispatch_claimed(claimed)

    def _ensure_calibrated(self) -> None:
        with self.cv:
            while self.calibrating:
                if not self.cv.wait(timeout=3600):
                    raise RuntimeError("Hết hạn chờ khóa bố cục video.")
            if self.calibrated:
                return
            paths = [self.main_files[n] for n in self.cal_numbers if n in self.main_files]
            self.calibrating = True
        try:
            self._apply_calibration(paths)
        except BaseException:
            with self.cv:
                self.calibrating = False
                self.cv.notify_all()
            raise
        with self.cv:
            self.cv.notify_all()

    def _apply_calibration(self, paths: list[Path]) -> None:
        measured = calibrate_from_paths(paths)
        locked = merge_layout(self.base, measured, weight=0.75) if measured is not None else self.base
        locked.device = self.device or self.base.device or "default"
        if measured is not None and self.on_progress is not None:
            self.on_progress(f"Đã khóa bố cục đầu video («{locked.device}»)")
        with self.cv:
            self.measured = measured
            self.layout_dict = locked.to_dict()
            self.calibrated = True
            self.calibrating = False

    def _cal_ready(self) -> bool:
        return all(number in self.main_files for number in self.cal_numbers)

    def _ready(self, job: _FrameJob) -> bool:
        if job.source == "burst":
            return job.number in self.burst_files
        if job.source == "tap":
            return all(number in self.main_files for number in job.window)
        return job.number in self.main_files

    def _claim_all(self) -> list[int]:
        picked: list[int] = []
        for index, job in enumerate(self.jobs):
            if index in self.dispatched or index in self.claiming or not self._ready(job):
                continue
            self.claiming.add(index)
            self.bound[index] = self._bind_path(job)
            picked.append(index)
        picked.sort(key=lambda index: (self.jobs[index].at, self.jobs[index].kind))
        return picked

    def _claim_one(self, source: str, number: int) -> list[int]:
        if source == "main":
            indexes = list(self.main_wait.get(number, []))
            indexes.extend(self.tap_by_frame.get(number, []))
        else:
            indexes = list(self.burst_wait.get(number, []))
        picked: list[int] = []
        seen: set[int] = set()
        for index in indexes:
            if index in seen or index in self.dispatched or index in self.claiming:
                continue
            seen.add(index)
            job = self.jobs[index]
            if not self._ready(job):
                continue
            self.claiming.add(index)
            self.bound[index] = self._bind_path(job)
            picked.append(index)
        picked.sort(key=lambda index: (self.jobs[index].at, self.jobs[index].kind))
        return picked

    def _bind_path(self, job: _FrameJob) -> Path:
        if job.source == "burst":
            return self.burst_files[job.number]
        return self.main_files[job.number]

    def _dispatch_claimed(self, claimed: list[int]) -> None:
        if not claimed:
            return
        digests: dict[int, tuple[int, int] | None] = {}
        for index in claimed:
            if self.jobs[index].kind != 0:
                continue
            digests[index] = _frame_dhash(str(self.bound[index]))
        planned: list[tuple[int, Callable[..., FrameObs], tuple]] = []
        announce: str | None = None
        with self.cv:
            for index in claimed:
                if index in self.dispatched:
                    self.claiming.discard(index)
                    continue
                job = self.jobs[index]
                digest = digests.get(index)
                if job.kind == 0:
                    src = self.alias.match(job.at, digest)
                    if src is not None:
                        self.copy_from[index] = src
                        self.dispatched.add(index)
                        self.claiming.discard(index)
                        with self.progress_lock:
                            self.alias_count += 1
                            self.goal = max(0, len(self.jobs) - self.alias_count)
                        continue
                    self.alias.remember(job.at, digest, index)
                planned.append((index, *self._call(index)))
                self.dispatched.add(index)
                self.claiming.discard(index)
            if not self.announced and self.on_progress is not None and (planned or self.alias_count):
                self.announced = True
                note = f" (bỏ {self.alias_count} khung trùng)" if self.alias_count else ""
                announce = f"Đang đọc chữ 0/{self.goal} khung{note}"
        if announce is not None and self.on_progress is not None:
            self.on_progress(announce)
        for index, fn, args in planned:
            # Chờ chỗ OCR ngoài khóa, để luồng giải mã kia vẫn ghi nhận khung mới.
            self.slots.acquire()
            try:
                future = self.submit(fn, *args)
            except BaseException:
                self.slots.release()
                raise
            with self.cv:
                self.futures[index] = future
            future.add_done_callback(self._ocr_done)

    def _call(self, index: int) -> tuple[Callable[..., FrameObs], tuple]:
        job = self.jobs[index]
        if job.source == "tap":
            paths = [str(self.main_files[number]) for number in job.window]
            return read_tap_at, (paths, job.at)
        return read_frame_at, (str(self.bound[index]), job.at, self.layout_dict)

    def _ocr_done(self, _future: Future) -> None:
        self.slots.release()
        if self.on_progress is None:
            return
        with self.progress_lock:
            self.done += 1
            done = self.done
            goal = self.goal
            aliases = self.alias_count
            now = time.monotonic()
            report = done == goal or done % self.step == 0 or now - self.last_beat >= 10.0
            if report:
                self.last_beat = now
        if not report or self.on_progress is None:
            return
        note = f" (bỏ {aliases} khung trùng)" if aliases else ""
        self.on_progress(f"Đang đọc chữ {done}/{goal} khung{note}")

    def _materialize(self) -> list[tuple[float, int, Path, Callable[..., FrameObs], tuple]]:
        jobs: list[tuple[float, int, Path, Callable[..., FrameObs], tuple]] = []
        for index, job in enumerate(self.jobs):
            if job.source == "tap":
                paths = [str(self.main_files[number]) for number in job.window]
                last = self.main_files[job.number]
                jobs.append((job.at, 1, last, read_tap_at, (paths, job.at)))
                continue
            path = self.bound.get(index)
            if path is None:
                path = self._bind_path(job)
            jobs.append((job.at, 0, path, read_frame_at, (str(path), job.at, self.layout_dict)))
        return jobs


def _scan_overlapped(
    path: Path,
    folder: Path,
    plan: VideoPlan,
    fps: float,
    submit: Submit,
    on_progress: Progress | None,
    device: str,
    layout: LayoutProfile | None,
) -> tuple[list[tuple[float, int, Path, Callable[..., FrameObs], tuple]], list[FrameObs], dict[str, object], LayoutProfile | None]:
    """Giải mã lượt chính và burst cùng lúc. OCR bắt đầu khi từng JPEG đã đóng."""
    needed = plan.needed()
    times = plan.transition_times(fps)
    burst_numbers = burst_frame_numbers(times)
    existing_times = {number / fps for number in needed}
    filtered = [
        number
        for number in burst_numbers
        if not any(abs(number / BURST_FPS - moment) < (0.5 / fps) for moment in existing_times)
    ]
    if on_progress is not None:
        on_progress(f"Đang tách {len(needed)} khung từ {path.name}")
        if filtered:
            on_progress(f"Đang tách burst {len(filtered)} khung @ {BURST_FPS:.0f}fps")
    cpus = max(1, os.cpu_count() or 1)
    each, slots = _decode_ocr_budget(_ffmpeg_budget(), 2 if filtered else 1, cpus)
    live = _LiveOcr(
        plan,
        filtered,
        fps,
        submit,
        on_progress,
        device,
        layout,
        ocr_slots=slots,
        cpus=cpus,
        decode_count=2 if filtered else 1,
    )
    errors: list[BaseException] = []

    def _run(
        numbers: list[int],
        fps_value: float,
        prefix: str,
        ready: Callable[[int, Path], None],
    ) -> None:
        try:
            written = extract_frames(
                path,
                folder,
                numbers,
                fps_value,
                on_progress=on_progress,
                prefix=prefix,
                threads=each,
                on_ready=ready,
                on_decoded=live.on_decoded,
            )
            # on_ready đã nhận hầu hết khung. Gọi lại lần nữa để không mất khung nếu watcher sót file cuối.
            for number, frame_path in written.items():
                ready(number, frame_path)
        except BaseException as exc:
            errors.append(exc)

    workers = [
        threading.Thread(
            target=_run,
            args=(needed, fps, "f", live.on_main),
            name="extract-main",
        )
    ]
    if filtered:
        workers.append(
            threading.Thread(
                target=_run,
                args=(filtered, BURST_FPS, "b", live.on_burst),
                name="extract-burst",
            )
        )
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        if errors:
            raise errors[0]
        return live.assemble()
    finally:
        live.open_slots()
        live.wait_all(raise_error=False)


def incomplete_anchors(frames: list[FrameObs], table: Table) -> list[float]:
    """Thời điểm của hàng chưa đủ Số + Tên + @ — mốc để đọc lại ±4 giây."""
    from control_plane.screen_table import clean_username, normalize_phone

    phone_seen: dict[str, float] = {}
    phone_open: dict[str, float] = {}
    user_at: dict[str, float] = {}
    for frame in frames:
        if frame.at < 0:
            continue
        if frame.kind == "profile":
            user = clean_username(frame.profile_username)
            if user and user not in user_at:
                user_at[user] = frame.at
        for hit in frame.contacts:
            phone = normalize_phone(hit.phone)
            if not phone:
                continue
            if hit.selected or frame.kind == "tap":
                phone_open[phone] = frame.at
            elif phone not in phone_seen:
                phone_seen[phone] = frame.at

    def phone_time(phone: str) -> float:
        if phone in phone_open:
            return phone_open[phone]
        return phone_seen.get(phone, -1.0)

    times: list[float] = []
    seen: set[tuple[str, str]] = set()

    def add_phone(phone: str) -> None:
        if not phone or ("p", phone) in seen:
            return
        at = phone_time(phone)
        if at < 0:
            return
        seen.add(("p", phone))
        times.append(at)

    def add_user(username: str) -> None:
        user = clean_username(username)
        if not user or ("u", user) in seen:
            return
        at = user_at.get(user, -1.0)
        if at < 0:
            return
        seen.add(("u", user))
        times.append(at)

    for row in table.rows:
        if row.phone and row.name and row.username:
            continue
        add_phone(row.phone)
        add_user(row.username)
    for item in table.unopened:
        add_phone(item.phone)
    for item in table.review:
        name = item.contact_name or item.profile_name
        if item.phone and name and item.username:
            continue
        add_phone(item.phone)
        add_user(item.username)
    return times


def merge_time_windows(times: list[float], radius: float = REFILL_RADIUS_SEC) -> list[tuple[float, float]]:
    """Gộp các khoảng ±radius chồng nhau thành một đoạn đọc lại."""
    if not times:
        return []
    spans = sorted((max(0.0, moment - radius), moment + radius) for moment in times)
    merged: list[tuple[float, float]] = [spans[0]]
    for start, end in spans[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1e-6:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _time_covered(sorted_times: list[float], at: float, tol: float) -> bool:
    import bisect

    index = bisect.bisect_left(sorted_times, at)
    if index < len(sorted_times) and abs(sorted_times[index] - at) <= tol:
        return True
    if index > 0 and abs(sorted_times[index - 1] - at) <= tol:
        return True
    return False


def refill_frame_numbers(
    windows: list[tuple[float, float]],
    already: list[float],
    *,
    fps: float = REFILL_FPS,
    duration: float = 0.0,
) -> list[int]:
    """Số khung @fps trong các khoảng, bỏ thời điểm đã OCR và không vượt quá cuối video."""
    if not windows or fps <= 0:
        return []
    limit = duration if duration > 0 else 1e12
    covered = sorted(moment for moment in already if moment >= 0)
    tol = 0.45 / fps
    picks: list[int] = []
    seen: set[int] = set()
    for start, end in windows:
        start = max(0.0, start)
        last = min(end, limit - 0.5 / fps) if duration > 0 else end
        if last < start:
            continue
        n0 = int(start * fps + 1e-6)
        n1 = int(last * fps + 1e-6)
        for number in range(max(0, n0), n1 + 1):
            if number in seen:
                continue
            at = number / fps
            if _time_covered(covered, at, tol):
                continue
            seen.add(number)
            picks.append(number)
    return picks


def _refill_missing_fields(
    path: Path,
    folder: Path,
    observations: list[tuple[Path, FrameObs]],
    *,
    submit: Submit | None,
    on_progress: Progress | None,
    layout_dict: dict[str, object],
) -> list[tuple[Path, FrameObs]]:
    """Hàng thiếu ô: đọc lại thật kỹ 4 giây trước và 4 giây sau khung đó."""
    from control_plane.video_validate import probe_duration_sec

    frames = [obs for _file, obs in observations]
    if not any(obs.at >= 0 for obs in frames):
        return observations
    table = pair_by_same_name(frames)
    anchors = incomplete_anchors(frames, table)
    if not anchors:
        return observations
    windows = merge_time_windows(anchors)
    duration = probe_duration_sec(Path(path))
    numbers = refill_frame_numbers(windows, [obs.at for obs in frames], duration=duration)
    if not numbers:
        return observations
    if on_progress is not None:
        on_progress(f"Đọc lại ±4 giây quanh {len(anchors)} chỗ thiếu ({len(numbers)} khung)")
    try:
        files = extract_frames(path, folder, numbers, REFILL_FPS, on_progress=on_progress, prefix="g")
    except RuntimeError as exc:
        print(f"Đọc lại ±4 giây bỏ qua: {exc}", flush=True)
        return observations
    if not files:
        return observations
    targets = [(index, str(file_path), number / REFILL_FPS) for index, (number, file_path) in enumerate(sorted(files.items()))]
    unique, _copies = _collapse_similar_frames(targets)
    total = len(unique)
    if on_progress is not None:
        on_progress(f"Đọc lại ±4 giây: OCR {total} khung khác nhau")
    found: list[FrameObs | None] = [None] * total

    def _take(slot: int, obs: FrameObs) -> None:
        found[slot] = obs

    if submit is None:
        for slot, (_index, file_path, at) in enumerate(unique):
            _take(slot, read_frame_aggressive_at(file_path, at, layout_dict))
            if on_progress is not None and (slot + 1 == total or (slot + 1) % 25 == 0):
                on_progress(f"Đọc lại ±4 giây {slot + 1}/{total}")
    else:
        done = 0
        batch = 64
        for start in range(0, total, batch):
            chunk = unique[start : start + batch]
            future_map = {
                submit(read_frame_aggressive_at, file_path, at, layout_dict): start + offset
                for offset, (_index, file_path, at) in enumerate(chunk)
            }
            for future in as_completed(future_map):
                slot = future_map[future]
                try:
                    obs = future.result()
                except Exception as exc:
                    print(f"Đọc lại ±4 giây lỗi: {exc}", flush=True)
                    done += 1
                    continue
                found[slot] = obs
                done += 1
                if on_progress is not None and (done == total or done % 25 == 0):
                    on_progress(f"Đọc lại ±4 giây {done}/{total}")
    extra: list[tuple[Path, FrameObs]] = []
    for slot, (_index, file_path, at) in enumerate(unique):
        obs = found[slot]
        if obs is None or obs.kind not in ("list", "profile", "tap"):
            continue
        extra.append((Path(file_path), dataclasses.replace(obs, at=at)))
    if not extra:
        return observations
    # Thứ tự thời gian: số ở danh bạ và @ ở hồ sơ phải đi đúng trước/sau trong 4 giây.
    merged = list(observations) + extra
    merged.sort(key=lambda item: item[1].at if item[1].at >= 0 else -1.0)
    return merged


# Cùng màn hình trong 2 giây khi cả vùng @ và cột chữ danh bạ không đổi.
# Đồng hồ ở status bar không buộc đọc lại. Một dòng danh bạ hoặc @ đổi thì đọc.
_DHASH_WINDOW_SEC = 2.0
_DHASH_MAX_DISTANCE = 4
_DHASH_HEADER_DISTANCE = 2


def _dhash_bits(gray: object) -> int:
    from PIL import Image

    small = gray.resize((9, 8), Image.Resampling.BOX)  # type: ignore[attr-defined]
    pixels = small.tobytes()
    if len(pixels) < 72:
        return 0
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits = (bits << 1) | (1 if pixels[row + x] > pixels[row + x + 1] else 0)
    return bits


def _frame_dhash(path: str) -> tuple[int, int] | None:
    """Hash vùng @ (9–22% cao) và cột chữ danh bạ. None nếu không mở được ảnh."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            gray = image.convert("L")
            width, height = gray.size
            at_top = int(0.09 * height)
            at_box = gray.crop(
                (
                    int(0.04 * width),
                    at_top,
                    max(int(0.04 * width) + 1, int(0.86 * width)),
                    max(at_top + 1, int(0.22 * height)),
                )
            )
            col_top = int(0.12 * height)
            column = gray.crop(
                (
                    int(0.18 * width),
                    col_top,
                    max(int(0.18 * width) + 1, int(0.72 * width)),
                    max(col_top + 1, int(0.92 * height)),
                )
            )
            return _dhash_bits(at_box), _dhash_bits(column)
    except OSError:
        return None


def _same_screen(left: object, right: object) -> bool:
    if isinstance(left, tuple) and isinstance(right, tuple) and len(left) == 2 and len(right) == 2:
        # [0] là vùng @, [1] là cột danh bạ. Một trong hai đổi thì đọc lại.
        at_zone = (int(left[0]) ^ int(right[0])).bit_count() <= _DHASH_HEADER_DISTANCE
        column = (int(left[1]) ^ int(right[1])).bit_count() <= _DHASH_MAX_DISTANCE
        return at_zone and column
    if isinstance(left, int) and isinstance(right, int):
        return (left ^ right).bit_count() <= _DHASH_MAX_DISTANCE
    return False


def _collapse_similar_frames(
    targets: list[tuple[int, str, float]],
) -> tuple[list[tuple[int, str, float]], list[tuple[int, int, float]]]:
    """Giữ một khung cho mỗi cụm ảnh giống nhau trong 2 giây.

    Phần còn lại nhận cùng kết quả OCR (cùng màn hình), không đọc lại.
    `targets` phải tăng dần theo thời gian.
    """
    unique: list[tuple[int, str, float]] = []
    copies: list[tuple[int, int, float]] = []
    if len(targets) >= 24:
        workers = min(8, len(targets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            digests = list(pool.map(lambda item: _frame_dhash(item[1]), targets, chunksize=16))
    else:
        digests = [_frame_dhash(path) for _index, path, _at in targets]
    recent: list[tuple[float, object, int]] = []
    for (index, path, at), digest in zip(targets, digests):
        matched: int | None = None
        if digest is not None:
            for prev_at, prev_hash, slot in reversed(recent):
                if at - prev_at > _DHASH_WINDOW_SEC:
                    break
                if _same_screen(digest, prev_hash):
                    matched = slot
                    break
        if matched is None:
            slot = len(unique)
            unique.append((index, path, at))
            if digest is not None:
                recent.append((at, digest, slot))
                if len(recent) > 48:
                    recent = [item for item in recent if at - item[0] <= _DHASH_WINDOW_SEC]
        else:
            copies.append((index, matched, at))
    return unique, copies


def _alias_single_frames(
    jobs: list[tuple[float, int, Path, Callable[..., FrameObs], tuple]],
) -> dict[int, int]:
    """Chỉ số khung đơn trùng → chỉ số khung gốc đã (hoặc sẽ) được OCR."""
    targets: list[tuple[int, str, float]] = []
    for index, (at, kind, path, fn, _args) in enumerate(jobs):
        if kind != 0 or fn is not read_frame_at:
            continue
        targets.append((index, str(path), at))
    unique, copies = _collapse_similar_frames(targets)
    return {index: unique[slot][0] for index, slot, at in copies}


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
    unique, copies = _collapse_similar_frames(targets)
    total = len(unique)
    skipped = len(targets) - total
    if on_progress is not None:
        on_progress(
            f"Vòng 2 OCR {total} khung khác nhau"
            + (f" (bỏ {skipped} khung trùng)" if skipped else "")
        )
    updated = list(results)
    found: list[FrameObs | None] = [None] * total

    def _store(slot: int, obs: FrameObs) -> None:
        found[slot] = obs
        index, _path, _at = unique[slot]
        if obs.kind != "unknown":
            updated[index] = obs

    if submit is None:
        for offset, (_index, path, at) in enumerate(unique, start=1):
            _store(offset - 1, read_frame_aggressive_at(path, at, layout_dict))
            if on_progress is not None and (offset == total or offset % 25 == 0):
                on_progress(f"Vòng 2 OCR {offset}/{total} khung khác nhau")
    else:
        done = 0
        batch = 64
        for start in range(0, total, batch):
            chunk = unique[start : start + batch]
            future_map = {
                submit(read_frame_aggressive_at, path, at, layout_dict): (start + offset, index)
                for offset, (index, path, at) in enumerate(chunk)
            }
            for future in as_completed(future_map):
                slot, index = future_map[future]
                try:
                    obs = future.result()
                except Exception as exc:
                    print(f"Vòng 2 OCR lỗi: {exc}", flush=True)
                    done += 1
                    continue
                found[slot] = obs
                if obs.kind != "unknown":
                    updated[index] = obs
                done += 1
                if on_progress is not None and (done == total or done % 25 == 0):
                    on_progress(f"Vòng 2 OCR {done}/{total} khung khác nhau")
    for index, slot, at in copies:
        obs = found[slot] if slot < len(found) else None
        if obs is None or obs.kind == "unknown":
            continue
        updated[index] = dataclasses.replace(obs, at=at)
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
        _ffmpeg_threads_arg(),
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
        if "moov atom not found" in error.lower():
            message = (
                "File MP4/MOV thiếu moov (thường tải dở hoặc không phải video). "
                "Không đọc được — hãy tải lại bằng cắt khúc/VideoUp đến khi xong."
            )
        else:
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


def _select_expr(numbers: list[int]) -> str:
    """Biểu thức select gọn: một khoảng liên tục là between, không phải hàng nghìn eq."""
    ordered = sorted(set(numbers))
    if not ordered:
        return "0"
    parts: list[str] = []
    start = prev = ordered[0]
    for number in ordered[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append(f"eq(n,{start})" if start == prev else f"between(n,{start},{prev})")
        start = prev = number
    parts.append(f"eq(n,{start})" if start == prev else f"between(n,{start},{prev})")
    return "+".join(parts)


def extract_frames(
    path: Path,
    folder: Path,
    numbers: list[int],
    fps: float = SCAN_FPS,
    on_progress: Progress | None = None,
    prefix: str = "f",
    threads: int | None = None,
    on_ready: Callable[[int, Path], None] | None = None,
    on_decoded: Callable[[], None] | None = None,
) -> dict[int, Path]:
    """Lượt hai: chỉ ghi ra đĩa các khung đã chọn, đủ độ phân giải. Chỉ luồng hình.

    `on_ready` nhận từng JPEG sau khi ffmpeg đã đóng file (file sau đã mở, hoặc tiến trình đã thoát).
    Không truyền `threads` / `on_ready` / `on_decoded` thì đường cũ giữ nguyên.
    """
    if threads is not None or on_ready is not None or on_decoded is not None:
        return _extract_frames_stream(
            path,
            folder,
            numbers,
            fps,
            on_progress=on_progress,
            prefix=prefix,
            threads=threads,
            on_ready=on_ready,
            on_decoded=on_decoded,
        )
    _require_ffmpeg(path)
    if not numbers:
        return {}
    script = folder / f"select_{prefix}.txt"
    # Giảm xuống tối đa 1080px trước khi ghi: OCR cũng chỉ đọc tới 1080, ảnh lớn hơn chỉ làm chậm.
    script.write_text(
        "fps=%s,select='%s',scale=w='min(1080,iw)':h=-2:flags=bilinear"
        % (fps, _select_expr(numbers)),
        encoding="utf-8",
    )
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
                _ffmpeg_threads_arg(),
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
                "4",
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


def _extract_frames_stream(
    path: Path,
    folder: Path,
    numbers: list[int],
    fps: float,
    *,
    on_progress: Progress | None,
    prefix: str,
    threads: int | None,
    on_ready: Callable[[int, Path], None] | None,
    on_decoded: Callable[[], None] | None,
) -> dict[int, Path]:
    """Giải mã và báo từng khung đã ghi xong. Vẫn select theo số khung `n`, không tua theo thời gian."""
    _require_ffmpeg(path)
    notified = False

    def _notify() -> None:
        nonlocal notified
        if notified or on_decoded is None:
            return
        notified = True
        on_decoded()

    if not numbers:
        _notify()
        return {}
    ordered = sorted(set(numbers))
    script = folder / f"select_{prefix}.txt"
    script.write_text(
        "fps=%s,select='%s',scale=w='min(1080,iw)':h=-2:flags=bilinear"
        % (fps, _select_expr(numbers)),
        encoding="utf-8",
    )
    pattern = folder / f"{prefix}_%06d.jpg"
    thread_arg = _ffmpeg_threads_arg() if threads is None else str(max(0, int(threads)))
    stop = threading.Event()
    emit_lock = threading.Lock()
    emitted = 0
    ready_error: list[BaseException] = []
    proc_holder: list[subprocess.Popen[bytes]] = []

    def _emit(process_done: bool) -> None:
        nonlocal emitted
        while True:
            with emit_lock:
                index = emitted
                if index >= len(ordered):
                    return
                current = folder / f"{prefix}_{index + 1:06d}.jpg"
                following = folder / f"{prefix}_{index + 2:06d}.jpg"
                if not current.exists():
                    return
                # File đang mở chưa xong. File kế đã xuất hiện, hoặc ffmpeg đã thoát, thì file này đã đóng.
                if not process_done and not following.exists():
                    return
                emitted = index + 1
                number = ordered[index]
                ready = current
            if on_ready is not None:
                on_ready(number, ready)

    def _watch() -> None:
        last_report = time.monotonic()
        while not stop.wait(0.05):
            try:
                before = emitted
                _emit(False)
                now = time.monotonic()
                if on_progress is not None and emitted != before and now - last_report >= 5.0:
                    on_progress(f"Đang tách khung {emitted}/{len(ordered)}")
                    last_report = now
            except BaseException as exc:
                ready_error.append(exc)
                proc = proc_holder[0] if proc_holder else None
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                return
        try:
            _emit(True)
        except BaseException as exc:
            ready_error.append(exc)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        thread_arg,
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
        "4",
        str(pattern),
    ]
    timed_out = False
    code = 1
    error_text = ""
    watcher = threading.Thread(target=_watch, daemon=True)
    started = False
    try:
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors)
            proc_holder.append(proc)
            watcher.start()
            started = True
            try:
                code = proc.wait(timeout=3 * 3600)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait(timeout=30)
                code = proc.returncode if proc.returncode is not None else 1
            errors.seek(0)
            error_text = errors.read(8192).decode("utf-8", "replace")
    finally:
        try:
            _notify()
        except BaseException as exc:
            ready_error.append(exc)
        stop.set()
        if started:
            watcher.join()
    if ready_error:
        raise ready_error[0]
    if timed_out:
        raise RuntimeError(f"ffmpeg quá hạn khi tách khung {path.name}.")
    if code != 0:
        detail = error_text.strip().splitlines()[0] if error_text.strip() else ""
        raise RuntimeError(detail or f"ffmpeg không đọc được {path.name}.")
    written = sorted(folder.glob(f"{prefix}_*.jpg"))
    if len(written) < len(numbers):
        raise RuntimeError(f"Video {path.name} chỉ ra {len(written)}/{len(numbers)} khung cần đọc.")
    if emitted < len(ordered):
        _emit(True)
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
