"""Tách khung hình từ video, bỏ khung trùng, đọc song song nhiều khung."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from doc_video.frame import FrameRead, read_frame

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
# Hai hồ sơ khác nhau gần như cùng một màn hình trắng, nên chỉ bỏ khung gần như y hệt.
SAME_FRAME = 0.6


def _read_file(path: str, at: float) -> FrameRead:
    try:
        with Image.open(path) as image:
            return read_frame(image.convert("RGB"), at)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Bỏ khung {Path(path).name}: {exc}", flush=True)
        return FrameRead(at=at)


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
        if submit is not None:
            futures = [submit(_read_file, path, at) for path, at in jobs]
            try:
                return [future.result() for future in futures]
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        workers = workers or max(1, (os.cpu_count() or 2) - 1)
        if workers == 1 or len(jobs) <= 1:
            return [_read_file(path, at) for path, at in jobs]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(_read_file, [path for path, _ in jobs], [at for _, at in jobs], chunksize=1))
    finally:
        for folder in temps:
            folder.cleanup()
