"""Giám sát bộ đọc video từ hub: tắt máy / kẹt tiến trình thì tự bật lại.

Đọc chạy trên máy chủ (systemd), không phụ thuộc trình duyệt hay app đang mở.
Vòng này chỉ đảm bảo service luôn sống khi hàng đợi còn việc.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

from control_plane.settings import settings
from control_plane.video_store import init_db, reader_alive, stats

_watch_task: asyncio.Task[None] | None = None


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=60,
    )


def reader_service_name() -> str:
    return os.environ.get("CONTROL_VIDEO_SERVICE", "fb-poller-video").strip() or "fb-poller-video"


def needs_restart(heartbeat: Path, *, max_age: float = 90.0) -> bool:
    """Hàng đợi còn việc mà heartbeat bộ đọc đã chết → cần restart service."""
    init_db(settings.video_db_path)
    counts = stats(settings.video_db_path)
    pending = int(counts.get("queued", 0)) + int(counts.get("running", 0))
    if pending <= 0:
        return False
    return not reader_alive(heartbeat, max_age=max_age)


def restart_reader() -> bool:
    if shutil.which("systemctl") is None:
        return False
    service = reader_service_name()
    done = _systemctl("restart", service)
    if done.returncode == 0:
        print(f"Watchdog: đã khởi động lại {service}.", flush=True)
        return True
    detail = (done.stderr or done.stdout or "").strip()[:300]
    print(f"Watchdog: không restart được {service}: {detail}", flush=True)
    return False


async def _loop() -> None:
    await asyncio.sleep(8)
    heartbeat = settings.data_dir / "video-worker.heartbeat"
    # Tránh restart liên tục khi service đang lên chậm.
    last_restart = 0.0
    while True:
        try:
            if needs_restart(heartbeat, max_age=90.0) and time.monotonic() - last_restart >= 60:
                if restart_reader():
                    last_restart = time.monotonic()
        except Exception as exc:
            print(f"Watchdog video lỗi: {exc}", flush=True)
        await asyncio.sleep(30)


def start_background_watchdog() -> None:
    """Bật vòng giám sát nếu có systemctl (môi trường VPS)."""
    global _watch_task
    if os.environ.get("CONTROL_VIDEO_WATCHDOG", "1").strip() in {"0", "false", "no"}:
        return
    if shutil.which("systemctl") is None:
        return
    if _watch_task is not None and not _watch_task.done():
        return
    _watch_task = asyncio.create_task(_loop())
