"""API kho khách: xem danh sách, hỏi cột Excel, rồi nạp và gộp trùng."""

from __future__ import annotations

import asyncio
import hmac
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from control_plane import db
from control_plane.column_detect import assign_fields, explain_mapping
from control_plane.customers import (
    FIELDS,
    LINE_FIELDS,
    MAX_LOOKUP,
    collapse,
    fold_customers,
    ingest_customers,
    lookup_by_phones,
    matches_query,
    phones_from_text,
    public_customer,
    repair_customer_rows,
)
from control_plane.excel_import import (
    FIELD_LABELS,
    IMPORT_BATCH,
    header_key,
    iter_mapped_file,
    mapping_from_form,
    merge_preview_columns,
    phones_from_files,
    preview_table,
    write_xlsx,
)
from control_plane.settings import settings

router = APIRouter()
_heavy_lock = asyncio.Lock()


def _bearer_matches(authorization: str, token: str) -> bool:
    expected = f"Bearer {token}"
    if len(authorization) != len(expected):
        return False
    return hmac.compare_digest(authorization, expected)


def _auth(authorization: str | None) -> None:
    accepted = [token for token in (settings.token, settings.page_token) if token]
    if not accepted:
        return
    if not authorization or not any(_bearer_matches(authorization, token) for token in accepted):
        raise HTTPException(status_code=401, detail="unauthorized")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remember(summary: str, detail: str | None = None) -> None:
    try:
        db.record_action(
            settings.db_path,
            at=utcnow(),
            actor="me",
            source="hub",
            kind="note",
            summary=summary,
            detail=detail,
        )
    except Exception:
        return


LIST_LIMIT = 2000


def _mapped(spec: dict[str, Any]) -> bool:
    if str(spec.get("header") or "").strip():
        return True
    return spec.get("index") not in (None, "")


def _is_upload(value: object) -> bool:
    return isinstance(value, UploadFile) or (
        hasattr(value, "filename") and hasattr(value, "read")
    )


UPLOAD_CHUNK = 1024 * 1024


def _cleanup_uploads(files: list[tuple[str, Path]]) -> None:
    for _name, path in files:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            continue


async def _spool_upload(value: Any) -> Path:
    settings.ensure_dirs()
    handle = tempfile.NamedTemporaryFile(
        prefix="khach-up-",
        suffix=".bin",
        dir=settings.uploads_dir(),
        delete=False,
    )
    path = Path(handle.name)
    try:
        while True:
            chunk = await value.read(UPLOAD_CHUNK)
            if not chunk:
                break
            handle.write(chunk)
        handle.flush()
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    handle.close()
    return path


async def _read_customer_form(request: Request) -> tuple[list[tuple[str, Path]], dict[str, str], list[str]]:
    max_part = settings.upload_limit_bytes()
    settings.ensure_dirs()
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(settings.tmp_dir())
    try:
        try:
            form = await request.form(
                max_files=float("inf"),
                max_fields=float("inf"),
                max_part_size=max_part if max_part is not None else sys.maxsize,
            )
        except MultiPartException as exc:
            raise HTTPException(
                status_code=400,
                detail="Không đọc được file tải lên. Không giới hạn số file và dung lượng; hãy thử lại hoặc gửi tiếp các file còn lại.",
            ) from exc
        files: list[tuple[str, Path]] = []
        fields: dict[str, str] = {}
        problems: list[str] = []
        try:
            for key, value in form.multi_items():
                if _is_upload(value):
                    name = str(getattr(value, "filename", None) or "khach.xlsx")
                    path = await _spool_upload(value)
                    if path.stat().st_size == 0:
                        path.unlink(missing_ok=True)
                        problems.append(f"{name} đang trống.")
                        continue
                    files.append((name, path))
                    continue
                if key not in fields:
                    fields[key] = str(value or "")
        except Exception:
            _cleanup_uploads(files)
            raise
        finally:
            await form.close()
        return files, fields, problems
    finally:
        tempfile.tempdir = previous_tempdir


def _blank_import_stats() -> dict[str, int]:
    return {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "absorbed": 0,
        "incoming": 0,
        "kept": 0,
        "total": 0,
        "removed": 0,
    }


def _add_import_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for key in ("created", "updated", "unchanged", "skipped", "absorbed", "incoming", "kept", "removed"):
        total[key] += int(part.get(key) or 0)
    total["total"] = int(part.get("total") or total["total"])


