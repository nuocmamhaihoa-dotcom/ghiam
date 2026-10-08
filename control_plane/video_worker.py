"""Đọc video trong hàng đợi. Đóng trình duyệt không dừng việc này.

Một nhóm tiến trình đọc khung dùng chung cho mọi video, mỗi tiến trình một
nhân, chừa một nhân cho trang web. Vài luồng nhận video và tách khung; khung
của video nào vào trước thì được đọc trước. Chỉ có một video thì cả nhóm
cùng đọc video đó, nên một video dài không còn nằm trên một nhân.
"""

from __future__ import annotations

import functools
import multiprocessing
import os
import shutil
import threading
import time
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from pathlib import Path

from control_plane.settings import settings
from control_plane.video_scan import scan_paths
from control_plane.video_store import (
    claim,
    fail,
    feeder_count,
    finish,
    init_db,
    merge_close_results,
    merge_same_name_results,
    requeue,
    requeue_running,
    touch_heartbeat,
    worker_count,
)


def heartbeat_path() -> Path:
    return settings.data_dir / "video-worker.heartbeat"


def run_job(db_path: Path, job: dict[str, object], scan=scan_paths) -> None:
    video_id = int(job["id"])
    path = Path(str(job["path"]))
    started = time.monotonic()
    try:
        table, frames = scan([path])
        finish(db_path, video_id, table)
    except BrokenExecutor:
        requeue(db_path, video_id)
        raise
    except Exception as exc:
        message = str(exc) or "Không đọc được video."
        fail(db_path, video_id, message)
        print(f"Video {video_id} lỗi sau {time.monotonic() - started:.0f} giây: {message[:300]}", flush=True)
        return
    path.unlink(missing_ok=True)
    print(
        f"Video {video_id} xong trong {time.monotonic() - started:.0f} giây, {len(frames)} khung: "
        f"{len(table.rows)} hàng đủ, {len(table.review)} cần xem, {len(table.unopened)} chưa mở hồ sơ.",
        flush=True,
    )


def _feed(db_path: Path, pool: ProcessPoolExecutor, work_dir: Path, stop: threading.Event, broken: threading.Event) -> None:
    scan = functools.partial(scan_paths, submit=pool.submit, work_dir=work_dir)
    current: dict[str, object] | None = None
    try:
        while not stop.is_set():
            touch_heartbeat(heartbeat_path())
            current = claim(db_path, os.getpid())
            if current is None:
                stop.wait(1.0)
                continue
            try:
                run_job(db_path, current, scan)
            except BrokenExecutor:
                # run_job đã đưa video về hàng đợi.
                current = None
                broken.set()
                return
            current = None
    finally:
        # Luồng nhận video chết giữa chừng mà tiến trình vẫn sống thì recover_dead không cứu được.
        if current is not None:
            requeue(db_path, int(current["id"]))


def main() -> None:
    db_path = settings.video_db_path
    init_db(db_path)
    merge_close_results(db_path)
    merge_same_name_results(db_path)
    requeue_running(db_path, os.getpid())
    work_dir = settings.data_dir / "frames"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    count = worker_count()
    feeders = feeder_count()
    print(f"Đọc video bằng {count} tiến trình, nhận {feeders} video một lúc. Để trống một nhân cho trang web.", flush=True)
    stop = threading.Event()
    broken = threading.Event()
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=count, mp_context=context, max_tasks_per_child=400) as pool:
        threads: list[threading.Thread] = []

        def start_feeder() -> threading.Thread:
            thread = threading.Thread(target=_feed, args=(db_path, pool, work_dir, stop, broken), daemon=True)
            thread.start()
            return thread

        threads = [start_feeder() for _ in range(feeders)]
        while not broken.is_set():
            touch_heartbeat(heartbeat_path())
            for index, thread in enumerate(threads):
                if not thread.is_alive() and not broken.is_set():
                    threads[index] = start_feeder()
            time.sleep(2)
        stop.set()
    # Một tiến trình đọc chết giữa chừng làm hỏng cả nhóm. Thoát để systemd khởi động lại sạch sẽ.
    raise SystemExit(1)


if __name__ == "__main__":
    main()
