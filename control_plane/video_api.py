"""Thêm video từ nhiều máy, xem thống kê, tải bản sao lưu về PC."""

from __future__ import annotations

import hashlib
import zlib
import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from control_plane.settings import settings
from control_plane.video_scan import IMAGE_SUFFIXES, VIDEO_SUFFIXES
from control_plane.video_store import (
    abort_upload,
    begin_upload,
    can_accept,
    commit_upload,
    init_db,
    iter_backup,
    list_videos,
    queued_bytes,
    reader_alive,
    retry,
    search_results,
    stats,
    worker_count,
)

router = APIRouter()
_tickets: dict[str, float] = {}
_ALLOWED = VIDEO_SUFFIXES | IMAGE_SUFFIXES


def _auth(authorization: str | None) -> None:
    expected = settings.token or os.environ.get("CONTROL_TOKEN")
    if not expected:
        return
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _limit_bytes() -> int:
    return settings.video_disk_gb * 1024 * 1024 * 1024


@router.get("/v1/videos/stats")
def video_stats(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    body = stats(settings.video_db_path)
    body["workers"] = worker_count()
    body["reader_alive"] = reader_alive(settings.data_dir / "video-worker.heartbeat")
    body["disk_used_bytes"] = queued_bytes(settings.video_db_path)
    body["disk_limit_bytes"] = _limit_bytes()
    return body


@router.get("/v1/videos")
def videos(authorization: str | None = Header(default=None), limit: int = Query(default=40, ge=1, le=200)) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    items = list_videos(settings.video_db_path, limit)
    return {"count": len(items), "items": items}


@router.post("/v1/videos")
async def upload_video(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    settings.ensure_dirs()
    original = Path(file.filename or "video.mp4").name
    suffix = Path(original).suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Chỉ nhận video hoặc ảnh chụp màn hình.")
    tmp = settings.video_dir / f"up-{secrets.token_hex(8)}{suffix}"
    hasher = hashlib.sha256()
    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    video_id: int | None = None
    final: Path | None = None
    try:
        with tmp.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="Video quá lớn.")
                hasher.update(chunk)
                handle.write(chunk)
        free = shutil.disk_usage(settings.video_dir).free
        if not can_accept(queued_bytes(settings.video_db_path), size, _limit_bytes(), free):
            raise HTTPException(status_code=507, detail="Hàng đợi đã đầy. Đợi bớt video rồi thêm tiếp.")
        created = begin_upload(settings.video_db_path, name=original, size_bytes=size, sha256=hasher.hexdigest())
        if created["duplicate"]:
            return created
        video_id = int(created["id"])
        final = settings.video_dir / f"{video_id}{suffix}"
        os.replace(tmp, final)
        commit_upload(settings.video_db_path, video_id, str(final))
        created["status"] = "queued"
        return created
    except Exception:
        if video_id is not None:
            abort_upload(settings.video_db_path, video_id)
        if final is not None:
            final.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()


@router.post("/v1/videos/{video_id}/retry")
def retry_video(video_id: int, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(authorization)
    if not retry(settings.video_db_path, video_id):
        raise HTTPException(status_code=404, detail="Không đọc lại được video này.")
    return {"ok": True}


@router.get("/v1/results")
def results(
    authorization: str | None = Header(default=None),
    q: str = "",
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    items = search_results(settings.video_db_path, q, limit)
    return {"count": len(items), "items": items}


@router.post("/v1/backup/ticket")
def backup_ticket(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _auth(authorization)
    now = time.time()
    expired = [key for key, until in _tickets.items() if until < now]
    for key in expired:
        _tickets.pop(key, None)
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = now + 600
    return {"url": f"/v1/backup/{ticket}"}


@router.get("/v1/backup/{ticket}")
def backup_download(ticket: str) -> StreamingResponse:
    until = _tickets.get(ticket)
    if until is None or until < time.time():
        raise HTTPException(status_code=404, detail="Liên kết tải đã hết hạn. Bấm sao lưu lại.")
    init_db(settings.video_db_path)

    def chunks():
        compressor = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        first = True
        for line in iter_backup(settings.video_db_path):
            payload = line.encode("utf-8")
            if first:
                payload = b"\xef\xbb\xbf" + payload
                first = False
            data = compressor.compress(payload)
            if data:
                yield data
        tail = compressor.flush()
        if tail:
            yield tail

    filename = time.strftime("sao-luu-danh-ba-%Y%m%d-%H%M.csv.gz")
    return StreamingResponse(
        chunks(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
