"""Thêm video từ nhiều máy, xem thống kê, tải bản sao lưu về PC."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from control_plane.fresh_lane import (
    fresh_db_path,
    fresh_heartbeat,
    fresh_report,
    fresh_results,
    fresh_video_dir,
    import_kept_videos,
)
from control_plane.settings import derive_secret, settings
from control_plane.video_scan import IMAGE_SUFFIXES, VIDEO_SUFFIXES
from control_plane.video_store import (
    abort_upload,
    begin_upload,
    can_accept,
    clean_device,
    commit_upload,
    correct_result,
    count_results,
    export_contact_rows,
    feeder_count,
    free_reserve_bytes,
    init_db,
    iter_backup,
    keep_video_count,
    keep_video_gb,
    list_videos,
    queued_bytes,
    reader_alive,
    rescue_stuck_uploads,
    render_csv,
    render_vcf,
    retry,
    result_shot_path,
    search_results,
    should_pause_ocr,
    stats,
    worker_count,
)
from control_plane.video_upload_sessions import (
    DEFAULT_CHUNK_SIZE,
    abort_session,
    cleanup_stale_uploads,
    TailOffsetError,
    complete_upload,
    ensure_upload_tables,
    get_upload,
    init_upload,
    list_receiving_uploads,
    note_tail,
    open_tail,
    put_chunk,
    pwrite_at,
    receiving_count,
    save_vision_payload,
    upload_slots,
)
from control_plane.video_validate import probe_duration_sec, validate_media_file

router = APIRouter()
_ALLOWED = VIDEO_SUFFIXES | IMAGE_SUFFIXES
TICKET_SECONDS = 600


def _uploads_db() -> Path:
    path = settings.video_uploads_db_path
    ensure_upload_tables(path, legacy_db=settings.video_db_path)
    return path


def _cleanup_uploads() -> int:
    """Dọn phiên tải chết / file mồ côi — gọi nhẹ mỗi lần xem hàng đợi."""
    try:
        removed = cleanup_stale_uploads(_uploads_db(), settings.video_dir)
    except Exception as exc:
        print(f"cleanup uploads: {exc}", flush=True)
        removed = 0
    try:
        rescue_stuck_uploads(settings.video_db_path, settings.video_dir)
    except Exception as exc:
        print(f"rescue uploads: {exc}", flush=True)
    return removed


def _locked_http(_exc: sqlite3.OperationalError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Máy chủ đang bận ghi DB — thử lại mảnh này (tự resume).",
        headers={"Retry-After": "3"},
    )


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
    cleaned = _cleanup_uploads()
    body = stats(settings.video_db_path)
    body["workers"] = worker_count()
    body["feeders"] = feeder_count()
    body["keep_videos"] = keep_video_count()
    body["keep_gb"] = keep_video_gb()
    body["reader_alive"] = reader_alive(settings.data_dir / "video-worker.heartbeat")
    body["disk_used_bytes"] = queued_bytes(settings.video_db_path)
    body["disk_limit_bytes"] = _limit_bytes()
    try:
        free = shutil.disk_usage(settings.video_dir).free
    except OSError:
        free = 0
    body["disk_free_bytes"] = free
    body["disk_reserve_bytes"] = free_reserve_bytes()
    body["upload_slots"] = upload_slots()
    body["uploads_receiving"] = receiving_count(_uploads_db())
    body["uploads_stale"] = receiving_count(_uploads_db(), include_stale=True) - int(body["uploads_receiving"])
    body["uploads_cleaned"] = cleaned
    body["ocr_paused"] = should_pause_ocr(settings.video_db_path, settings.video_dir)
    return body


@router.get("/v1/videos")
def videos(authorization: str | None = Header(default=None), limit: int = Query(default=40, ge=1, le=200)) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    _cleanup_uploads()
    uploads = list_receiving_uploads(_uploads_db(), limit=min(80, max(limit, 40)))
    items = list_videos(settings.video_db_path, limit)
    # Phiên đang tải lên trước — luôn thấy trong hàng đợi kể cả khi iPhone ngủ giữa chừng.
    merged = uploads + items
    return {
        "count": len(merged),
        "items": merged,
        "uploads": uploads,
        "uploads_receiving": receiving_count(_uploads_db()),
    }


def _write_chunk(handle: BinaryIO, hasher: Any, chunk: bytes) -> None:
    hasher.update(chunk)
    handle.write(chunk)


async def _store_video_upload(
    file: UploadFile,
    device: str,
    db_path: Path,
    video_dir: Path,
    *,
    allow_repeat: bool,
) -> dict[str, object]:
    """Ghi đĩa và cơ sở dữ liệu chạy ngoài vòng sự kiện, để một video lớn không làm đứng trang của máy khác."""
    await run_in_threadpool(init_db, db_path)
    video_dir.mkdir(parents=True, exist_ok=True)
    original = Path(file.filename or "video.mp4").name
    suffix = Path(original).suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Chỉ nhận video hoặc ảnh chụp màn hình.")
    machine = clean_device(device)
    tmp = video_dir / f"up-{secrets.token_hex(8)}{suffix}"
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
        try:
            await run_in_threadpool(validate_media_file, tmp)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        free = shutil.disk_usage(video_dir).free
        used = await run_in_threadpool(queued_bytes, db_path)
        if not can_accept(used, size, _limit_bytes(), free):
            raise HTTPException(status_code=507, detail="Hàng đợi đã đầy. Đợi bớt video rồi thêm tiếp.")
        created = await run_in_threadpool(
            lambda: begin_upload(
                db_path,
                name=original,
                size_bytes=size,
                sha256=hasher.hexdigest(),
                device=machine,
                allow_repeat=allow_repeat,
            )
        )
        if created["duplicate"]:
            return created
        video_id = int(created["id"])
        final = video_dir / f"{video_id}{suffix}"
        os.replace(tmp, final)
        old_path = str(created.pop("old_path", "") or "")
        if old_path and old_path != str(final):
            Path(old_path).unlink(missing_ok=True)
        duration = await run_in_threadpool(probe_duration_sec, final)
        await run_in_threadpool(commit_upload, db_path, video_id, str(final), duration)
        created["status"] = "queued"
        created["duration_sec"] = duration
        return created
    except Exception:
        if created is not None and not created.get("duplicate"):
            await run_in_threadpool(abort_upload, db_path, int(created["id"]), bool(created.get("reopened")))
        if final is not None:
            final.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()


@router.post("/v1/videos")
async def upload_video(
    file: UploadFile = File(...),
    device: str = Form(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _auth(authorization)
    settings.ensure_dirs()
    return await _store_video_upload(
        file, device, settings.video_db_path, settings.video_dir, allow_repeat=False
    )


@router.post("/v1/fresh/videos")
async def upload_fresh_video(
    file: UploadFile = File(...),
    device: str = Form(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Tải vào bảng mới, kể cả file đã có ở bảng cũ. Đọc từ đầu, không đụng kết quả cũ."""
    _auth(authorization)
    return await _store_video_upload(
        file,
        device,
        fresh_db_path(settings.data_dir),
        fresh_video_dir(settings.data_dir),
        allow_repeat=True,
    )


