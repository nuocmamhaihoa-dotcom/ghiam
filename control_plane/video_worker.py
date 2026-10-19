"""Đọc video trong hàng đợi. Đóng trình duyệt không dừng việc này.

Một nhóm tiến trình đọc khung dùng chung cho mọi video, mỗi tiến trình một
nhân, chừa một nhân cho trang web. Vài luồng nhận video và tách khung; khung
của video nào vào trước thì được đọc trước. Chỉ có một video thì cả nhóm
cùng đọc video đó, nên một video dài không còn nằm trên một nhân.
"""

from __future__ import annotations

import functools
import hashlib
import multiprocessing
import os
import shutil
import threading
import time
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from pathlib import Path

from doc_video.frame import FrameRead
from doc_video.pipeline import read_video

from control_plane.results_out import webhook_loop
from control_plane.screen_table import Review, Row, Table, Unopened
from control_plane.settings import settings
from control_plane.upload_parts import reclaim_stale_partials
from control_plane.video_shrink import DEFAULT_CRF, shrink
from control_plane.video_store import (
    adopt_shrunk,
    backfill_video_counts,
    claim,
    confirm_open_profiles,
    disk_limit_bytes,
    drop_orphan_small,
    drop_stale_uploads,
    fail,
    feeder_count,
    finish,
    get_meta,
    init_db,
    merge_close_results,
    next_to_shrink,
    prune_old_videos,
    release_stale_pc,
    report_progress,
    requeue,
    requeue_running,
    shrink_failed,
    touch_heartbeat,
    worker_count,
)


def scan_paths(
    paths: list[Path], submit=None, work_dir: Path | None = None, progress=None
) -> tuple[Table, list[FrameRead]]:
    """Đọc bằng doc_video rồi đổi sang dạng bảng mà kho kết quả đang lưu."""
    if progress is None:
        result, frames = read_video(paths, submit=submit, work_dir=work_dir)
    else:
        result, frames = read_video(paths, submit=submit, work_dir=work_dir, progress=progress)
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
    """Giữ video mới. Khi ổ đĩa vượt mức, xóa bản tải dở đã cũ và video đã đọc xong."""
    try:
        usage = shutil.disk_usage(settings.video_dir)
        limit = disk_limit_bytes(int(usage.total), settings.video_disk_percent)
        if int(usage.used) > limit or int(usage.free) <= 2 * 1024 * 1024 * 1024:
            reclaim_stale_partials(settings.video_dir)
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


# Công tắc trong video.db, mặc định tắt. Chỉ bật khi cổng chống lùi đạt với đúng mức nén này.
SHRINK_KEY = "video_shrink"
SHRINK_CRF_KEY = "video_shrink_crf"


def shrink_setting(db_path: Path) -> tuple[bool, int]:
    enabled = get_meta(db_path, SHRINK_KEY, "off") == "on"
    try:
        crf = int(get_meta(db_path, SHRINK_CRF_KEY, str(DEFAULT_CRF)))
    except ValueError:
        crf = DEFAULT_CRF
    return enabled, max(0, min(30, crf))


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def shrink_next(db_path: Path, crf: int, shrinker=shrink) -> bool:
    """Thu nhỏ một video đang chờ. Trả về False khi không còn video nào cần thu nhỏ."""
    job = next_to_shrink(db_path)
    if job is None:
        return False
    video_id = int(job["id"])
    source = Path(str(job["path"]))
    if not source.is_file():
        shrink_failed(db_path, video_id)
        return True
    temporary = source.with_name(f"up-shrink-{video_id}.mp4")
    started = time.monotonic()
    if not shrinker(source, temporary, crf=crf):
        shrink_failed(db_path, video_id)
        print(f"Video {video_id} chưa thu nhỏ được. Giữ file gốc.", flush=True)
        return True
    before = int(job["bytes"])
    after = temporary.stat().st_size
    if adopt_shrunk(db_path, video_id, str(source), temporary, _file_sha256(temporary)):
        print(
            f"Video {video_id} thu nhỏ từ {before // 1048576} MB còn {after // 1048576} MB "
            f"trong {time.monotonic() - started:.0f} giây.",
            flush=True,
        )
    return True


def _shrink_loop(db_path: Path, stop: threading.Event) -> None:
    while not stop.is_set():
        worked = False
        try:
            enabled, crf = shrink_setting(db_path)
            worked = enabled and shrink_next(db_path, crf)
        except Exception as exc:
            print(f"Chưa thu nhỏ được video: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        stop.wait(1.0 if worked else 20.0)


class ProgressWriter:
    """Ghi tiến độ vào cơ sở dữ liệu tối đa mỗi vài giây, và mỗi khi đổi bước. Ghi hỏng thì bỏ qua."""

    def __init__(self, db_path: Path, video_id: int, every: float = 5.0, lease: str = "") -> None:
        self.db_path = db_path
        self.video_id = video_id
        self.every = every
        self.lease = lease
        self.stage = ""
        self.at = 0.0

    def __call__(self, stage: str, done: int, total: int) -> None:
        now = time.monotonic()
        if stage == self.stage and now - self.at < self.every and done < total:
            return
        self.stage = stage
        self.at = now
        try:
            report_progress(self.db_path, self.video_id, stage, done, total, self.lease or None)
        except Exception:
            pass


def run_job(db_path: Path, job: dict[str, object], scan=scan_paths) -> None:
    video_id = int(job["id"])
    path = Path(str(job["path"]))
    lease = str(job.get("lease") or "") or None
    started = time.monotonic()
    try:
        if scan is scan_paths or isinstance(scan, functools.partial) and scan.func is scan_paths:
            table, frames = scan([path], progress=ProgressWriter(db_path, video_id, lease=lease or ""))
        else:
            table, frames = scan([path])
        if not finish(db_path, video_id, table, lease=lease):
            print(f"Video {video_id} không còn thuộc lượt đọc này. Không ghi đè kết quả.", flush=True)
            return
    except BrokenExecutor:
        requeue(db_path, video_id, lease)
        raise
    except Exception as exc:
        message = str(exc) or "Không đọc được video."
        if fail(db_path, video_id, message, lease):
            print(f"Video {video_id} lỗi sau {time.monotonic() - started:.0f} giây: {message[:300]}", flush=True)
        else:
            print(
                f"Video {video_id} lỗi ở lượt đọc cũ sau {time.monotonic() - started:.0f} giây, "
                f"video đã sang lượt khác nên không đánh lỗi: {message[:300]}",
                flush=True,
            )
        keep_newest_videos(db_path)
        return
    keep_newest_videos(db_path)
    if job.get("check"):
        print(
            f"Video {video_id} kiểm xong trong {time.monotonic() - started:.0f} giây, {len(frames)} khung.",
            flush=True,
        )
        return
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
            requeue(db_path, int(current["id"]), str(current.get("lease") or "") or None)


def main() -> None:
    db_path = settings.video_db_path
    init_db(db_path)
    merge_close_results(db_path)
    requeue_running(db_path, os.getpid())
    work_dir = settings.data_dir / "frames"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    drop_orphan_small(db_path, settings.video_dir)
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
        threading.Thread(target=_shrink_loop, args=(db_path, stop), daemon=True).start()
        threading.Thread(target=webhook_loop, args=(db_path, stop), daemon=True).start()
        while not broken.is_set():
            touch_heartbeat(heartbeat_path())
            now = time.monotonic()
            if now - last_sweep >= 60:
                last_sweep = now
                drop_stale_uploads(db_path)
                release_stale_pc(db_path)
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
