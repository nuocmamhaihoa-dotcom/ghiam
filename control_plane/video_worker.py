"""Đọc video trong hàng đợi. Đóng trình duyệt không dừng việc này.

Mỗi tiến trình đọc một video và chỉ dùng một luồng OCR, để đủ tiến trình
lấp các nhân trừ một nhân cho trang web và cơ sở dữ liệu.
"""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

from control_plane.settings import settings
from control_plane.video_scan import scan_paths
from control_plane.video_store import (
    claim,
    fail,
    finish,
    init_db,
    merge_close_results,
    recover_dead,
    touch_heartbeat,
    worker_count,
)


def heartbeat_path() -> Path:
    return settings.data_dir / "video-worker.heartbeat"


def run_job(db_path: Path, job: dict[str, object], scan=scan_paths) -> None:
    video_id = int(job["id"])
    path = Path(str(job["path"]))
    try:
        table, _frames = scan([path])
        finish(db_path, video_id, table)
    except Exception as exc:
        fail(db_path, video_id, str(exc) or "Không đọc được video.")
        return
    path.unlink(missing_ok=True)


def worker_loop(db_path: str) -> None:
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OMP_THREAD_LIMIT"] = "1"
    path = Path(db_path)
    init_db(path)
    while True:
        touch_heartbeat(heartbeat_path())
        recover_dead(path)
        job = claim(path, os.getpid())
        if job is None:
            time.sleep(0.8)
            continue
        run_job(path, job)


def main() -> None:
    init_db(settings.video_db_path)
    merge_close_results(settings.video_db_path)
    recover_dead(settings.video_db_path)
    count = worker_count()
    print(f"Đọc video bằng {count} tiến trình. Để trống một nhân cho trang web.", flush=True)
    if count == 1:
        worker_loop(str(settings.video_db_path))
        return
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=worker_loop, args=(str(settings.video_db_path),), daemon=False) for _ in range(count)]
    for process in processes:
        process.start()
    while True:
        for index, process in enumerate(processes):
            if process.is_alive():
                continue
            process.join()
            replacement = context.Process(target=worker_loop, args=(str(settings.video_db_path),), daemon=False)
            replacement.start()
            processes[index] = replacement
        time.sleep(2)


if __name__ == "__main__":
    main()
