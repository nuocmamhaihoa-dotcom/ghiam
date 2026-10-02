"""Đồng hồ từng bước đọc video: giây cộng dồn và số lần. PC gửi tóm tắt này lên hub."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_seconds: dict[str, float] = {}
_counts: dict[str, int] = {}


def add(stage: str, seconds: float, count: int = 1) -> None:
    with _lock:
        _seconds[stage] = _seconds.get(stage, 0.0) + max(0.0, float(seconds))
        _counts[stage] = _counts.get(stage, 0) + max(0, int(count))


def bump(name: str, count: int = 1) -> None:
    """Đếm một việc không cần đo giờ."""
    add(name, 0.0, count)


@contextmanager
def timed(stage: str, count: int = 1) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        add(stage, time.perf_counter() - start, count)


def snapshot() -> dict[str, tuple[float, int]]:
    with _lock:
        return {name: (_seconds.get(name, 0.0), _counts.get(name, 0)) for name in set(_seconds) | set(_counts)}


def take() -> dict[str, tuple[float, int]]:
    """Lấy số đã cộng dồn rồi xóa, để kỳ sau đo lại từ đầu."""
    with _lock:
        found = {name: (_seconds.get(name, 0.0), _counts.get(name, 0)) for name in set(_seconds) | set(_counts)}
        _seconds.clear()
        _counts.clear()
    return found


def reset() -> None:
    take()


def _mean_ms(data: dict[str, tuple[float, int]], stage: str) -> int:
    seconds, count = data.get(stage, (0.0, 0))
    return int(round(seconds * 1000 / count)) if count > 0 else 0


def summary(data: dict[str, tuple[float, int]]) -> dict[str, int]:
    """Số nguyên nhỏ gọn để gửi lên hub. Mili giây là trung bình mỗi khung hoặc mỗi dòng."""
    reads = data.get("read", (0.0, 0))[1]
    votes = data.get("vote.lines", (0.0, 0))[1]
    return {
        "frames": reads,
        "readMs": _mean_ms(data, "read"),
        "voteMs": _mean_ms(data, "vote"),
        "thumbMs": _mean_ms(data, "thumb"),
        "ffmpegSec": int(round(data.get("ffmpeg", (0.0, 0))[0])),
        "voteLines": votes,
        "reused": data.get("vote.reused", (0.0, 0))[1],
        "third": data.get("vote.third", (0.0, 0))[1],
        "rereads": data.get("vote.rereads", (0.0, 0))[1],
        "skipped": data.get("vote.skipped", (0.0, 0))[1],
        "rescued": data.get("zone.rescued", (0.0, 0))[1],
        "strips": data.get("scroll.strips", (0.0, 0))[1],
        "fades": data.get("scroll.fades", (0.0, 0))[1],
    }


def describe(data: dict[str, tuple[float, int]]) -> str:
    """Một câu cho cửa sổ PC. Rỗng khi chưa đọc khung nào."""
    found = summary(data)
    if found["frames"] <= 0:
        return ""
    text = (
        f"Kỳ vừa rồi {found['frames']} khung: đọc khung {found['readMs']} ms, "
        f"đối chiếu tên/@ {found['voteMs']} ms, so khung {found['thumbMs']} ms mỗi khung, "
        f"ffmpeg {found['ffmpegSec']} giây."
    )
    if found["voteLines"] > 0:
        text += (
            f" Dòng đối chiếu {found['voteLines']}, dùng lại {found['reused']}, "
            f"hướng 3 {found['third']}, đọc lại 5 lần {found['rereads']}, bỏ ô sai hình {found['skipped']}."
        )
    if found["rescued"] > 0:
        text += f" Cứu {found['rescued']} trang hồ sơ."
    if found["strips"] > 0:
        text += f" Đọc dải cuộn {found['strips']} khung."
    if found["fades"] > 0:
        text += f" Bỏ {found['fades']} khung mờ lúc chuyển trang."
    return text
