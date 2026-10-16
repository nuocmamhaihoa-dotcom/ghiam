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

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from control_plane.settings import derive_secret, settings
from control_plane.upload_parts import (
    UploadRejected,
    check_upload_id,
    lookup_part,
    release_finish,
    save_done,
    write_part,
)
from control_plane.video_scan import IMAGE_SUFFIXES, VIDEO_SUFFIXES
from control_plane.pc_reader import (
    code_sha256,
    PC_TOOL_FILES,
    connector_bytes,
    connector_template_sha256,
    default_tessdata_zip,
    launcher_bytes,
    open_script_bytes,
    reader_manifest,
    reader_zip,
    table_from_payload,
    tessdata_zip,
    weights_file,
    weights_info,
)
from control_plane.video_store import (
    abort_upload,
    begin_upload,
    claim_pc,
    clean_device,
    commit_upload,
    device_day,
    disk_limit_bytes,
    finish,
    init_db,
    iter_backup,
    list_videos,
    pc_video_path,
    prune_old_videos,
    reader_alive,
    release_pc,
    release_stale_pc,
    result_page,
    retry,
    start_full_check,
    stats,
    stop_full_check,
    touch_pc,
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


def _disk_snapshot() -> tuple[int, int, int]:
    usage = shutil.disk_usage(settings.video_dir)
    return usage.used, usage.free, disk_limit_bytes(usage.total, settings.video_disk_percent)


def _ticket_key(purpose: str = "backup-ticket") -> bytes:
    return derive_secret(settings.token or "no-token", purpose).encode("utf-8")


def make_ticket(now: float, purpose: str = "backup-ticket", seconds: int = TICKET_SECONDS) -> str:
    """Vé tải tự kiểm được, nên chạy nhiều tiến trình web vẫn nhận ra vé của nhau."""
    expiry = str(int(now) + seconds)
    signature = hmac.new(_ticket_key(purpose), expiry.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{expiry}-{signature}"


def ticket_ok(ticket: str, now: float, purpose: str = "backup-ticket") -> bool:
    expiry, _, signature = ticket.partition("-")
    if not expiry.isdigit() or int(expiry) < now:
        return False
    expected = hmac.new(_ticket_key(purpose), expiry.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(signature, expected)


PC_TICKET = "pc-ticket"
# Link cài trên nhiều PC. Vé sao lưu vẫn hết hạn sau TICKET_SECONDS.
PC_TICKET_SECONDS = 24 * 60 * 60


def _public_base(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if not host or "\n" in host or "\r" in host:
        return str(request.base_url).rstrip("/")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if scheme not in ("http", "https"):
        scheme = request.url.scheme
    return f"{scheme}://{host}".rstrip("/")


def _page_token() -> str:
    return settings.page_token or settings.token


class PcClaimBody(BaseModel):
    name: str = "PC"


class PcLeaseBody(BaseModel):
    lease: str = ""


class PcResultBody(BaseModel):
    lease: str = ""
    rows: list[dict] = Field(default_factory=list)
    review: list[dict] = Field(default_factory=list)
    unopened: list[dict] = Field(default_factory=list)


@router.get("/v1/videos/stats")
def video_stats(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    release_stale_pc(settings.video_db_path)
    body = stats(settings.video_db_path)
    body["workers"] = worker_count()
    body["reader_alive"] = reader_alive(settings.data_dir / "video-worker.heartbeat")
    used, _free, limit = _disk_snapshot()
    body["disk_used_bytes"] = used
    body["disk_limit_bytes"] = limit
    body["phones"] = device_day(settings.video_db_path)
    return body


@router.get("/v1/videos")
def videos(
    authorization: str | None = Header(default=None),
    limit: int | None = Query(default=None, ge=1),
    device: str | None = Query(default=None),
) -> dict[str, object]:
    """Không truyền limit thì trả hết video, mới nhất trước. device có mặt thì chỉ máy đó."""
    _auth(authorization)
    init_db(settings.video_db_path)
    chosen = None if device is None else device
    items = list_videos(settings.video_db_path, limit, chosen)
    return {"count": len(items), "items": items}


def _write_chunk(handle: BinaryIO, hasher: Any, chunk: bytes) -> None:
    hasher.update(chunk)
    handle.write(chunk)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def _reject(error: UploadRejected) -> JSONResponse:
    body: dict[str, object] = {"detail": error.detail}
    if error.received is not None:
        body["received"] = error.received
    if error.restart:
        body["restart"] = True
    return JSONResponse(body, status_code=error.status)


async def _publish_file(src: Path, original: str, size: int, digest: str, device: str) -> dict[str, object]:
    """Đưa file đã nằm đủ trên đĩa vào hàng chờ. File nguồn được chuyển đi, không copy thêm một bản."""
    await run_in_threadpool(
        lambda: prune_old_videos(
            settings.video_db_path,
            settings.video_dir,
            percent=settings.video_disk_percent,
            protect_newest=False,
        )
    )
    used, free, limit = _disk_snapshot()
    if used > limit or free <= 2 * 1024 * 1024 * 1024:
        raise HTTPException(
            status_code=507,
            detail=(
                f"Ổ đĩa đã tới {settings.video_disk_percent}%."
                " Video đang đọc chưa xóa được. Đợi đọc xong rồi tải tiếp."
            ),
        )
    created: dict[str, object] | None = None
    final: Path | None = None
    try:
        created = await run_in_threadpool(
            lambda: begin_upload(
                settings.video_db_path,
                name=original,
                size_bytes=size,
                sha256=digest,
                device=device,
            )
        )
        if created["duplicate"]:
            await run_in_threadpool(lambda: src.unlink(missing_ok=True))
            return created
        video_id = int(created["id"])
        final = settings.video_dir / f"{video_id}{Path(original).suffix.lower()}"
        await run_in_threadpool(lambda: os.replace(src, final))
        old_path = str(created.pop("old_path", "") or "")
        if old_path and old_path != str(final):
            await run_in_threadpool(lambda: Path(old_path).unlink(missing_ok=True))
        await run_in_threadpool(commit_upload, settings.video_db_path, video_id, str(final))
        created["status"] = "queued"
        return created
    except Exception:
        if created is not None and not created.get("duplicate"):
            await run_in_threadpool(
                abort_upload, settings.video_db_path, int(created["id"]), bool(created.get("reopened"))
            )
        if final is not None and final.is_file() and src != final:
            await run_in_threadpool(lambda: final.unlink(missing_ok=True))
        raise


@router.post("/v1/videos")
async def upload_video(
    file: UploadFile = File(...),
    device: str = Form(""),
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
    tmp = settings.video_dir / f"up-{secrets.token_hex(8)}{suffix}"
    hasher = hashlib.sha256()
    size = 0
    upload_limit = settings.max_upload_mb * 1024 * 1024
    try:
        with tmp.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > upload_limit:
                    raise HTTPException(status_code=413, detail="Video quá lớn.")
                await run_in_threadpool(_write_chunk, handle, hasher, chunk)
        return await _publish_file(tmp, original, size, hasher.hexdigest(), device)
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()


@router.get("/v1/videos/chunks/{upload_id}")
async def chunk_status(upload_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
    """Số byte đã nằm trên đĩa. Trình duyệt hỏi trước khi gửi tiếp, để không gửi lại phần đã lên."""
    _auth(authorization)
    try:
        checked = check_upload_id(upload_id)
    except UploadRejected as error:
        return _reject(error)
    found = await run_in_threadpool(lookup_part, settings.video_dir, checked)
    if found is None:
        return JSONResponse({"detail": "Chưa có phần nào.", "received": 0}, status_code=404)
    return JSONResponse(found)


@router.post("/v1/videos/chunks")
async def upload_chunk(
    upload_id: str = Form(...),
    name: str = Form(...),
    size: int = Form(...),
    offset: int = Form(...),
    device: str = Form(""),
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Nhận một mảnh. Đủ file thì đưa vào hàng chờ. Đứt mạng thì mảnh sau nối vào byte đã có."""
    _auth(authorization)
    await run_in_threadpool(init_db, settings.video_db_path)
    settings.ensure_dirs()
    phone = clean_device(device)
    try:
        checked = check_upload_id(upload_id)
        data = await file.read(8 * 1024 * 1024 + 1)
        outcome = await run_in_threadpool(
            lambda: write_part(
                settings.video_dir,
                upload_id=checked,
                name=name,
                size=size,
                device=phone,
                offset=offset,
                data=data,
                max_bytes=settings.max_upload_mb * 1024 * 1024,
            )
        )
    except UploadRejected as error:
        return _reject(error)
    finally:
        await file.close()
    saved = outcome.get("saved")
    if isinstance(saved, dict):
        return JSONResponse(saved)
    if not outcome.get("complete"):
        return JSONResponse(
            {
                "received": outcome.get("received", 0),
                "size": outcome.get("size", size),
                "complete": False,
                "pending": bool(outcome.get("pending")),
            }
        )
    try:
        digest = await run_in_threadpool(_hash_file, Path(str(outcome["path"])))
        created = await _publish_file(
            Path(str(outcome["path"])),
            str(outcome.get("name") or name),
            int(outcome["size"]),
            digest,
            str(outcome.get("device") or device),
        )
    except Exception:
        await run_in_threadpool(release_finish, settings.video_dir, checked)
        raise
    created["received"] = int(outcome["size"])
    created["complete"] = True
    await run_in_threadpool(save_done, settings.video_dir, checked, created)
    return JSONResponse(created)


@router.post("/v1/videos/{video_id}/retry")
def retry_video(video_id: int, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(authorization)
    if not retry(settings.video_db_path, video_id):
        raise HTTPException(status_code=404, detail="Không đọc lại được video này.")
    return {"ok": True}


@router.post("/v1/results/check/start")
def start_result_check(authorization: str | None = Header(default=None)) -> dict[str, int]:
    _auth(authorization)
    init_db(settings.video_db_path)
    outcome = start_full_check(settings.video_db_path)
    print(
        f"Bắt đầu kiểm {outcome['videos']} video, {outcome['rows']} dòng đủ ba ô.",
        flush=True,
    )
    return outcome


@router.post("/v1/results/check/stop")
def stop_result_check(authorization: str | None = Header(default=None)) -> dict[str, int]:
    _auth(authorization)
    init_db(settings.video_db_path)
    outcome = stop_full_check(settings.video_db_path)
    print(
        f"Dừng kiểm {outcome['stopped']} video chưa đọc. Còn {outcome['reading']} video đang kiểm.",
        flush=True,
    )
    return outcome


@router.get("/v1/results")
def results(
    authorization: str | None = Header(default=None),
    q: str = "",
    limit: int = Query(default=500, ge=1, le=1000),
    before: int = Query(default=0, ge=0),
    after: int = Query(default=0, ge=0),
) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    page = result_page(settings.video_db_path, q, limit, before, after)
    items = page["items"]
    page["count"] = len(items) if isinstance(items, list) else 0
    return page


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


@router.get("/v1/pc/reader")
def pc_reader_manifest(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    return reader_manifest()


@router.get("/v1/pc/reader.zip")
def pc_reader_zip(authorization: str | None = Header(default=None)) -> Response:
    _auth(authorization)
    digest = code_sha256()
    return Response(
        content=reader_zip(),
        media_type="application/zip",
        headers={
            "X-Reader-Sha256": digest,
            "Cache-Control": "no-cache",
        },
    )


@router.get("/v1/pc/weights")
def pc_weights(authorization: str | None = Header(default=None)) -> FileResponse:
    _auth(authorization)
    info = weights_info()
    path = weights_file()
    if info is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Máy chủ không có bộ đọc dấu.")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename="vgg_transformer.pth",
        headers={"X-Weights-Sha256": str(info["sha256"]), "Cache-Control": "no-cache"},
    )


@router.get("/v1/pc/tessdata.zip")
def pc_tessdata(authorization: str | None = Header(default=None)) -> Response:
    _auth(authorization)
    payload = tessdata_zip()
    if payload is None:
        raise HTTPException(status_code=404, detail="Máy chủ không có bộ chữ riêng.")
    return Response(content=payload, media_type="application/zip", headers={"Cache-Control": "no-cache"})


@router.get("/v1/pc/tessdata-default.zip")
def pc_tessdata_default(authorization: str | None = Header(default=None)) -> Response:
    _auth(authorization)
    payload = default_tessdata_zip()
    if payload is None:
        raise HTTPException(status_code=404, detail="Máy chủ không có bộ chữ mặc định.")
    return Response(content=payload, media_type="application/zip", headers={"Cache-Control": "no-cache"})


@router.get("/v1/pc/open.ps1")
def pc_open_script(authorization: str | None = Header(default=None)) -> Response:
    _auth(authorization)
    return Response(
        content=open_script_bytes(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/v1/pc/tools/{name}")
def pc_tool(name: str, authorization: str | None = Header(default=None)) -> FileResponse:
    _auth(authorization)
    filename = PC_TOOL_FILES.get(name)
    if filename is None or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Không có bộ cài này.")
    path = settings.data_dir / "pc-tools" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Máy chủ chưa có bộ cài này.")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/v1/pc/connector/version")
def pc_connector_version(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    manifest = reader_manifest()
    return {"version": int(manifest["version"]), "sha256": connector_template_sha256()}


@router.get("/v1/pc/connector.py")
def pc_connector_script(request: Request, authorization: str | None = Header(default=None)) -> Response:
    _auth(authorization)
    token = _page_token()
    if not token:
        raise HTTPException(status_code=404, detail="Chưa có mã trang.")
    return Response(
        content=connector_bytes(_public_base(request), token),
        media_type="text/x-python",
        headers={"Cache-Control": "no-cache", "Content-Disposition": 'attachment; filename="ghiam-pc.py"'},
    )


@router.post("/v1/pc/ticket")
def pc_ticket(request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
    _auth(authorization)
    if not _page_token():
        raise HTTPException(status_code=404, detail="Chưa có mã trang.")
    path = f"/v1/pc/package/{make_ticket(time.time(), PC_TICKET, PC_TICKET_SECONDS)}"
    return {"url": path, "link": f"{_public_base(request)}{path}"}


@router.get("/v1/pc/package/{ticket}")
def pc_package(ticket: str, request: Request) -> Response:
    if not ticket_ok(ticket, time.time(), PC_TICKET):
        raise HTTPException(status_code=404, detail="Liên kết tải đã hết hạn. Bấm tải lại.")
    token = _page_token()
    if not token:
        raise HTTPException(status_code=404, detail="Chưa có mã trang.")
    return Response(
        content=launcher_bytes(_public_base(request), token),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="ghiam-pc.bat"', "Cache-Control": "no-cache"},
    )


@router.post("/v1/pc/claim")
def pc_claim(body: PcClaimBody, authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    job = claim_pc(settings.video_db_path, body.name)
    if job is not None:
        print(f"PC nhận video {job['id']}.", flush=True)
    return {"video": job}


@router.post("/v1/pc/videos/{video_id}/heartbeat")
def pc_heartbeat(video_id: int, body: PcLeaseBody, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(authorization)
    if not touch_pc(settings.video_db_path, video_id, body.lease):
        raise HTTPException(status_code=409, detail="Video không còn dành cho PC này.")
    return {"ok": True}


@router.get("/v1/pc/videos/{video_id}/file")
def pc_file(video_id: int, lease: str = Query(default=""), authorization: str | None = Header(default=None)) -> FileResponse:
    _auth(authorization)
    path = pc_video_path(settings.video_db_path, video_id, lease, settings.video_dir)
    if path is None:
        raise HTTPException(status_code=409, detail="Video không còn dành cho PC này.")
    return FileResponse(path, media_type="video/mp4", filename=f"{video_id}.mp4")


@router.post("/v1/pc/videos/{video_id}/release")
def pc_release(video_id: int, body: PcLeaseBody, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(authorization)
    return {"ok": release_pc(settings.video_db_path, video_id, body.lease)}


@router.post("/v1/pc/videos/{video_id}/result")
def pc_result(video_id: int, body: PcResultBody, authorization: str | None = Header(default=None)) -> dict[str, object]:
    _auth(authorization)
    init_db(settings.video_db_path)
    try:
        table = table_from_payload({"rows": body.rows, "review": body.review, "unopened": body.unopened})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved = finish(settings.video_db_path, video_id, table, lease=body.lease)
    if not saved:
        raise HTTPException(status_code=409, detail="Video không còn dành cho PC này.")
    print(
        f"PC xong video {video_id}: {len(table.rows)} hàng đủ, {len(table.review)} cần xem, {len(table.unopened)} chưa mở hồ sơ.",
        flush=True,
    )
    return {"ok": True, "rows": len(table.rows), "review": len(table.review), "unopened": len(table.unopened)}
