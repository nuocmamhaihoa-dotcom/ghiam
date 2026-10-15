"""API nạp / tra cứu / xuất kho tổng."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from control_plane import db
from control_plane.settings import derive_secret, settings
from control_plane.warehouse import (
    FIELDS,
    preview_bytes,
    records_from_bytes,
    sheet_to_dict,
)
from control_plane.warehouse_store import (
    export_csv_rows,
    init_warehouse,
    list_templates,
    open_snapshot,
    preview_matches,
    restore_snapshot,
    save_template,
    search_contacts,
    upsert_records,
    warehouse_stats,
)

router = APIRouter()
UPLOAD_TTL_SEC = 2 * 60 * 60
MAX_WAREHOUSE_MB = 200
TICKET_SECONDS = 600


def _ticket_key() -> bytes:
    return derive_secret(settings.token or "no-token", "kho-file-ticket").encode("utf-8")


def _make_ticket(now: float) -> str:
    expiry = str(int(now) + TICKET_SECONDS)
    signature = hmac.new(_ticket_key(), expiry.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{expiry}-{signature}"


def _ticket_ok(ticket: str, now: float) -> bool:
    expiry, _, signature = ticket.partition("-")
    if not expiry.isdigit() or int(expiry) < now:
        return False
    expected = hmac.new(_ticket_key(), expiry.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(signature, expected)


def _auth(authorization: str | None) -> None:
    accepted = [token for token in (settings.token, settings.page_token, os.environ.get("CONTROL_TOKEN", "")) if token]
    if not accepted:
        return
    if not authorization or not any(hmac.compare_digest(authorization, f"Bearer {token}") for token in accepted):
        raise HTTPException(status_code=401, detail="unauthorized")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upload_root() -> Path:
    path = settings.data_dir / "warehouse_uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup(now: float) -> None:
    root = _upload_root()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        stamp = child / "meta.json"
        if not stamp.is_file():
            shutil.rmtree(child, ignore_errors=True)
            continue
        try:
            meta = json.loads(stamp.read_text(encoding="utf-8"))
            saved = float(meta.get("savedAt") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            shutil.rmtree(child, ignore_errors=True)
            continue
        if now - saved > UPLOAD_TTL_SEC:
            shutil.rmtree(child, ignore_errors=True)


def _save_upload(filename: str, payload: bytes) -> str:
    _cleanup(time.time())
    token = secrets.token_hex(16)
    folder = _upload_root() / token
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "file.bin").write_bytes(payload)
    (folder / "meta.json").write_text(
        json.dumps({"filename": filename, "savedAt": time.time(), "bytes": len(payload)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return token


def _load_upload(token: str) -> tuple[str, bytes]:
    folder = _upload_root() / token
    meta_path = folder / "meta.json"
    file_path = folder / "file.bin"
    if not meta_path.is_file() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File nạp tạm đã hết hạn. Hãy chọn lại file.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return str(meta.get("filename") or "file.xlsx"), file_path.read_bytes()


def _safe_mapping(raw: dict[str, Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for key, value in raw.items():
        if key not in FIELDS:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value < 0 or value > 200:
            continue
        mapping[key] = value
    return mapping


class CommitBody(BaseModel):
    token: str
    sheet: str | None = None
    mapping: dict[str, int] = Field(default_factory=dict)
    source: str = ""
    fingerprint: str = ""
    headers: list[str] = Field(default_factory=list)
    headerRow: int = 0
    hasHeader: bool | None = None


@router.get("/v1/kho/stats")
def kho_stats(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    init_warehouse(settings.db_path)
    return warehouse_stats(settings.db_path)


@router.get("/v1/kho")
def kho_search(
    authorization: str | None = Header(default=None),
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    after: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _auth(authorization)
    init_warehouse(settings.db_path)
    return search_contacts(settings.db_path, q, limit=limit, after=after)


@router.get("/v1/kho/export")
def kho_export(
    authorization: str | None = Header(default=None),
    q: str = Query(default=""),
) -> StreamingResponse:
    _auth(authorization)
    init_warehouse(settings.db_path)
    return StreamingResponse(
        export_csv_rows(settings.db_path, q),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kho-tong.csv"'},
    )


@router.post("/v1/kho/preview")
async def kho_preview(
    authorization: str | None = Header(default=None),
    file: UploadFile = File(...),
    sheet: str | None = Query(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    init_warehouse(settings.db_path)
    filename = Path(file.filename or "file.xlsx").name
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="File trống")
    if len(payload) > MAX_WAREHOUSE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File lớn hơn {MAX_WAREHOUSE_MB}MB")
    try:
        preview = preview_bytes(payload, filename, list_templates(settings.db_path), sheet)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    token = _save_upload(filename, payload)
    chosen = next((item for item in preview.sheets if item.name == preview.chosen), preview.sheets[0])
    body = sheet_to_dict(chosen)
    body["sample"] = preview_matches(settings.db_path, chosen.sample)
    return {
        "token": token,
        "filename": filename,
        "chosen": preview.chosen,
        "sheet": body,
        "sheets": [sheet_to_dict(item) for item in preview.sheets],
    }


@router.post("/v1/kho/commit")
def kho_commit(
    body: CommitBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    init_warehouse(settings.db_path)
    filename, payload = _load_upload(body.token)
    mapping = _safe_mapping(body.mapping)
    records = records_from_bytes(
        payload,
        filename,
        body.sheet,
        mapping,
        header_row=body.headerRow,
        has_header=body.hasHeader,
    )
    source = (body.source or filename).strip()[:80]
    result = upsert_records(
        settings.db_path,
        records,
        filename=filename,
        source=source,
        mapping=mapping,
        at=_now(),
    )
    save_template(settings.db_path, body.fingerprint, body.headers, mapping, _now())
    try:
        db.record_action(
            settings.db_path,
            at=_now(),
            actor="me",
            source="hub",
            kind="note",
            summary=f"Nạp {filename}: +{result['inserted']} mới, {result['updated']} ghép, {result['skipped']} bỏ",
            detail=json.dumps({"mapping": mapping, "rows": len(records)}, ensure_ascii=False),
        )
    except Exception:
        pass
    return {**result, "read": len(records), "filename": filename}


@router.post("/v1/kho/file-ticket")
def kho_file_ticket(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _auth(authorization)
    init_warehouse(settings.db_path)
    return {"url": f"/v1/kho/file/{_make_ticket(time.time())}"}


@router.get("/v1/kho/file/{ticket}")
def kho_file(ticket: str) -> StreamingResponse:
    if not _ticket_ok(ticket, time.time()):
        raise HTTPException(status_code=404, detail="Liên kết tải đã hết hạn. Bấm tải lại.")
    init_warehouse(settings.db_path)
    return StreamingResponse(
        open_snapshot(settings.db_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="kho-tong.db"'},
    )


@router.post("/v1/kho/restore")
async def kho_restore(
    request: Request,
    authorization: str | None = Header(default=None),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    _auth(authorization)
    init_warehouse(settings.db_path)
    declared = request.headers.get("content-length")
    free = shutil.disk_usage(settings.db_path.parent).free
    if declared and declared.isdigit() and int(declared) > free - 64 * 1024 * 1024:
        raise HTTPException(
            status_code=507,
            detail="Đĩa không còn chỗ để nhận bản lưu. Hãy giải phóng bớt rồi nạp lại.",
        )
    incoming = settings.db_path.with_name(settings.db_path.name + ".incoming")
    written = 0
    try:
        with incoming.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > free - 64 * 1024 * 1024:
                    raise HTTPException(
                        status_code=507,
                        detail="Đĩa không còn chỗ để nhận bản lưu. Hãy giải phóng bớt rồi nạp lại.",
                    )
                handle.write(chunk)
        if written < 100:
            raise HTTPException(status_code=400, detail="File không phải bản lưu kho.")
        return await run_in_threadpool(restore_snapshot, settings.db_path, incoming)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if incoming.exists():
            incoming.unlink(missing_ok=True)
