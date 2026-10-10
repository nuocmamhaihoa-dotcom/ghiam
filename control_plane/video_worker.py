"""Đọc video trong hàng đợi. Đóng trình duyệt không dừng việc này.

Nhiều máy (iPhone) đẩy video lên cùng lúc vẫn chỉ xếp hàng; mỗi video được đọc
bằng một nhóm tiến trình riêng, xong mới sang video kế. Đọc chạy trên máy chủ
(systemd): thoát app / đổi phần mềm không làm dừng. Chỉ restart khi nhóm tiến
trình chết thật — không giết job đang tách khung hoặc OCR.
"""

from __future__ import annotations

import functools
import multiprocessing
import os
import shutil
import sqlite3
import threading
import time
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from pathlib import Path

from control_plane.ocr_backend import describe_ocr, gpu_available, resolve_ocr_engine
from control_plane.settings import settings
from control_plane.video_scan import scan_paths
from control_plane.video_store import (
    claim,
    fail,
    feeder_count,
    finish,
    init_db,
    keep_video_count,
    prune_old_videos,
    rematch_results,
    requeue,
    requeue_running,
    set_duration,
    set_progress,
    should_pause_ocr,
    stale_running,
    touch_heartbeat,
    worker_count,
)
from control_plane.video_validate import probe_duration_sec


def heartbeat_path() -> Path:
    return settings.data_dir / "video-worker.heartbeat"


def frame_work_dir() -> Path:
    """Thư mục tách khung. Ưu tiên /dev/shm (tmpfs) khi còn đủ chỗ — cùng JPEG, I/O nhanh hơn."""
    override = os.environ.get("CONTROL_VIDEO_FRAME_DIR", "").strip()
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    shm = Path("/dev/shm")
    try:
        if shm.is_dir() and shutil.disk_usage(shm).free >= 2 * 1024 * 1024 * 1024:
            path = shm / "fb-poller-frames"
            path.mkdir(parents=True, exist_ok=True)
            return path
    except OSError:
        pass
    path = settings.data_dir / "frames"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _restart_now(reason: str) -> None:
    """Thoát cứng để systemd Restart=always. SystemExit bị kẹt khi pool/thread còn sống."""
    print(reason, flush=True)
    os._exit(1)


def _rematch_process(path: str) -> None:
    """Tiến trình riêng: pair_close_names nặng không giữ GIL của feeder/OCR."""
    try:
        # Chỉ ghép trong từng video — không O(n×m) toàn DB (tránh chiếm 1 CPU hàng giờ).
        joined = rematch_results(Path(path), cross_video=False)
        if joined:
            print(f"Đã ghép thêm {joined} hàng số + username từ kết quả cũ.", flush=True)
    except Exception as exc:
        print(f"Rematch nền lỗi (bỏ qua, sẽ ghép sau mỗi video): {exc}", flush=True)