@router.get("/v1/fresh/videos")
def fresh_videos(authorization: str | None = Header(default=None), limit: int = Query(default=40, ge=1, le=200)) -> dict[str, object]:
    _auth(authorization)
    db_path = fresh_db_path(settings.data_dir)
    init_db(db_path)
    items = list_videos(db_path, limit)
    return {"count": len(items), "items": items}


@router.post("/v1/fresh/import")
def import_fresh_videos(authorization: str | None = Header(default=None)) -> dict[str, int]:
    """Video còn file trên đĩa được đưa sang bảng mới và đọc lại từ đầu."""
    _auth(authorization)
    return import_kept_videos(settings.video_db_path, settings.data_dir)


@router.get("/v1/fresh/report")
def fresh_read_report(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    db_path = fresh_db_path(settings.data_dir)
    report = fresh_report(db_path)
    report["reader_alive"] = reader_alive(fresh_heartbeat(settings.data_dir))
    return report


@router.get("/v1/fresh/results")
def fresh_result_list(
    q: str = "",
    limit: int = Query(default=80, ge=1, le=80),
    offset: int = Query(default=0, ge=0),
    view: str = Query(default="all"),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _auth(authorization)
    if view not in ("complete", "incomplete", "all"):
        raise HTTPException(status_code=422, detail="view phải là complete, incomplete hoặc all.")
    db_path = fresh_db_path(settings.data_dir)
    init_db(db_path)
    items = fresh_results(db_path, q, limit, offset, view)
    total = count_results(db_path, q, view)
    return {"count": len(items), "total": total, "offset": offset, "view": view, "items": items}


@router.get("/v1/fresh/results/{result_id}/shot/{field}")
def fresh_result_shot(
    result_id: int,
    field: str,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    _auth(authorization)
    db_path = fresh_db_path(settings.data_dir)
    init_db(db_path)
    path = result_shot_path(db_path, result_id, field)
    if path is None:
        raise HTTPException(status_code=404, detail="Không có ảnh dải chữ.")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/v1/videos/{video_id}/retry")
def retry_video(video_id: int, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(authorization)
    if not retry(settings.video_db_path, video_id):
        raise HTTPException(
            status_code=404,
            detail=(
                "Không đọc lại được — cần file còn giữ trên đĩa "
                f"(tối đa {keep_video_count()} video gần nhất)."
            ),
        )
    return {"ok": True}


class ChunkInitBody(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    size_bytes: int = Field(gt=0, le=16 * 1024 * 1024 * 1024)
    device: str = ""
    client_key: str = ""
    chunk_size: int | None = None
    sha256: str = ""


@router.post("/v1/videos/uploads")
async def start_chunked_upload(
    body: ChunkInitBody,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Bắt đầu / resume phiên tải cắt khúc (B)."""
    _auth(authorization)
    await run_in_threadpool(init_db, settings.video_db_path)
    settings.ensure_dirs()
    suffix = Path(body.name).suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Chỉ nhận video hoặc ảnh chụp màn hình.")
    uploads_db = _uploads_db()

    def _open() -> dict[str, object]:
        opened = init_upload(
            uploads_db,
            settings.video_dir,
            name=body.name,
            size_bytes=body.size_bytes,
            device=body.device,
            client_key=body.client_key,
            chunk_size=body.chunk_size or DEFAULT_CHUNK_SIZE,
            sha256=body.sha256,
            disk_limit_bytes=_limit_bytes(),
            video_db_path=settings.video_db_path,
            legacy_db=settings.video_db_path,
        )
        # Nhường đĩa trước khi iPhone gửi mảnh đầu — không đợi vòng thăm của bộ đọc.
        try:
            from control_plane.upload_priority import apply_upload_priority

            apply_upload_priority("arm" if int(opened.get("received_bytes") or 0) <= 0 else "flow")
        except Exception as exc:
            print(f"ưu tiên upload: {exc}", flush=True)
        return opened

    try:
        return await run_in_threadpool(_open)
    except MemoryError as exc:
        # Client (web/iOS) đọc Retry-After rồi tự thử lại / resume — không mất video.
        raise HTTPException(
            status_code=507,
            detail=str(exc),
            headers={"Retry-After": "90"},
        ) from exc
    except sqlite3.OperationalError as exc:
        raise _locked_http(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v1/videos/uploads/{upload_id}")
def chunked_upload_status(
    upload_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _auth(authorization)
    body = get_upload(_uploads_db(), upload_id)
    if body is None:
        raise HTTPException(status_code=404, detail="Không thấy phiên tải.")
    return body


@router.put("/v1/videos/uploads/{upload_id}/chunks/{index}")
async def upload_chunk(
    upload_id: str,
    index: int,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _auth(authorization)
    started = time.perf_counter()
    data = await request.body()
    read_s = time.perf_counter() - started
    uploads_db = _uploads_db()
    try:
        worked = time.perf_counter()
        body = await run_in_threadpool(put_chunk, uploads_db, upload_id, index, data)
        work_s = time.perf_counter() - worked
        # Đĩa/CPU nghẽn thì ACK chậm và iPhone ngừng gửi mảnh kế.
        if work_s >= 0.4:
            print(
                f"chunk chậm index={index} bytes={len(data)} đọc={read_s:.2f}s ghi={work_s:.2f}s",
                flush=True,
            )
        return body
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise _locked_http(exc) from exc


@router.put("/v1/videos/uploads/{upload_id}/body")
async def upload_body(
    upload_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_upload_offset: str | None = Header(default=None),
) -> dict[str, object]:
    """Một file phần đuôi. Body được ghi từng khúc, không đọc hết 1.5GB vào RAM.

    iOS 16 không có uploadTask(withResumeData:). Client hỏi contiguous_offset
    rồi gửi một uploadTask(fromFile:) với header X-Upload-Offset.
    """
    _auth(authorization)
    if x_upload_offset is None or not str(x_upload_offset).strip():
        raise HTTPException(status_code=400, detail="Thiếu X-Upload-Offset")
    try:
        offset = int(x_upload_offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="X-Upload-Offset không phải số") from exc
    uploads_db = _uploads_db()
    try:
        path = await run_in_threadpool(open_tail, uploads_db, upload_id, offset)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TailOffsetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    written = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            n = await run_in_threadpool(pwrite_at, path, offset + written, bytes(chunk))
            written += int(n)
    finally:
        if written > 0:
            try:
                await run_in_threadpool(note_tail, uploads_db, upload_id, offset, written)
            except (LookupError, ValueError, sqlite3.OperationalError) as exc:
                print(f"note_tail: {exc}", flush=True)
    body = await run_in_threadpool(get_upload, uploads_db, upload_id)
    return body or {"ok": True, "written": written}


_VISION_BODY_LIMIT = 2 * 1024 * 1024


@router.post("/v1/videos/uploads/{upload_id}/vision")
async def upload_vision(
    upload_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """JSON chữ Apple Vision đọc trên iPhone khi app đang mở. Không thay quét máy chủ."""
    _auth(authorization)
    data = await request.body()
    if len(data) > _VISION_BODY_LIMIT:
        raise HTTPException(status_code=413, detail="Sidecar Vision quá 2MB")
    try:
        return await run_in_threadpool(save_vision_payload, _uploads_db(), upload_id, data)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise _locked_http(exc) from exc


@router.post("/v1/videos/uploads/{upload_id}/complete")
async def finish_chunked_upload(
    upload_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _auth(authorization)
    await run_in_threadpool(init_db, settings.video_db_path)
    uploads_db = _uploads_db()
    try:
        return await run_in_threadpool(
            lambda: complete_upload(
                uploads_db,
                settings.video_dir,
                upload_id,
                disk_limit_bytes=_limit_bytes(),
                video_db_path=settings.video_db_path,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise _locked_http(exc) from exc
    except MemoryError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/v1/videos/uploads/{upload_id}")
def cancel_chunked_upload(
    upload_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    _auth(authorization)
    if not abort_session(_uploads_db(), upload_id):
        raise HTTPException(status_code=404, detail="Không thấy phiên tải.")
    return {"ok": True}


# Trang cũ xin 1000 dòng rồi lặp đến hết (hơn một vạn dòng). Safari chỉ có 6 kết nối,
# bảng đó chiếm hết nên video không gửi lên được. Kẹp mọi request về một trang nhỏ.
_RESULTS_PAGE_CAP = 80


@router.get("/v1/results")
def results(
    authorization: str | None = Header(default=None),
    q: str = "",
    limit: int = Query(default=80, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    view: str = Query(default="complete", pattern="^(complete|incomplete|all)$"),
) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    limit = min(int(limit), _RESULTS_PAGE_CAP)
    items = search_results(settings.video_db_path, q, limit, offset, view)
    total = count_results(settings.video_db_path, q, view)
    return {"count": len(items), "total": total, "offset": offset, "view": view, "items": items}


class ResultCorrection(BaseModel):
    phone: str = ""
    name: str = ""
    username: str = ""


@router.get("/v1/results/{result_id}/shot/{field}")
def result_shot(
    result_id: int,
    field: str,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    """Ảnh dải chữ chưa đọc được, đúng ô của hàng đó."""
    _auth(authorization)
    init_db(settings.video_db_path)
    path = result_shot_path(settings.video_db_path, result_id, field)
    if path is None:
        raise HTTPException(status_code=404, detail="Không có ảnh dải chữ.")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/v1/results/{result_id}")
def patch_result(
    result_id: int,
    body: ResultCorrection,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Sửa số, tên, @. Ảnh chỉ là dải chữ chưa đọc — không đoán chữ dưới chấm."""
    _auth(authorization)
    init_db(settings.video_db_path)
    try:
        return correct_result(
            settings.video_db_path,
            result_id,
            body.phone,
            body.name,
            body.username,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise _locked_http(exc) from exc


@router.get("/v1/results/export.csv")
def export_results_csv(authorization: str | None = Header(default=None)) -> Response:
    """Một số một dòng. Không đổi % đủ."""
    _auth(authorization)
    init_db(settings.video_db_path)
    text = render_csv(export_contact_rows(settings.video_db_path))
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="danh-ba.csv"'},
    )


@router.get("/v1/results/export.vcf")
def export_results_vcf(authorization: str | None = Header(default=None)) -> Response:
    """vCard: mỗi số điện thoại một thẻ. Không đổi % đủ."""
    _auth(authorization)
    init_db(settings.video_db_path)
    text = render_vcf(export_contact_rows(settings.video_db_path))
    return Response(
        content=text,
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="danh-ba.vcf"'},
    )


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
