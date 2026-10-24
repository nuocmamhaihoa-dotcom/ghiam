"""API kho khách: xem danh sách, hỏi cột Excel, rồi nạp và gộp trùng."""

from __future__ import annotations

import asyncio
import gc
import hmac
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from control_plane import db
from control_plane.column_detect import suggested_payload
from control_plane.customers import (
    FIELDS,
    LINE_FIELDS,
    MAX_LOOKUP,
    collapse,
    fold_customers,
    ingest_customers,
    lookup_by_phones,
    phones_from_text,
    public_customer,
    repair_customer_rows,
)
from control_plane.excel_import import (
    FIELD_LABELS,
    IMPORT_BATCH,
    group_preview_layouts,
    header_key,
    mapping_for_file,
    iter_mapped_file,
    mapping_from_form,
    merge_preview_columns,
    phones_from_files,
    preview_table,
    write_xlsx_to_path,
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


def _remember(summary: str, detail: str | None = None, at: str | None = None) -> None:
    try:
        db.record_action(
            settings.db_path,
            at=at or utcnow(),
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


def _safe_upload_name(name: str) -> str:
    base = Path(name).name.replace("\x00", "").strip() or "khach.xlsx"
    return base[:180]


def _keep_imported_files(uploaded: list[tuple[str, Path]], stamp: str) -> list[str]:
    """Giữ file đã nạp để lần sau còn đối chiếu, không xóa khách theo file."""
    folder = settings.imports_dir() / re.sub(r"[^0-9T]", "", stamp)[:20]
    folder.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    used: set[str] = set()
    for name, path in uploaded:
        dest = folder / _safe_upload_name(name)
        if dest.name in used or dest.exists():
            dest = folder / f"{dest.stem}-{path.name[-8:]}{dest.suffix or '.xlsx'}"
        used.add(dest.name)
        source = Path(path)
        if not source.exists():
            continue
        try:
            source.replace(dest)
        except OSError:
            try:
                shutil.copy2(source, dest)
            except OSError:
                continue
        kept.append(dest.name)
    return kept


_NAP_SUMMARY = re.compile(
    r"thêm\s+(\d+),\s+bổ sung\s+(\d+),\s+giữ\s+(\d+),\s+gộp\s+(\d+)",
    re.I,
)
_FILE_ROW = re.compile(r'\{\s*"name":\s*"([^"]+)",\s*"rows":\s*(\d+)')


def _files_from_truncated(raw: str) -> list[dict[str, Any]]:
    return [{"name": name, "rows": int(rows)} for name, rows in _FILE_ROW.findall(raw or "")]


def _parse_iso(value: str) -> datetime | None:
    text = collapse(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _import_sessions() -> dict[str, Any]:
    actions = db.list_actions(settings.db_path, limit=200, q="Nạp Excel")
    parsed: list[dict[str, Any]] = []
    for action in actions:
        if "Nạp Excel" not in str(action.get("summary") or ""):
            continue
        payload: dict[str, Any] = {}
        raw = action.get("detail") or ""
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {"files": _files_from_truncated(raw)}
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        match = _NAP_SUMMARY.search(str(action.get("summary") or ""))
        created = int(stats.get("created") or (match.group(1) if match else 0) or 0)
        updated = int(stats.get("updated") or (match.group(2) if match else 0) or 0)
        unchanged = int(stats.get("unchanged") or (match.group(3) if match else 0) or 0)
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        skipped = int(stats.get("skipped") or 0)
        file_rows = int(
            sum(int(item.get("fileRows") or item.get("rows") or 0) for item in files if isinstance(item, dict))
        )
        unmapped = int(sum(int(item.get("unmapped") or 0) for item in files if isinstance(item, dict)))
        processed = created + updated + unchanged
        incoming = int(stats.get("incoming") or 0) or file_rows or processed
        if processed > incoming:
            incoming = processed
        empty = [
            str(item.get("name") or "")
            for item in files
            if isinstance(item, dict) and not item.get("rows") and not item.get("error")
        ]
        empty = [name for name in empty if name]
        if isinstance(payload.get("emptyFiles"), list) and not empty:
            empty = [str(name) for name in payload["emptyFiles"] if name]
        ok_names = [
            str(item.get("name") or "")
            for item in files
            if isinstance(item, dict) and item.get("name") and item.get("rows")
        ]
        names = [str(item.get("name") or "") for item in files if isinstance(item, dict) and item.get("name")]
        at = str(action.get("at") or "")
        stamp = str(payload.get("importedAt") or "") or at
        moment = _parse_iso(stamp)
        kept = 0
        if created >= 1000 and moment is not None:
            if payload.get("importedAt"):
                kept = db.customers_created_between(settings.db_path, stamp, stamp)
            else:
                start = (moment - timedelta(hours=6)).isoformat()
                kept = db.customers_created_between(settings.db_path, start, at)
        parsed.append(
            {
                "id": action.get("id"),
                "at": at,
                "summary": action.get("summary") or "",
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "incoming": incoming,
                "skipped": skipped,
                "fileRows": file_rows,
                "unmapped": unmapped,
                "fileCount": int(payload.get("fileCount") or len(files) or 0),
                "okFiles": int(payload.get("okFiles") or len(ok_names)),
                "emptyFiles": empty,
                "okNames": ok_names,
                "names": names,
                "keptInWarehouse": kept,
                "missingRaw": created >= 1000 and kept < max(1, int(created * 0.1)),
                "filesKept": payload.get("keptFiles") or [],
            }
        )
    later_ok: set[str] = set()
    sessions: list[dict[str, Any]] = []
    need_reupload: list[str] = []
    for item in parsed:
        empty = [name for name in item["emptyFiles"] if name not in later_ok]
        resolved = [name for name in item["emptyFiles"] if name in later_ok]
        later_hit = bool(item["names"]) and all(name in later_ok for name in item["names"])
        missing = bool(item["missingRaw"]) and not later_hit
        if missing:
            need_reupload.extend(name for name in (item["names"] or ["file lần nạp này"]) if name not in later_ok)
        need_reupload.extend(empty)
        later_ok.update(name for name in item["okNames"] if name)
        processed = int(item["created"]) + int(item["updated"]) + int(item["unchanged"])
        incoming = int(item["incoming"])
        skipped = int(item["skipped"])
        unmapped = int(item["unmapped"])
        read_ok = incoming > 0 and (processed + skipped) >= incoming and unmapped == 0
        sessions.append(
            {
                "id": item["id"],
                "at": item["at"],
                "summary": item["summary"],
                "created": item["created"],
                "updated": item["updated"],
                "unchanged": item["unchanged"],
                "incoming": incoming,
                "skipped": skipped,
                "fileRows": item["fileRows"],
                "unmapped": unmapped,
                "fileCount": item["fileCount"],
                "okFiles": item["okFiles"],
                "emptyFiles": empty,
                "resolvedEmpty": resolved,
                "keptInWarehouse": item["keptInWarehouse"],
                "missing": missing,
                "laterOk": later_hit,
                "readOk": read_ok,
                "filesKept": item["filesKept"],
            }
        )
    seen: set[str] = set()
    unique_reupload: list[str] = []
    for name in need_reupload:
        if name in seen:
            continue
        seen.add(name)
        unique_reupload.append(name)
    warehouse = db.warehouse_stats(settings.db_path)
    return {
        "total": warehouse["total"],
        "uniquePhones": warehouse["uniquePhones"],
        "emptyPhone": warehouse["emptyPhone"],
        "duplicatePhones": warehouse["duplicatePhones"],
        "sessions": sessions,
        "needReupload": unique_reupload[:80],
        "complete": not unique_reupload,
        "rowSum": sum(int(item.get("incoming") or 0) for item in sessions),
    }


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
        rows, matched = db.search_customers(settings.db_path, q, limit=LIST_LIMIT)
        shown = [public_customer(row) for row in rows]
        return {
            "count": len(shown),
            "matched": matched,
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
    groups = group_preview_layouts(previews)
    columns = merge_preview_columns(previews)
    different = [
        item["name"]
        for item in previews[1:]
        if item["headers"] != first["headers"]
    ]
    headers = [str(column.get("header") or "") for column in columns]
    values = _column_values_from_previews(columns, previews)
    if len(groups) == 1:
        payload = dict(groups[0]["suggested"])
        detect_note = groups[0]["detectNote"]
        columns = groups[0]["columns"]
    else:
        labeled = max(groups, key=lambda item: int(item.get("labelHits") or 0))
        payload, details = suggested_payload(headers, values)
        for field in FIELDS:
            header = ""
            spec = (labeled.get("suggested") or {}).get(field)
            if spec is not None and 0 <= int(spec) < len(labeled.get("headers") or []):
                header = str(labeled["headers"][int(spec)])
            if not header:
                continue
            for index, title in enumerate(headers):
                if header_key(title) == header_key(header):
                    payload[field] = index
                    if field in LINE_FIELDS:
                        payload[f"{field}Columns"] = [index]
                    break
        detect_note = labeled.get("detectNote") or ""
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
        "groups": groups,
        "suggested": payload,
        "detectNote": detect_note,
        "fields": [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS],
        "applyNote": "Mỗi file tự khớp cột theo tiêu đề của file đó. Không giới hạn số file và dung lượng. File nạp thành công được giữ lại trên đĩa. Nạp từng dòng, ghi từng lô vào SQLite, cộng vào kho, không xóa khách cũ. Khớp tên cột trước; nếu thiếu tên hoặc SĐT thì lấy cột có tên người / số điện thoại / địa chỉ. Cột giới tính, ngày sinh, link thì bỏ qua.",
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
    if not any(_mapped(parsed.get(field) or {}) for field in FIELDS) and not any(
        _mapped((spec or {}).get(field) or {})
        for spec in (parsed.get("fileMaps") or {}).values()
        if isinstance(spec, dict)
        for field in FIELDS
    ):
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
        file_counts = {"fileRows": 0, "unmapped": 0, "phonesOnFile": 0}
        try:
            file_mapping = mapping_for_file(parsed, name, payload)
            first_width, mapped = iter_mapped_file(
                name, payload, file_mapping, first_width, counts=file_counts
            )
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
            file_reports.append(
                {
                    "name": name,
                    "rows": int(part.get("incoming") or 0),
                    "fileRows": int(file_counts.get("fileRows") or 0),
                    "phonesOnFile": int(file_counts.get("phonesOnFile") or 0),
                    "unmapped": int(file_counts.get("unmapped") or 0),
                }
            )
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
    empty_files = [item["name"] for item in file_reports if not item.get("rows") and not item.get("error")]
    kept_files = _keep_imported_files(uploaded, now)
    summary = (
        f"Nạp Excel: thêm {stats['created']}, bổ sung {stats['updated']}, "
        f"giữ {stats['unchanged']}, gộp {stats['absorbed']}."
    )
    _remember(
        summary,
        json.dumps(
            {
                "stats": stats,
                "importedAt": now,
                "fileCount": len(file_reports),
                "okFiles": sum(1 for item in file_reports if item.get("rows")),
                "emptyFiles": empty_files,
                "keptFiles": kept_files,
                "files": file_reports,
            },
            ensure_ascii=False,
        ),
        at=now,
    )
    gc.collect()
    return {
        "stats": stats,
        "files": file_reports,
        "fileErrors": problems,
        "emptyFiles": empty_files,
        "keptFiles": kept_files,
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


@router.get("/v1/customers/sessions")
def customers_sessions(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    db.init_db(settings.db_path)
    return _import_sessions()


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
    by_phone = db.customers_for_phones(settings.db_path, phones)
    stored: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rows in by_phone.values():
        for row in rows:
            customer_id = str(row.get("id") or "")
            if customer_id and customer_id in seen_ids:
                continue
            if customer_id:
                seen_ids.add(customer_id)
            stored.append(row)
    items, stats = lookup_by_phones(stored, phones)
    stats.update(parsed)
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
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Danh sách số không đọc được.") from exc
        text = str((payload or {}).get("text") or "") if isinstance(payload, dict) else ""
        return await run_in_threadpool(_lookup_uploaded, [], text, [])
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        try:
            text = str(form.get("text") or "")
        finally:
            await form.close()
        return await run_in_threadpool(_lookup_uploaded, [], text, [])
    uploaded, fields, problems = await _read_customer_form(request)
    try:
        text = fields.get("text") or ""
        if uploaded:
            async with _heavy_lock:
                return await run_in_threadpool(_lookup_uploaded, uploaded, text, problems)
        return await run_in_threadpool(_lookup_uploaded, [], text, problems)
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


def _unlink_temp(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        return


def _xlsx_file_response(dest: Path, download_name: str) -> FileResponse:
    filename = quote(download_name)
    return FileResponse(
        path=dest,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        background=BackgroundTask(_unlink_temp, str(dest)),
    )


def _new_xlsx_path(prefix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=".xlsx", delete=False)
    dest = Path(handle.name)
    handle.close()
    return dest


@router.post("/v1/customers/lookup/export")
async def customers_lookup_export(
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    _auth(authorization)
    db.init_db(settings.db_path)
    items, _stats, _reports = await _lookup_from_request(request)
    headers = ["Số tra cứu", "Có trong kho", *FIELD_LABELS.values()]

    def rows() -> Any:
        for item in items:
            customer = item.get("customer") or {}
            yield [
                item.get("query") or "",
                "Có" if item.get("found") else "Không",
                *[customer.get(field) or "" for field in FIELDS],
            ]

    dest = _new_xlsx_path("tra-cuu-")
    try:
        write_xlsx_to_path(headers, rows(), dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return _xlsx_file_response(dest, "tra-cuu-khach.xlsx")


@router.get("/v1/customers/export")
def customers_export(authorization: str | None = Header(default=None)) -> FileResponse:
    _auth(authorization)
    db.init_db(settings.db_path)
    headers = [FIELD_LABELS[field] for field in FIELDS]
    dest = _new_xlsx_path("kho-khach-")
    try:
        write_xlsx_to_path(
            headers,
            ([row.get(field) or "" for field in FIELDS] for row in db.iter_customers(settings.db_path)),
            dest,
        )
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return _xlsx_file_response(dest, "kho-khach.xlsx")
