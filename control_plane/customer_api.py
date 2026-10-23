"""API kho khách: xem danh sách, hỏi cột Excel, rồi nạp và gộp trùng."""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from control_plane import db
from control_plane.column_detect import assign_fields, explain_mapping
from control_plane.customers import (
    FIELDS,
    MAX_LOOKUP,
    collapse,
    fold_customers,
    lookup_by_phones,
    matches_query,
    phones_from_text,
    public_customer,
)
from control_plane.excel_import import (
    FIELD_LABELS,
    header_key,
    mapping_from_form,
    merge_preview_columns,
    phones_from_files,
    preview_table,
    rows_from_files,
    write_xlsx,
)
from control_plane.settings import settings

router = APIRouter()


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


async def _read_customer_form(request: Request) -> tuple[list[tuple[str, bytes]], dict[str, str], list[str]]:
    max_part = max(int(settings.max_upload_mb), 1) * 1024 * 1024
    try:
        form = await request.form(
            max_files=float("inf"),
            max_fields=float("inf"),
            max_part_size=max_part,
        )
    except MultiPartException as exc:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được file tải lên. Không giới hạn số file; hãy thử lại hoặc gửi tiếp các file còn lại.",
        ) from exc
    files: list[tuple[str, bytes]] = []
    fields: dict[str, str] = {}
    problems: list[str] = []
    try:
        for key, value in form.multi_items():
            if _is_upload(value):
                name = str(getattr(value, "filename", None) or "khach.xlsx")
                payload = await value.read()
                if not payload:
                    problems.append(f"{name} đang trống.")
                    continue
                files.append((name, payload))
                continue
            if key not in fields:
                fields[key] = str(value or "")
    finally:
        await form.close()
    return files, fields, problems


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
        return {
            "count": len(rows),
            "total": total,
            "items": rows[:LIST_LIMIT],
            "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
        }
    rows = [public_customer(row) for row in db.list_customers(settings.db_path, limit=LIST_LIMIT)]
    return {
        "count": total,
        "total": total,
        "items": rows,
        "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
    }


@router.post("/v1/customers/preview")
async def customers_preview(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    uploaded, _fields, problems = await _read_customer_form(request)
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
    for field, key in (("address", "addressColumns"), ("adminAddress", "adminAddressColumns")):
        indexes = (details.get(field) or {}).get("indexes") or []
        if indexes:
            payload[key] = indexes
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
        "applyNote": "Cách chọn cột này dùng cho mọi file trong lần nạp này. Không giới hạn số file.",
        "headerMismatch": different,
    }


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


@router.post("/v1/customers/import")
async def customers_import(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    uploaded, fields, problems = await _read_customer_form(request)
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
    incoming, file_reports = rows_from_files(uploaded, parsed)
    problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    problems = [item for item in problems if item]
    if not incoming:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được dòng khách nào. " + " ".join(problems),
        )
    stored = db.list_customers(settings.db_path)
    folded, stats, removed = fold_customers(stored, incoming, utcnow())
    db.replace_customers(settings.db_path, folded, removed)
    summary = (
        f"Nạp Excel: thêm {stats['created']}, bổ sung {stats['updated']}, "
        f"giữ {stats['unchanged']}, gộp {stats['absorbed']}."
    )
    _remember(summary, json.dumps({"files": file_reports, "stats": stats}, ensure_ascii=False))
    changed = [public_customer(row) for row in folded if row.get("write")]
    return {
        "stats": stats,
        "files": file_reports,
        "fileErrors": problems,
        "items": changed[:200],
        "total": stats["total"],
    }


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


async def _lookup_from_request(request: Request) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    uploaded, fields, problems = await _read_customer_form(request)
    file_phones: list[str] = []
    file_reports: list[dict[str, Any]] = []
    if uploaded:
        file_phones, file_reports = phones_from_files(uploaded)
        problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    items, stats = _lookup_payload(fields.get("text") or "", file_phones)
    stats["fileErrors"] = [item for item in problems if item]
    return items, stats, file_reports


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
