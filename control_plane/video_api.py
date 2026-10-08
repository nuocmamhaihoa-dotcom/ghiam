"""Thêm video từ nhiều máy, xem thống kê, tải bản sao lưu về PC."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import time
import zlib
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from control_plane.settings import derive_secret, settings
from control_plane.video_scan import IMAGE_SUFFIXES, VIDEO_SUFFIXES
from control_plane.video_store import (
    abort_upload,
    begin_upload,
    can_accept,
    clean_device,
    commit_upload,
    count_results,
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
_ALLOWED = VIDEO_SUFFIXES | IMAGE_SUFFIXES
TICKET_SECONDS = 600


def _auth(authorization: str | None) -> None:
    accepted = [token for token in (settings.token, settings.page_token) if token]
    if not accepted:
        return
    if not authorization or not any(hmac.compare_digest(authorization, f"Bearer {token}") for token in accepted):
        raise HTTPException(status_code=401, detail="unauthorized")


def _limit_bytes() -> int:
    return settings.video_disk_gb * 1024 * 1024 * 1024


def _ticket_key() -> bytes:
    return derive_secret(settings.token or "no-token", "backup-ticket").encode("utf-8")


def make_ticket(now: float) -> str:
    """Vé tải tự kiểm được, nên chạy nhiều tiến trình web vẫn nhận ra vé của nhau."""
    expiry = str(int(now) + TICKET_SECONDS)
    signature = hmac.new(_ticket_key(), expiry.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{expiry}-{signature}"


def ticket_ok(ticket: str, now: float) -> bool:
    expiry, _, signature = ticket.partition("-")
    if not expiry.isdigit() or int(expiry) < now:
        return False
    expected = hmac.new(_ticket_key(), expiry.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(signature, expected)


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


def _write_chunk(handle: BinaryIO, hasher: Any, chunk: bytes) -> None:
    hasher.update(chunk)
    handle.write(chunk)


@router.post("/v1/videos")
async def upload_video(
    file: UploadFile = File(...),
    device: str = Form(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Ghi đĩa và cơ sở dữ liệu chạy ngoài vòng sự kiện, để một video lớn không làm đứng trang của máy khác."""
    _auth(authorization)
    await run_in_threadpool(init_db, settings.video_db_path)
    settings.ensure_dirs()
    original = Path(file.filename or "video.mp4").name
    suffix = Path(original).suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Chỉ nhận video hoặc ảnh chụp màn hình.")
    machine = clean_device(device)
    tmp = settings.video_dir / f"up-{secrets.token_hex(8)}{suffix}"
    hasher = hashlib.sha256()
    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    created: dict[str, object] | None = None
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
                await run_in_threadpool(_write_chunk, handle, hasher, chunk)
        free = shutil.disk_usage(settings.video_dir).free
        used = await run_in_threadpool(queued_bytes, settings.video_db_path)
        if not can_accept(used, size, _limit_bytes(), free):
            raise HTTPException(status_code=507, detail="Hàng đợi đã đầy. Đợi bớt video rồi thêm tiếp.")
        created = await run_in_threadpool(
            lambda: begin_upload(
                settings.video_db_path,
                name=original,
                size_bytes=size,
                sha256=hasher.hexdigest(),
                device=machine,
            )
        )
        if created["duplicate"]:
            return created
        video_id = int(created["id"])
        final = settings.video_dir / f"{video_id}{suffix}"
        os.replace(tmp, final)
        old_path = str(created.pop("old_path", "") or "")
        if old_path and old_path != str(final):
            Path(old_path).unlink(missing_ok=True)
        await run_in_threadpool(commit_upload, settings.video_db_path, video_id, str(final))
        created["status"] = "queued"
        return created
    except Exception:
        if created is not None and not created.get("duplicate"):
            await run_in_threadpool(
                abort_upload, settings.video_db_path, int(created["id"]), bool(created.get("reopened"))
            )
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
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    items = search_results(settings.video_db_path, q, limit, offset)
    total = count_results(settings.video_db_path, q)
    return {"count": len(items), "total": total, "offset": offset, "items": items}


@router.post("/v1/backup/ticket")
def backup_ticket(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _auth(authorization)
    return {"url": f"/v1/backup/{make_ticket(time.time())}"}


@router.get("/v1/backup/{ticket}")
def backup_download(ticket: str) -> StreamingResponse:
    if not ticket_ok(ticket, time.time()):
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
