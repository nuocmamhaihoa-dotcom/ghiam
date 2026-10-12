"""Nhường đĩa và vài nhân cho đường tải ngay khi phiên vừa mở.

Đọc không dừng: khi đã có byte thì OCR vẫn chạy trên các nhân còn lại,
chỉ hạ ưu tiên đĩa. Khi chưa có byte, tạm dừng ffmpeg từng nhịp ngắn
để mảnh đầu không xếp hàng sau hàng đống đọc đĩa. Hết tải thì trả lại hết máy.
"""

from __future__ import annotations

import ctypes
import os
import signal
import time
from pathlib import Path

# ioprio_set(which=IOPRIO_WHO_PROCESS, who=pid, ioprio)
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_BE = 2
_IOPRIO_CLASS_IDLE = 3
_SYSCALL = {
    "x86_64": 251,
    "amd64": 251,
    "aarch64": 30,
    "armv8l": 30,
    "armv7l": 315,
    "armv6l": 315,
}

_last_mode = ""
_last_apply = 0.0
_stopped: set[int] = set()


def reserved_cores(cpus: int | None = None) -> int:
    """Số nhân để trống cho tiến trình nhận upload. Phần còn lại vẫn đọc."""
    count = cpus if cpus is not None else (os.cpu_count() or 2)
    raw = os.environ.get("CONTROL_VIDEO_UPLOAD_CORES", "").strip()
    if raw:
        return max(1, min(max(1, count - 1), int(raw)))
    if count >= 8:
        return 4
    if count >= 4:
        return 2
    return 1


def is_ffmpeg_cmdline(cmd: bytes) -> bool:
    base = cmd.split(b"\0", 1)[0]
    name = base.rsplit(b"/", 1)[-1]
    return name == b"ffmpeg" or name.startswith(b"ffmpeg")


def _syscall_nr() -> int | None:
    try:
        return _SYSCALL.get(os.uname().machine)
    except AttributeError:
        return None


def set_ioprio(pid: int, klass: int, data: int) -> None:
    nr = _syscall_nr()
    if nr is None:
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        pri = (int(klass) << 13) | (int(data) & 0x1FFF)
        libc.syscall(int(nr), _IOPRIO_WHO_PROCESS, int(pid), int(pri))
    except Exception:
        return


def set_nice(pid: int, level: int) -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, int(pid), int(level))
    except (OSError, AttributeError):
        return


def _proc_rows() -> list[tuple[int, int, bytes]]:
    proc = Path("/proc")
    rows: list[tuple[int, int, bytes]] = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return rows
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            cmd = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        end = stat.rfind(")")
        if end < 0:
            continue
        parts = stat[end + 2 :].split()
        if len(parts) < 2:
            continue
        try:
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, cmd))
    return rows


def reader_descendant_pids() -> list[int]:
    """Mọi tiến trình con của bộ đọc (pool, ffmpeg, tesseract)."""
    rows = _proc_rows()
    children: dict[int, list[int]] = {}
    roots: list[int] = []
    skip: set[int] = set()
    for pid, ppid, cmd in rows:
        if b"resource_tracker" in cmd:
            skip.add(pid)
            continue
        children.setdefault(ppid, []).append(pid)
        if b"video_worker" in cmd:
            roots.append(pid)
    found: list[int] = []
    seen: set[int] = set()
    stack: list[int] = []
    for root in roots:
        stack.extend(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in skip or pid in seen:
            continue
        seen.add(pid)
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def release_stopped() -> None:
    for pid in list(_stopped):
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass
        _stopped.discard(pid)


def pause_ffmpeg(pids: list[int] | None = None) -> int:
    """Dừng ffmpeg đang tách khung. OCR trên khung đã có vẫn chạy."""
    stopped = 0
    for pid in pids if pids is not None else reader_descendant_pids():
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if not is_ffmpeg_cmdline(cmd):
            continue
        if pid in _stopped:
            continue
        try:
            os.kill(pid, signal.SIGSTOP)
        except OSError:
            continue
        _stopped.add(pid)
        stopped += 1
    return stopped


def apply_upload_priority(mode: str) -> None:
    """mode: off | arm | flow.

    arm — phiên vừa mở, chưa có byte. Đĩa nhường cho mảnh đầu.
    flow — đang có byte. Đọc tiếp trên nhân còn lại, ưu tiên đĩa vừa phải.
    off — hết tải. Đọc dùng lại hết máy.
    """
    global _last_mode, _last_apply
    now = time.monotonic()
    if mode == _last_mode and (now - _last_apply) < 0.8:
        return
    _last_mode = mode
    _last_apply = now
    if mode == "off":
        release_stopped()
        klass, data, nice = _IOPRIO_CLASS_BE, 0, 0
        uploading = False
    elif mode == "arm":
        klass, data, nice = _IOPRIO_CLASS_IDLE, 0, 12
        uploading = True
    else:
        release_stopped()
        klass, data, nice = _IOPRIO_CLASS_BE, 7, 5
        uploading = True
    cpus = os.cpu_count() or 1
    keep = reserved_cores(cpus)
    if uploading and keep < cpus and hasattr(os, "sched_setaffinity"):
        allowed = set(range(keep, cpus))
    else:
        allowed = set(range(cpus))
    if not allowed:
        allowed = {0}
    for pid in reader_descendant_pids():
        set_ioprio(pid, klass, data)
        set_nice(pid, nice)
        if hasattr(os, "sched_setaffinity"):
            try:
                os.sched_setaffinity(pid, allowed)
            except OSError:
                pass