def run_job(
    db_path: Path,
    job: dict[str, object],
    workers: int,
    work_dir: Path,
    pool: ProcessPoolExecutor,
) -> None:
    video_id = int(job["id"])
    path = Path(str(job["path"]))
    started = time.monotonic()
    last_message = "Đang đọc"
    stop_pulse = threading.Event()

    def on_progress(message: str) -> None:
        nonlocal last_message
        text = " ".join((message or "").split())[:200] or last_message
        last_message = text
        try:
            set_progress(db_path, video_id, text)
            touch_heartbeat(heartbeat_path())
        except Exception as exc:
            print(f"progress video {video_id}: {exc}", flush=True)

    def _pulse() -> None:
        """Chỉ giữ heartbeat; không ghi đè progress_at — để phát hiện OCR chết khi số khung đứng yên."""
        while not stop_pulse.wait(10.0):
            try:
                touch_heartbeat(heartbeat_path())
            except Exception:
                pass

    pulse = threading.Thread(target=_pulse, daemon=True)
    pulse.start()
    try:
        try:
            seconds = probe_duration_sec(path)
            if seconds > 0:
                set_duration(db_path, video_id, seconds)
        except Exception as dur_exc:
            print(f"duration video {video_id}: {dur_exc}", flush=True)
        device = str(job.get("device") or "")
        scan = functools.partial(
            scan_paths,
            submit=pool.submit,
            work_dir=work_dir,
            on_progress=on_progress,
            device=device,
            data_dir=settings.data_dir,
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
        try:
            pruned = prune_old_videos(db_path)
            if pruned:
                print(f"Đã xóa {pruned} file video cũ hơn {keep_video_count()} video gần nhất.", flush=True)
        except Exception as prune_exc:
            print(f"Dọn video cũ lỗi: {prune_exc}", flush=True)
        return
    finally:
        stop_pulse.set()
        pulse.join(timeout=1.0)
    # Gói S: giữ file để Đọc lại; chỉ xóa khi vượt cửa sổ KEEP.
    try:
        pruned = prune_old_videos(db_path)
        if pruned:
            print(f"Đã xóa {pruned} file video cũ hơn {keep_video_count()} video gần nhất.", flush=True)
    except Exception as prune_exc:
        print(f"Dọn video cũ lỗi: {prune_exc}", flush=True)
    kept = path.exists()
    print(
        f"Video {video_id} xong trong {time.monotonic() - started:.0f} giây, {len(frames)} khung: "
        f"{len(table.rows)} hàng đủ, {len(table.review)} cần xem, {len(table.unopened)} chưa mở hồ sơ"
        f"{'; giữ file để Đọc lại' if kept else ''}.",
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
    pool: ProcessPoolExecutor | None = None
    try:
        while not stop.is_set():
            touch_heartbeat(heartbeat_path())
            # Nhiều iPhone đang up / đĩa căng → nhường băng thông + chỗ trống, chưa nhận OCR mới.
            if should_pause_ocr(db_path, settings.video_dir):
                stop.wait(5.0)
                continue
            try:
                current = claim(db_path, os.getpid())
            except sqlite3.OperationalError as exc:
                # DB bận tạm thời — chờ rồi thử lại, không để feeder chết.
                print(f"claim tạm lỗi (thử lại): {exc}", flush=True)
                stop.wait(1.0)
                continue
            if current is None:
                stop.wait(1.0)
                continue
            try:
                # Giữ pool qua nhiều video — tránh spawn lại 15 tiến trình mỗi lần (cùng logic OCR).
                if pool is None:
                    pool = ProcessPoolExecutor(max_workers=workers, mp_context=context)
                run_job(db_path, current, workers, work_dir, pool)
            except BrokenExecutor:
                # run_job đã đưa video về hàng đợi.
                current = None
                if pool is not None:
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    pool = None
                broken.set()
                return
            current = None
    finally:
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        # Luồng nhận video chết giữa chừng mà tiến trình vẫn sống thì recover_dead không cứu được.
        if current is not None:
            requeue(db_path, int(current["id"]))


def main() -> None:
    db_path = settings.video_db_path
    for attempt in range(12):
        try:
            init_db(db_path)
            break
        except Exception as exc:
            if "locked" not in str(exc).lower() and attempt < 11:
                time.sleep(min(8.0, 0.5 * (attempt + 1)))
                continue
            raise
    try:
        pruned = prune_old_videos(db_path)
        if pruned:
            print(f"Giữ tối đa {keep_video_count()} video trên đĩa — đã dọn {pruned} file/path cũ.", flush=True)
    except Exception as exc:
        print(f"Dọn video cũ lúc khởi động lỗi (bỏ qua): {exc}", flush=True)
    try:
        returned = requeue_running(db_path, os.getpid())
        if returned:
            print(f"Đưa {returned} video đọc dở về hàng đợi.", flush=True)
    except Exception as exc:
        print(f"requeue_running lỗi (bỏ qua): {exc}", flush=True)
    work_dir = frame_work_dir()
    # Dọn khung cũ (kể cả lần chạy trước trên /dev/shm hoặc data/frames).
    stale_dirs = {
        work_dir.resolve(),
        (settings.data_dir / "frames").resolve(),
        Path("/dev/shm/fb-poller-frames").resolve(),
    }
    for stale in stale_dirs:
        shutil.rmtree(stale, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    total_workers = worker_count()
    feeders = feeder_count()
    per_workers = max(1, total_workers // feeders)
    engine = resolve_ocr_engine()
    if engine == "paddle" and not gpu_available():
        engine = "tesserocr"
    print(
        f"Đọc video bằng {per_workers} tiến trình/video, xếp hàng {feeders} video một lúc "
        f"(tối đa {per_workers * feeders} nhân, khung tại {work_dir}). "
        f"{describe_ocr()}. Chỉ đọc luồng hình (bỏ audio). "
        f"Chạy liên tục trên máy chủ — thoát app không dừng.",
        flush=True,
    )
    if not gpu_available():
        print(
            "VPS không có GPU NVIDIA — bỏ qua Paddle/GPU OCR để giữ độ chính xác Tesseract; "
            "đang dùng hết CPU với tesserocr + nhiều feeder.",
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

    # Feeders trước — rematch nền (tiến trình riêng) sau để không chặn OCR/upload.
    threads = [start_feeder() for _ in range(feeders)]
    rematch_proc = context.Process(
        target=_rematch_process,
        args=(str(db_path),),
        name="video-rematch-deferred",
        daemon=True,
    )
    rematch_proc.start()

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
                    f"Video {video_id} chết im (không còn tiến trình con) — xếp lại hàng và khởi động lại.",
                    flush=True,
                )
            _restart_now("Bộ đọc thoát cứng sau khi phát hiện video nghẽn thật.")
        time.sleep(2)
    stop.set()
    _restart_now("Bộ đọc thoát cứng vì nhóm tiến trình hỏng — systemd sẽ chạy lại.")


if __name__ == "__main__":
    main()
