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

from doc_video.frame import FrameRead
from doc_video.join import add_full_name_rows, add_side_rows, build
from doc_video.video import read_inputs

from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.settings import settings
from control_plane.video_store import (
    backfill_video_counts,
    claim,
    confirm_open_profiles,
    drop_stale_uploads,
    fail,
    feeder_count,
    finish,
    init_db,
    merge_close_results,
    prune_old_videos,
    requeue,
    requeue_running,
    touch_heartbeat,
    worker_count,
)


# Một khung đọc mất vài giây. Chờ một khung quá lâu là nhóm tiến trình đọc đã hỏng: đưa video về
# hàng chờ và khởi động lại bộ đọc, thay vì treo mãi.
FRAME_TIMEOUT_SECONDS = 900


def scan_paths(paths: list[Path], submit=None, work_dir: Path | None = None) -> tuple[Table, list[FrameRead]]:
    """Đọc bằng doc_video rồi đổi sang dạng bảng mà kho kết quả đang lưu."""
    frames = read_inputs(paths, submit=submit, work_dir=work_dir, task_timeout=FRAME_TIMEOUT_SECONDS)
    result = add_full_name_rows(add_side_rows(build(frames), frames), frames)
    table = Table(
        rows=[Row(row.phone, row.name, row.handle) for row in result.rows],
        review=[Review(item.phone, item.list_name, item.profile_name, item.handle, item.reason) for item in result.review],
        unopened=[Unopened(phone, name) for phone, name in result.unopened],
    )
    return table, frames


def _drop_old_parts(folder: Path) -> None:
    """File tạm của lần tải bị đứt hơn 30 phút. Lần tải đang chạy ghi tiếp vào file của nó, không đụng tới."""
    if not folder.is_dir():
        return
    cutoff = time.time() - 1800
    for path in folder.glob("up-*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def keep_newest_videos(db_path: Path) -> None:
    """Giữ video mới. Khi ổ đĩa vượt mức, xóa video cũ đã đọc xong. Không đụng video đang đọc."""
    try:
        prune_old_videos(
            db_path,
            settings.video_dir,
            percent=settings.video_disk_percent,
            protect_newest=True,
        )
    except OSError as exc:
        print(f"Chưa dọn được video cũ: {exc}", flush=True)


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
        keep_newest_videos(db_path)
        return
    keep_newest_videos(db_path)
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
    requeue_running(db_path, os.getpid())
    work_dir = settings.data_dir / "frames"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    count = worker_count()
    feeders = feeder_count()
    if feeders == 1:
        print(f"Đọc từng video một, bằng {count} tiến trình. Video còn lại nằm trong hàng chờ.", flush=True)
    else:
        print(f"Đọc {feeders} video cùng lúc, bằng {count} tiến trình.", flush=True)
    stop = threading.Event()
    broken = threading.Event()
    last_sweep = 0.0
    context = multiprocessing.get_context("spawn")
    # Không dùng max_tasks_per_child: khi mọi tiến trình đọc cùng hết lượt, nhóm không mở tiến trình mới
    # và lần đọc treo mãi (đã gặp trên VPS với Python 3.12).
    with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool:
        threads: list[threading.Thread] = []

        def start_feeder() -> threading.Thread:
            thread = threading.Thread(target=_feed, args=(db_path, pool, work_dir, stop, broken), daemon=True)
            thread.start()
            return thread

        threads = [start_feeder() for _ in range(feeders)]
        while not broken.is_set():
            touch_heartbeat(heartbeat_path())
            now = time.monotonic()
            if now - last_sweep >= 60:
                last_sweep = now
                drop_stale_uploads(db_path)
                _drop_old_parts(settings.video_dir)
                keep_newest_videos(db_path)
                backfill_video_counts(db_path)
                confirm_open_profiles(db_path)
            for index, thread in enumerate(threads):
                if not thread.is_alive() and not broken.is_set():
                    threads[index] = start_feeder()
            time.sleep(2)
        stop.set()
    # Một tiến trình đọc chết giữa chừng làm hỏng cả nhóm. Thoát để systemd khởi động lại sạch sẽ.
    raise SystemExit(1)


if __name__ == "__main__":
    main()
