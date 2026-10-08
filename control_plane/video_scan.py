"""Đọc video hoặc ảnh chụp màn hình danh bạ, xuất bảng số điện thoại, tên, username.

Lượt một quét ảnh xám rất nhỏ, 15 khung mỗi giây, để biết lúc nào màn hình
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
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from control_plane.screen_read import read_image, read_tap
from control_plane.screen_table import FrameObs, Table, build_table

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

SCAN_FPS = 15.0
THUMB_W = 48
THUMB_H = 104
SETTLE_LAG = 5
SETTLED = 3.5
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên ngưỡng "đã đổi cảnh" phải rất thấp.
CHANGED = 1.2
MOVING = 2.5
TAP_WINDOW = 5

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


def read_frame_at(path: str, at: float) -> FrameObs:
    return dataclasses.replace(read_image(path), at=at)


def read_tap_at(paths: list[str], at: float) -> FrameObs:
    return dataclasses.replace(read_tap(paths), at=at)


def scan_paths(
    paths: list[Path],
    fps: float = SCAN_FPS,
    submit: Submit | None = None,
    work_dir: Path | None = None,
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
            ordered.extend(_scan_video(path, Path(folder.name), fps, submit))
        return build_table([obs for _, obs in ordered]), ordered
    finally:
        for folder in temps:
            folder.cleanup()


def _scan_video(path: Path, folder: Path, fps: float, submit: Submit | None) -> list[tuple[Path, FrameObs]]:
    plan = plan_video(path, fps)
    files = extract_frames(path, folder, plan.needed(), fps)
    jobs: list[tuple[float, int, Path, Callable[..., FrameObs], tuple]] = []
    for number in plan.reads:
        jobs.append((number / fps, 0, files[number], read_frame_at, (str(files[number]), number / fps)))
    for window in plan.taps:
        last = window[-1]
        paths = [str(files[number]) for number in window]
        jobs.append((last / fps, 1, files[last], read_tap_at, (paths, last / fps)))
    jobs.sort(key=lambda job: (job[0], job[1]))
    if submit is None:
        results = [fn(*args) for _, _, _, fn, args in jobs]
    else:
        futures = [submit(fn, *args) for _, _, _, fn, args in jobs]
        results = [future.result() for future in futures]
    return [(job[2], obs) for job, obs in zip(jobs, results)]


def plan_video(path: Path, fps: float = SCAN_FPS) -> VideoPlan:
    """Lượt một: ảnh xám nhỏ đi qua ống, chỉ giữ vài khung gần nhất trong bộ nhớ."""
    _require_ffmpeg(path)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "0",
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
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(size)
            if len(chunk) < size:
                break
            planner.push(np.frombuffer(chunk, dtype=np.uint8).reshape(THUMB_H, THUMB_W).astype(np.float32))
        error = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
    if code != 0 and planner.count == 0:
        raise RuntimeError(error.strip() or f"ffmpeg không đọc được {path.name}.")
    plan = planner.finish()
    if plan.count == 0:
        raise RuntimeError(f"Video {path.name} không có khung hình.")
    return plan


class _Planner:
    """Chọn khung khi các ảnh nhỏ đi qua lần lượt.

    Đứng yên: khung giống khung SETTLE_LAG bước sau. Giữ khi khác khung đã giữ.
    Bắt đầu chuyển: bước đầu tiên có độ khác lớn. Lấy TAP_WINDOW khung ngay trước đó.
    """

    def __init__(self) -> None:
        self.ring: deque[np.ndarray] = deque(maxlen=SETTLE_LAG + 1)
        self.last_kept: np.ndarray | None = None
        self.was_moving = False
        self.count = 0
        self.plan = VideoPlan()

    def push(self, thumb: np.ndarray) -> None:
        number = self.count
        self.count += 1
        if self.ring:
            moving = _mean_abs(self.ring[-1], thumb) >= MOVING
            if moving and not self.was_moving:
                self._tap_window(number - 1)
            self.was_moving = moving
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

    def _tap_window(self, last: int) -> None:
        if last < 0:
            return
        start = max(0, last - TAP_WINDOW + 1)
        if self.plan.taps and self.plan.taps[-1][-1] >= start:
            start = self.plan.taps[-1][-1] + 1
        if start > last:
            return
        self.plan.taps.append(list(range(start, last + 1)))


def extract_frames(path: Path, folder: Path, numbers: list[int], fps: float = SCAN_FPS) -> dict[int, Path]:
    """Lượt hai: chỉ ghi ra đĩa các khung đã chọn, đủ độ phân giải."""
    _require_ffmpeg(path)
    if not numbers:
        return {}
    script = folder / "select.txt"
    picks = "+".join(f"eq(n,{number})" for number in numbers)
    script.write_text(f"fps={fps},select='{picks}'", encoding="utf-8")
    pattern = folder / "f_%06d.jpg"
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
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"ffmpeg không đọc được {path.name}.")
    written = sorted(folder.glob("f_*.jpg"))
    if len(written) < len(numbers):
        raise RuntimeError(f"Video {path.name} chỉ ra {len(written)}/{len(numbers)} khung cần đọc.")
    return dict(zip(numbers, written))


def _require_ffmpeg(path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Cần cài ffmpeg để đọc video.")
    if not path.exists():
        raise RuntimeError(f"Không thấy file {path}.")


def _mean_abs(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        return 255.0
    return float(np.abs(left - right).mean())


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
