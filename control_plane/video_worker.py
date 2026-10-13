"""Đọc video trong hàng đợi. Đóng trình duyệt không dừng việc này.

Nhiều máy đẩy video lên thì xếp hàng, không bỏ video nào. Một nhóm tiến trình
dùng chung: chỉ một video thì dùng hết nhân, nhiều video thì chia nhân, video
vào trước được nhận trước. Đang tải chỉ nhường vài nhân, không dừng hàng đọc.
Đọc chạy trên máy chủ (systemd). Chỉ restart khi nhóm tiến trình chết thật.
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
from control_plane.upload_priority import (
    apply_upload_priority,
    release_stopped,
    reserved_cores,
)
from control_plane.video_scan import scan_paths, use_ffmpeg_threads
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
    rescue_stuck_uploads,
    set_duration,
    set_progress,
    should_pause_ocr,
    stale_running,
    touch_heartbeat,
    upload_io_mode,
    worker_count,
)
from control_plane.video_validate import probe_duration_sec


_JOBS = 0
_JOBS_LOCK = threading.Lock()


def heartbeat_path() -> Path:
    override = os.environ.get("CONTROL_VIDEO_HEARTBEAT", "").strip()
    if override:
        return Path(override)
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
    try:
        release_stopped()
    except Exception:
        pass
    os._exit(1)


def _yield_ocr_while_uploading(db_path: Path, stop: threading.Event) -> None:
    """Có byte thì nhường vài nhân cho đường tải. Không dừng ffmpeg — đọc vẫn chạy.

    Sang app khác trên điện thoại không ảnh hưởng hàng đọc: việc này chạy trên máy chủ.
    """
    held = "off"
    try:
        while not stop.wait(0.5):
            try:
                mode = upload_io_mode(db_path)
            except Exception:
                mode = "off"
            # arm và flow cùng mức: đọc tiếp, chỉ tránh để upload xếp sau đĩa.
            priority = "off" if mode == "off" else "flow"
            try:
                apply_upload_priority(priority)
            except Exception:
                pass
            if mode != held:
                if mode == "off":
                    print("Upload nguội — đọc dùng lại hết nhân.", flush=True)
                else:
                    print(
                        f"Đang tải — dành {reserved_cores()} nhân cho upload, phần còn lại vẫn đọc.",
                        flush=True,
                    )
                held = mode
    finally:
        release_stopped()
        try:
            apply_upload_priority("off")
        except Exception:
            pass


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
    global _JOBS
    try:
        with _JOBS_LOCK:
            _JOBS += 1
        try:
            seconds = probe_duration_sec(path)
            if seconds > 0:
                set_duration(db_path, video_id, seconds)
        except Exception as dur_exc:
            print(f"duration video {video_id}: {dur_exc}", flush=True)
        device = str(job.get("device") or "")
        cpus = os.cpu_count() or 2
        with _JOBS_LOCK:
            running = max(1, _JOBS)
        use_ffmpeg_threads(max(2, cpus // running))
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
        with _JOBS_LOCK:
            _JOBS = max(0, _JOBS - 1)
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
        f"{len(table.rows)} hàng đủ 3 cột, {len(table.unopened)} số thiếu @, {len(table.review)} dòng đối chiếu"
        f"{'; giữ file để Đọc lại' if kept else ''}.",
        flush=True,
    )


def _feed(
    db_path: Path,
    work_dir: Path,
    pool: ProcessPoolExecutor,
    stop: threading.Event,
    broken: threading.Event,
) -> None:
    current: dict[str, object] | None = None
    try:
        while not stop.is_set():
            touch_heartbeat(heartbeat_path())
            # Chỉ nhường job mới khi đĩa sắp đầy. Đang tải thì vẫn nhận — upload đã có nhân riêng.
            if should_pause_ocr(db_path, settings.video_dir):
                stop.wait(2.0)
                continue
            try:
                current = claim(db_path, os.getpid())
            except sqlite3.OperationalError as exc:
                # DB bận tạm thời — chờ rồi thử lại, không để feeder chết.
                print(f"claim tạm lỗi (thử lại): {exc}", flush=True)
                stop.wait(1.0)
                continue
            if current is None:
                stop.wait(0.4)
                continue
            try:
                run_job(db_path, current, work_dir, pool)
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
    try:
        rescued = rescue_stuck_uploads(db_path, settings.video_dir)
        if rescued:
            print(f"Đưa {rescued} video kẹt lúc tải vào hàng đọc hoặc bỏ dòng không còn file.", flush=True)
    except Exception as exc:
        print(f"rescue upload kẹt lỗi (bỏ qua): {exc}", flush=True)
    total_workers = worker_count()
    feeders = feeder_count()
    engine = resolve_ocr_engine()
    if engine == "paddle" and not gpu_available():
        engine = "tesserocr"
    print(
        f"Đọc bằng {total_workers} tiến trình dùng chung, tối đa {feeders} video cùng lúc "
        f"(video vào hàng trước được nhận trước, khung tại {work_dir}). "
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
    threading.Thread(
        target=_yield_ocr_while_uploading,
        args=(db_path, stop),
        name="yield-ocr-for-upload",
        daemon=True,
    ).start()
    context = multiprocessing.get_context("spawn")
    pool = ProcessPoolExecutor(max_workers=total_workers, mp_context=context)
    threads: list[threading.Thread] = []

    def start_feeder() -> threading.Thread:
        thread = threading.Thread(
            target=_feed,
            args=(db_path, work_dir, pool, stop, broken),
            daemon=True,
        )
        thread.start()
        return thread

    # Feeders trước — rematch nền (tiến trình riêng) sau để không chặn OCR/upload.
    threads = [start_feeder() for _ in range(feeders)]
    rematch_proc: multiprocessing.Process | None = context.Process(
        target=_rematch_process,
        args=(str(db_path),),
        name="video-rematch-deferred",
        daemon=True,
    )
    rematch_proc.start()

    last_upload_clean = 0.0
    while not broken.is_set():
        touch_heartbeat(heartbeat_path())
        now_clean = time.time()
        if now_clean - last_upload_clean >= 10:
            last_upload_clean = now_clean
            try:
                from control_plane.video_upload_sessions import cleanup_stale_uploads

                cleanup_stale_uploads(settings.video_uploads_db_path, settings.video_dir)
            except Exception as exc:
                print(f"Dọn phiên tải kẹt lỗi (bỏ qua): {exc}", flush=True)
        if rematch_proc is not None and not rematch_proc.is_alive():
            rematch_proc.join(timeout=0.1)
            rematch_proc = None
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
