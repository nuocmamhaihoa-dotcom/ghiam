"""Đọc video hoặc ảnh chụp màn hình danh bạ, xuất bảng số điện thoại, tên, username.

Lượt một quét ảnh xám rất nhỏ, 20 khung mỗi giây, để biết lúc nào màn hình
đứng yên và lúc nào bắt đầu chuyển. Lượt hai chỉ giải nén đầy đủ các khung
cần đọc: một khung cho mỗi cảnh đứng yên, và vài khung ngay trước mỗi lần
chuyển màn hình để tìm dòng vừa bấm. Ghép bằng tên; nếu video vừa bấm một
dòng rồi mở hồ sơ thì ghép theo lần bấm đó.
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

from control_plane.screen_read import read_image, read_tap
from control_plane.screen_table import FrameObs, Table, build_table

Progress = Callable[[str], None]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

# 20fps: hồ sơ mở ~0.1s còn ~2 khung; spike 1 khung ~50ms vẫn bắt được.
SCAN_FPS = 20.0
THUMB_W = 48
THUMB_H = 104
# ~0.05–0.1s @20fps: hai khung liên tiếp giống nhau là đủ đưa vào hàng đọc.
SETTLE_LAG = 1
SETTLED = 3.5
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên ngưỡng "đã đổi cảnh" phải rất thấp.
CHANGED = 1.2
MOVING = 2.5
# Hồ sơ vs danh bạ lệch rất mạnh; ngưỡng hơi thấp hơn để bắt flash nhạt / chuyển cảnh mờ.
FLASH_CHANGED = 9.0
# ~0.25s trước chuyển cảnh — đủ tìm chấm bấm; không phình OCR.
TAP_WINDOW = 5
# Chỉ OCR vài khung sát lúc thoát (hồ sơ ngắn). Cả TAP_WINDOW từng làm đọc gần như mọi khung khi cuộn.
BRIEF_EXIT_READS = 3

Submit = Callable[..., Future]


@dataclass
class VideoPlan:
    """Khung cần đọc, tính theo số thứ tự ở nhịp SCAN_FPS."""

    reads: list[int] = field(default_factory=list)
    taps: list[list[int]] = field(default_factory=list)
    count: int = 0

    def needed(self) -> list[int]:
        frames = set(self.reads)
        for window in self.taps:
            frames.update(window)
        return sorted(frames)


# Một khung hỏng hoặc một lần đọc chữ quá hạn chỉ làm mất khung đó, không làm hỏng cả video.
_FRAME_ERRORS = (OSError, ValueError, subprocess.SubprocessError)


def read_frame_at(path: str, at: float) -> FrameObs:
    try:
        return dataclasses.replace(read_image(path), at=at)
    except _FRAME_ERRORS as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return FrameObs("unknown", at=at)


def read_tap_at(paths: list[str], at: float) -> FrameObs:
    try:
        return dataclasses.replace(read_tap(paths), at=at)
    except _FRAME_ERRORS as exc:
        print(f"Bỏ khung bấm {Path(paths[-1]).name}: {exc}", flush=True)
        return FrameObs("unknown", at=at)


def scan_paths(
    paths: list[Path],
    fps: float = SCAN_FPS,
    submit: Submit | None = None,
    work_dir: Path | None = None,
    on_progress: Progress | None = None,
) -> tuple[Table, list[tuple[Path, FrameObs]]]:
    """Đọc ảnh và video theo thứ tự. submit là executor.submit để đọc nhiều khung cùng lúc."""
    for path in paths:
        suffix = path.suffix.lower()
        if suffix not in IMAGE_SUFFIXES and suffix not in VIDEO_SUFFIXES:
            raise RuntimeError(f"Không đọc được {path.name}. Dùng ảnh hoặc video.")

    temps: list[tempfile.TemporaryDirectory[str]] = []
    try:
        image_paths = [(index, path) for index, path in enumerate(paths) if path.suffix.lower() in IMAGE_SUFFIXES]
        image_hits: dict[int, FrameObs] = {}
        if image_paths:
            if on_progress is not None:
                on_progress(f"Đang đọc {len(image_paths)} ảnh")
            if submit is None:
                for index, path in image_paths:
                    image_hits[index] = read_image(path)
            else:
                futures = [(index, path, submit(read_image, str(path))) for index, path in image_paths]
                for index, path, future in futures:
                    image_hits[index] = future.result()

        ordered: list[tuple[Path, FrameObs]] = []
        for index, path in enumerate(paths):
            if path.suffix.lower() in IMAGE_SUFFIXES:
                ordered.append((path, image_hits[index]))
                continue
            folder = tempfile.TemporaryDirectory(prefix="danhba-frames-", dir=work_dir)
            temps.append(folder)
            ordered.extend(_scan_video(path, Path(folder.name), fps, submit, on_progress))
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
) -> list[tuple[Path, FrameObs]]:
    if on_progress is not None:
        on_progress(f"Đang xem trước {path.name}")
    plan = plan_video(path, fps, on_progress=on_progress)
    needed = plan.needed()
    if on_progress is not None:
        on_progress(f"Đang tách {len(needed)} khung từ {path.name}")
    files = extract_frames(path, folder, needed, fps, on_progress=on_progress)
    jobs: list[tuple[float, int, Path, Callable[..., FrameObs], tuple]] = []
    read_numbers = set(plan.reads)
    for number in plan.reads:
        jobs.append((number / fps, 0, files[number], read_frame_at, (str(files[number]), number / fps)))
    for window in plan.taps:
        last = window[-1]
        paths = [str(files[number]) for number in window]
        jobs.append((last / fps, 1, files[last], read_tap_at, (paths, last / fps)))
        # Thoát hồ sơ nhanh: cửa sổ "trước chuyển cảnh" chứa khung hồ sơ — OCR thường, không chỉ tìm chấm bấm.
        for number in window[-BRIEF_EXIT_READS:]:
            if number in read_numbers:
                continue
            read_numbers.add(number)
            jobs.append((number / fps, 0, files[number], read_frame_at, (str(files[number]), number / fps)))
    jobs.sort(key=lambda job: (job[0], job[1]))
    total = len(jobs)
    # Cập nhật ít nhất mỗi 10 giây tường — video 10k+ khung không được im 10 phút rồi bị watchdog restart.
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
        # Đọc theo lô — tránh nộp cả 10k+ future một lúc; dễ treo khi pool recycle worker.
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
    return [(job[2], obs) for job, obs in zip(jobs, results)]


def plan_video(path: Path, fps: float = SCAN_FPS, on_progress: Progress | None = None) -> VideoPlan:
    """Lượt một: ảnh xám nhỏ đi qua ống, chỉ giữ vài khung gần nhất trong bộ nhớ."""
    _require_ffmpeg(path)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "0",
        "-an",
        "-i",
        str(path),
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
) -> dict[int, Path]:
    """Lượt hai: chỉ ghi ra đĩa các khung đã chọn, đủ độ phân giải."""
    _require_ffmpeg(path)
    if not numbers:
        return {}
    script = folder / "select.txt"
    picks = "+".join(f"eq(n,{number})" for number in numbers)
    script.write_text(f"fps={fps},select='{picks}'", encoding="utf-8")
    pattern = folder / "f_%06d.jpg"
    stop = threading.Event()
    watcher: threading.Thread | None = None
    if on_progress is not None:
        expected = len(numbers)

        def _watch() -> None:
            while not stop.wait(5.0):
                try:
                    written = sum(1 for _ in folder.glob("f_*.jpg"))
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
                "-an",
                "-i",
                str(path),
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
    written = sorted(folder.glob("f_*.jpg"))
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
