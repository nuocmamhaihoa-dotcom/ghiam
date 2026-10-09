"""Đọc video trong hàng đợi. Đóng trình duyệt không dừng việc này.

Nhiều máy (iPhone) đẩy video lên cùng lúc vẫn chỉ xếp hàng; mỗi video được đọc
bằng một nhóm tiến trình riêng, xong mới sang video kế. Không dùng chung một
nhóm tiến trình cho nhiều video — cách đó từng làm cả hàng đợi nghẽn im khi một
tiến trình OCR chết giữa chừng. Nếu một video đứng im quá lâu, bộ đọc tự xếp
lại hàng và khởi động lại sạch.
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
    rematch_results,
    requeue,
    requeue_running,
    set_progress,
    stale_running,
    touch_heartbeat,
    worker_count,
)


def heartbeat_path() -> Path:
    return settings.data_dir / "video-worker.heartbeat"


def _restart_now(reason: str) -> None:
    """Thoát cứng để systemd Restart=always. SystemExit bị kẹt khi pool/thread còn sống."""
    print(reason, flush=True)
    os._exit(1)


def run_job(
    db_path: Path,
    job: dict[str, object],
    workers: int,
    work_dir: Path,
    context: multiprocessing.context.BaseContext,
) -> None:
    video_id = int(job["id"])
    path = Path(str(job["path"]))
    started = time.monotonic()

    def on_progress(message: str) -> None:
        set_progress(db_path, video_id, message)
        touch_heartbeat(heartbeat_path())

    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=context,
            max_tasks_per_child=400,
        ) as pool:
            scan = functools.partial(
                scan_paths,
                submit=pool.submit,
                work_dir=work_dir,
                on_progress=on_progress,
            )
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


def _feed(
    db_path: Path,
    workers: int,
    work_dir: Path,
    context: multiprocessing.context.BaseContext,
    stop: threading.Event,
    broken: threading.Event,
) -> None:
    current: dict[str, object] | None = None
    try:
        while not stop.is_set():
            touch_heartbeat(heartbeat_path())
            current = claim(db_path, os.getpid())
            if current is None:
                stop.wait(1.0)
                continue
            try:
                run_job(db_path, current, workers, work_dir, context)
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
    joined = rematch_results(db_path)
    if joined:
        print(f"Đã ghép thêm {joined} hàng số + username từ kết quả cũ.", flush=True)
    returned = requeue_running(db_path, os.getpid())
    if returned:
        print(f"Đưa {returned} video đọc dở về hàng đợi.", flush=True)
    work_dir = settings.data_dir / "frames"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    total_workers = worker_count()
    feeders = feeder_count()
    per_workers = max(1, total_workers // feeders)
    print(
        f"Đọc video bằng {per_workers} tiến trình/video, xếp hàng {feeders} video một lúc "
        f"(tối đa {per_workers * feeders} nhân). Nhiều máy đẩy lên vẫn xếp hàng ổn định.",
        flush=True,
    )
    stop = threading.Event()
    broken = threading.Event()
    context = multiprocessing.get_context("spawn")
    threads: list[threading.Thread] = []

    def start_feeder() -> threading.Thread:
        thread = threading.Thread(
            target=_feed,
            args=(db_path, per_workers, work_dir, context, stop, broken),
            daemon=True,
        )
        thread.start()
        return thread

    threads = [start_feeder() for _ in range(feeders)]
    while not broken.is_set():
        touch_heartbeat(heartbeat_path())
        for index, thread in enumerate(threads):
            if not thread.is_alive() and not broken.is_set():
                threads[index] = start_feeder()
        stuck = stale_running(db_path)
        if stuck:
            for video_id in stuck:
                requeue(db_path, video_id)
                print(
                    f"Video {video_id} đứng im quá lâu — xếp lại hàng và khởi động lại bộ đọc.",
                    flush=True,
                )
            _restart_now("Bộ đọc thoát cứng sau khi phát hiện video nghẽn.")
        time.sleep(2)
    stop.set()
    _restart_now("Bộ đọc thoát cứng vì nhóm tiến trình hỏng — systemd sẽ chạy lại.")


if __name__ == "__main__":
    main()
