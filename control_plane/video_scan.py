"""Đọc video hoặc ảnh chụp màn hình danh bạ, xuất bảng số điện thoại, tên, username.

Chỉ giữ khung hình khi hình đã đứng yên. Danh bạ và hồ sơ đọc theo vùng chữ.
Ghép bằng tên; nếu video vừa chọn một dòng rồi mở hồ sơ thì ghép theo lần bấm đó.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from control_plane.screen_read import read_image
from control_plane.screen_table import FrameObs, Table, build_table

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def scan_paths(paths: list[Path], fps: float = 3.0) -> tuple[Table, list[tuple[Path, FrameObs]]]:
    frames: list[tuple[Path, FrameObs]] = []
    temps: list[tempfile.TemporaryDirectory[str]] = []
    try:
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                frames.append((path, read_image(path)))
                continue
            if suffix in VIDEO_SUFFIXES:
                folder = tempfile.TemporaryDirectory(prefix="danhba-frames-")
                temps.append(folder)
                for frame_path in stable_video_frames(path, Path(folder.name), fps=fps):
                    frames.append((frame_path, read_image(frame_path)))
                continue
            raise RuntimeError(f"Không đọc được {path.name}. Dùng ảnh hoặc video.")
        table = build_table([obs for _, obs in frames])
        return table, frames
    finally:
        for folder in temps:
            folder.cleanup()


def stable_video_frames(path: Path, folder: Path, fps: float = 3.0) -> list[Path]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Cần cài ffmpeg để đọc video.")
    if not path.exists():
        raise RuntimeError(f"Không thấy file {path}.")
    pattern = folder / "f_%05d.jpg"
    done = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps}",
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
    extracted = sorted(folder.glob("f_*.jpg"))
    if not extracted:
        raise RuntimeError(f"Video {path.name} không có khung hình.")
    thumbs = [_thumb(item) for item in extracted]
    chosen = voting_frames(thumbs)
    if not chosen:
        chosen = [0]
    return [extracted[index] for index in chosen]


def voting_frames(frames: list[np.ndarray]) -> list[int]:
    """Mỗi cảnh đứng yên lấy thêm một khung liền sau để hai lần đọc phải trùng số."""
    chosen = keep_stable(frames)
    picked = set(chosen)
    for index in chosen:
        nxt = index + 1
        if nxt >= len(frames) or nxt in picked:
            continue
        if _mean_abs(frames[index], frames[nxt]) >= 3.5:
            continue
        picked.add(nxt)
    return sorted(picked)


def keep_stable(frames: list[np.ndarray], settled: float = 3.5, changed: float = 1.2) -> list[int]:
    """Giữ khung đứng yên và khác khung đã giữ trước đó. Bỏ khung đang cuộn.

    Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên ngưỡng khác biệt
    phải thấp hơn mức đổi cả màn hình.
    """
    if not frames:
        return []
    if len(frames) == 1:
        return [0]
    diffs = [_mean_abs(frames[index], frames[index + 1]) for index in range(len(frames) - 1)]
    kept: list[int] = []
    last: np.ndarray | None = None
    for index, diff in enumerate(diffs):
        if diff >= settled:
            continue
        if last is not None and _mean_abs(frames[index], last) < changed:
            continue
        kept.append(index)
        last = frames[index]
    return kept


def _thumb(path: Path) -> np.ndarray:
    image = Image.open(path).convert("L")
    image.thumbnail((96, 96))
    return np.asarray(image, dtype=np.float32)


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
