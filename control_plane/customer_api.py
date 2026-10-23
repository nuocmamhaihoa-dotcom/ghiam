"""API kho khách: xem danh sách, hỏi cột Excel, rồi nạp và gộp trùng."""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

from control_plane import db
from control_plane.customers import FIELDS, fold_customers, matches_query, public_customer
from control_plane.excel_import import (
    FIELD_LABELS,
    mapping_from_form,
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


def _mapped(spec: dict[str, Any]) -> bool:
    if str(spec.get("header") or "").strip():
        return True
    return spec.get("index") not in (None, "")


async def _files_from_upload(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not files:
        raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một file Excel.")
    collected: list[tuple[str, bytes]] = []
    for item in files:
        name = item.filename or "khach.xlsx"
        payload = await item.read()
        if not payload:
            raise HTTPException(status_code=400, detail=f"{name} đang trống.")
        collected.append((name, payload))
    return collected


@router.get("/v1/customers")
def customers_list(
    q: str = Query(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    rows = [public_customer(row) for row in db.list_customers(settings.db_path)]
    filtered = [row for row in rows if matches_query(row, q)]
    return {
        "count": len(filtered),
        "total": len(rows),
        "items": filtered[:500],
        "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
    }


@router.post("/v1/customers/preview")
async def customers_preview(
    files: list[UploadFile] = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    uploaded = await _files_from_upload(files)
    previews: list[dict[str, Any]] = []
    for name, payload in uploaded:
        try:
            previews.append(preview_table(name, payload))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    first = previews[0]
    different = [
        item["name"]
        for item in previews[1:]
        if item["headers"] != first["headers"]
    ]
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
        "columns": first["columns"],
        "suggested": first["suggested"],
        "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
        "applyNote": "Cách chọn cột này dùng cho mọi file trong lần nạp này.",
        "headerMismatch": different,
    }


@router.post("/v1/customers/import")
async def customers_import(
    files: list[UploadFile] = File(...),
    mapping: str = Form(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    try:
        parsed = mapping_from_form(json.loads(mapping))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Bảng chọn cột không đọc được.") from exc
    if not any(_mapped(spec) for spec in parsed.values()):
        raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một cột dữ liệu.")
    uploaded = await _files_from_upload(files)
    try:
        incoming, file_reports = rows_from_files(uploaded, parsed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored = db.list_customers(settings.db_path)
    folded, stats, removed = fold_customers(stored, incoming, utcnow())
    db.replace_customers(settings.db_path, folded, removed)
    summary = (
        f"Nạp Excel: thêm {stats['created']}, bổ sung {stats['updated']}, "
        f"giữ {stats['unchanged']}, gộp {stats['absorbed']}."
    )
    _remember(summary, json.dumps({"files": file_reports, "stats": stats}, ensure_ascii=False))
    return {
        "stats": stats,
        "files": file_reports,
        "items": [public_customer(row) for row in folded[:200]],
        "total": stats["total"],
    }


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