def _fold_import_batch(
    stored: list[dict[str, Any]],
    incoming: list[dict[str, str]],
    now: str,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    if not incoming:
        stats = _blank_import_stats()
        stats["total"] = len(stored)
        return stored, stats, []
    repair_customer_rows(incoming)
    folded, stats, removed = fold_customers(stored, incoming, now)
    db.replace_customers(settings.db_path, folded, removed)
    return folded, stats, removed


def _collect_changed(changed: list[dict[str, str]], folded: list[dict[str, Any]]) -> None:
    if len(changed) >= 200:
        return
    for row in folded:
        if not row.get("write"):
            continue
        changed.append(public_customer(row))
        if len(changed) >= 200:
            return


@router.get("/v1/customers")
def customers_list(
    q: str = Query(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    total = db.customer_count(settings.db_path)
    if collapse(q):
        rows = [public_customer(row) for row in db.list_customers(settings.db_path) if matches_query(row, q)]
        shown = rows[:LIST_LIMIT]
        return {
            "count": len(shown),
            "matched": len(rows),
            "total": total,
            "items": shown,
            "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
        }
    rows = [public_customer(row) for row in db.list_customers(settings.db_path, limit=LIST_LIMIT)]
    return {
        "count": len(rows),
        "matched": total,
        "total": total,
        "items": rows,
        "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
    }


def _preview_uploaded(
    uploaded: list[tuple[str, Path]],
    problems: list[str],
) -> dict[str, Any]:
    if not uploaded:
        raise HTTPException(
            status_code=400,
            detail="Hãy chọn ít nhất một file Excel." + ((" " + " ".join(problems)) if problems else ""),
        )
    previews: list[dict[str, Any]] = []
    for name, payload in uploaded:
        try:
            previews.append(preview_table(name, payload))
        except ValueError as exc:
            problems.append(f"{name}: {exc}")
    if not previews:
        raise HTTPException(status_code=400, detail="Không đọc được file nào. " + " ".join(problems))
    first = previews[0]
    columns = merge_preview_columns(previews)
    different = [
        item["name"]
        for item in previews[1:]
        if item["headers"] != first["headers"]
    ]
    headers = [str(column.get("header") or "") for column in columns]
    values = _column_values_from_previews(columns, previews)
    suggested, details = assign_fields(headers, values)
    payload = dict(suggested)
    for field in LINE_FIELDS:
        indexes = (details.get(field) or {}).get("indexes") or []
        if indexes:
            payload[f"{field}Columns"] = indexes
    return {
        "files": [
            {
                "name": item["name"],
                "columns": item["columns"],
                "samples": item["samples"],
                "rowCount": item["rowCount"],
                "truncated": item["truncated"],
            }
            for item in previews
        ],
        "fileErrors": problems,
        "columns": columns,
        "suggested": payload,
        "detectNote": explain_mapping(details, headers),
        "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
        "applyNote": "Cách chọn cột này dùng cho mọi file trong lần nạp này. Không giới hạn số file và dung lượng. File nằm trên đĩa khi đọc. Nạp từng dòng, ghi từng lô vào SQLite, không tải cả kho khách vào bộ nhớ. Khớp tên cột trước; nếu thiếu tên hoặc SĐT thì lấy cột có tên người / số điện thoại / địa chỉ. Cột giới tính, ngày sinh, link thì bỏ qua.",
        "headerMismatch": different,
    }


@router.post("/v1/customers/preview")
async def customers_preview(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    uploaded, _fields, problems = await _read_customer_form(request)
    try:
        async with _heavy_lock:
            return await run_in_threadpool(_preview_uploaded, uploaded, problems)
    finally:
        _cleanup_uploads(uploaded)


def _column_values_from_previews(
    columns: list[dict[str, Any]],
    previews: list[dict[str, Any]],
) -> list[list[str]]:
    buckets: list[list[str]] = [[] for _ in columns]
    by_header = {header_key(str(column.get("header") or "")): index for index, column in enumerate(columns)}
    for preview in previews:
        headers = [str(item) for item in (preview.get("headers") or [])]
        rows = preview.get("detectRows") or preview.get("samples") or []
        for row in rows:
            for source, header in enumerate(headers):
                dest = by_header.get(header_key(header))
                if dest is None or source >= len(row):
                    continue
                cell = row[source]
                if collapse(cell):
                    buckets[dest].append(cell)
    return buckets


def _import_uploaded(
    uploaded: list[tuple[str, Path]],
    fields: dict[str, str],
    problems: list[str],
) -> dict[str, Any]:
    try:
        parsed = mapping_from_form(json.loads(fields.get("mapping") or ""))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Bảng chọn cột không đọc được.") from exc
    if not any(_mapped(spec) for spec in parsed.values()):
        raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một cột dữ liệu.")
    if not uploaded:
        raise HTTPException(
            status_code=400,
            detail="Hãy chọn ít nhất một file Excel." + ((" " + " ".join(problems)) if problems else ""),
        )
    stats = _blank_import_stats()
    changed: list[dict[str, str]] = []
    file_reports: list[dict[str, Any]] = []
    first_width: int | None = None
    now = utcnow()
    incoming_total = 0
    for name, payload in uploaded:
        mapped: Any = None
        try:
            first_width, mapped = iter_mapped_file(name, payload, parsed, first_width)
            part, preview = ingest_customers(
                settings.db_path,
                mapped,
                now,
                flush_every=IMPORT_BATCH,
            )
            _add_import_stats(stats, part)
            for row in preview:
                if len(changed) >= 200:
                    break
                changed.append(public_customer(row))
            incoming_total += int(part.get("incoming") or 0)
            file_reports.append({"name": name, "rows": int(part.get("incoming") or 0)})
        except ValueError as exc:
            file_reports.append({"name": name, "rows": 0, "error": str(exc)})
        finally:
            closer = getattr(mapped, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
    problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    problems = [item for item in problems if item]
    if incoming_total == 0:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được dòng khách nào. " + " ".join(problems),
        )
    stats["total"] = db.customer_count(settings.db_path)
    summary = (
        f"Nạp Excel: thêm {stats['created']}, bổ sung {stats['updated']}, "
        f"giữ {stats['unchanged']}, gộp {stats['absorbed']}."
    )
    _remember(summary, json.dumps({"files": file_reports, "stats": stats}, ensure_ascii=False))
    return {
        "stats": stats,
        "files": file_reports,
        "fileErrors": problems,
        "items": changed[:200],
        "total": stats["total"],
    }


@router.post("/v1/customers/import")
async def customers_import(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    uploaded, fields, problems = await _read_customer_form(request)
    try:
        async with _heavy_lock:
            return await run_in_threadpool(_import_uploaded, uploaded, fields, problems)
    finally:
        _cleanup_uploads(uploaded)


def _unique_phones(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    phones: list[str] = []
    for group in groups:
        for phone in group:
            if phone in seen or len(phones) >= MAX_LOOKUP:
                continue
            seen.add(phone)
            phones.append(phone)
    return phones


def _lookup_payload(text: str, file_phones: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pasted, parsed = phones_from_text(text)
    phones = _unique_phones(pasted, file_phones)
    if not phones:
        raise HTTPException(status_code=400, detail="Không thấy số điện thoại nào trong danh sách.")
    stored = db.list_customers(settings.db_path)
    items, stats = lookup_by_phones(stored, phones)
    stats.update(parsed)
    stats["total"] = len(stored)
    return items, stats


def _lookup_uploaded(
    uploaded: list[tuple[str, Path]],
    text: str,
    problems: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    file_phones: list[str] = []
    file_reports: list[dict[str, Any]] = []
    if uploaded:
        file_phones, file_reports = phones_from_files(uploaded)
        problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    items, stats = _lookup_payload(text, file_phones)
    stats["fileErrors"] = [item for item in problems if item]
    return items, stats, file_reports


async def _lookup_from_request(request: Request) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    uploaded, fields, problems = await _read_customer_form(request)
    try:
        async with _heavy_lock:
            return await run_in_threadpool(_lookup_uploaded, uploaded, fields.get("text") or "", problems)
    finally:
        _cleanup_uploads(uploaded)


@router.post("/v1/customers/lookup")
async def customers_lookup(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    items, stats, file_reports = await _lookup_from_request(request)
    return {"stats": stats, "files": file_reports, "fileErrors": stats.get("fileErrors") or [], "items": items}


@router.post("/v1/customers/lookup/export")
async def customers_lookup_export(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    _auth(authorization)
    db.init_db(settings.db_path)
    items, _stats, _reports = await _lookup_from_request(request)
    headers = ["Số tra cứu", "Có trong kho", *FIELD_LABELS.values()]
    body: list[list[str]] = []
    for item in items:
        customer = item.get("customer") or {}
        body.append(
            [
                item.get("query") or "",
                "Có" if item.get("found") else "Không",
                *[customer.get(field) or "" for field in FIELDS],
            ]
        )
    payload = write_xlsx(headers, body)
    filename = quote("tra-cuu-khach.xlsx")
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/v1/customers/export")
def customers_export(authorization: str | None = Header(default=None)) -> Response:
    _auth(authorization)
    db.init_db(settings.db_path)
    rows = db.list_customers(settings.db_path)
    headers = [FIELD_LABELS[field] for field in FIELDS]
    body = [[row.get(field) or "" for field in FIELDS] for row in rows]
    payload = write_xlsx(headers, body)
    filename = quote("kho-khach.xlsx")
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
