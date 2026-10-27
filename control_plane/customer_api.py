"""API kho khách: xem danh sách, hỏi cột Excel, rồi nạp và gộp trùng."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from control_plane import accounts, db, khach_jobs, khach_uploads
from control_plane.accounts_api import _actor, _manager
from control_plane.column_detect import suggested_payload
from control_plane.customers import (
    FIELDS,
    LINE_FIELDS,
    MAX_LOOKUP,
    collapse,
    fold_customers,
    ingest_customers,
    iter_lookup_groups,
    lookup_phone_groups,
    phone_groups_from_text,
    public_customer,
    real_lookup_phones,
    repair_customer_rows,
)
from control_plane.excel_import import (
    FIELD_LABELS,
    IMPORT_BATCH,
    chosen_mapping_for_file,
    group_preview_layouts,
    header_key,
    mapping_for_file,
    iter_mapped_file,
    mapping_from_form,
    merge_preview_columns,
    phone_groups_from_files,
    preview_table,
    write_xlsx_to_path,
)
from control_plane.settings import settings

router = APIRouter()
_heavy_lock = asyncio.Lock()
_LOG = logging.getLogger(__name__)


def _charge(actor: accounts.Actor, phones: list[str]) -> dict[str, Any]:
    try:
        return accounts.consume_lookup(settings.db_path, actor, phones, utcnow())
    except accounts.LookupQuotaError as exc:
        raise HTTPException(status_code=429, detail=exc.detail) from exc


def _visible_job(actor: accounts.Actor, job: dict[str, Any] | None) -> dict[str, Any]:
    if job is None:
        raise HTTPException(status_code=404, detail="Không thấy việc này, có thể đã quá hạn. Hãy chạy lại.")
    if actor.is_manager:
        return job
    owner = str((job.get("info") or {}).get("accountId") or "")
    if job.get("kind") == "lookup" and owner == actor.id:
        return job
    raise HTTPException(status_code=403, detail="Không xem được việc này.")


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
LOOKUP_RETURN_LIMIT = 2000


def _mapped(spec: dict[str, Any]) -> bool:
    if str(spec.get("header") or "").strip():
        return True
    return spec.get("index") not in (None, "")


def _is_upload(value: object) -> bool:
    return isinstance(value, UploadFile) or (
        hasattr(value, "filename") and hasattr(value, "read")
    )


UPLOAD_CHUNK = 4 * 1024 * 1024


def _cleanup_uploads(files: list[tuple[str, Path]]) -> None:
    for _name, path in files:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            continue


def _safe_upload_name(name: str) -> str:
    base = Path(name).name.replace("\x00", "").strip() or "khach.xlsx"
    return base[:180]


def _keep_imported_files(uploaded: list[tuple[str, Path]], stamp: str, link: bool = False) -> list[str]:
    """Giữ file đã nạp để lần sau còn đối chiếu, không xóa khách theo file.

    link=True: file gửi theo mảnh vẫn để lại chỗ cũ (dùng lại được), bản giữ là hard link.
    """
    folder = settings.imports_dir() / re.sub(r"[^0-9T]", "", stamp)[:20]
    folder.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    used: set[str] = set()
    for index, (name, path) in enumerate(uploaded):
        dest = folder / _safe_upload_name(name)
        if dest.name in used or dest.exists():
            dest = folder / f"{dest.stem}-{index + 1}{dest.suffix or '.xlsx'}"
        used.add(dest.name)
        source = Path(path)
        if not source.exists():
            continue
        if link:
            if not khach_uploads.link_or_copy(source, dest):
                continue
        else:
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


def _warehouse_is_huge() -> bool:
    """Kho hàng chục triệu dòng thì không đếm lại từng lần nạp khi mở trang."""
    saved = db.cached_customer_total(settings.db_path)
    if saved is not None:
        return saved >= 100_000
    try:
        return settings.db_path.stat().st_size >= 500_000_000
    except OSError:
        return False


def _listed_customer_total() -> int:
    stats = db.page_warehouse_stats(settings.db_path)
    return int(stats["total"] or 0)


def _remembered_total() -> int:
    saved = db.cached_customer_total(settings.db_path)
    if saved is not None:
        return saved
    total = db.customer_count(settings.db_path)
    db.set_customer_total(settings.db_path, total)
    return total


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
        recount = created >= 1000 and moment is not None and not _warehouse_is_huge()
        kept = 0
        if recount:
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
                "missingRaw": recount and kept < max(1, int(created * 0.1)),
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
    warehouse = db.page_warehouse_stats(settings.db_path)
    row_sum = sum(int(item.get("incoming") or 0) for item in sessions)
    unique_phones = int(warehouse["uniquePhones"] or 0)
    return {
        "total": warehouse["total"],
        "uniquePhones": unique_phones,
        "emptyPhone": warehouse["emptyPhone"],
        "duplicatePhones": warehouse["duplicatePhones"],
        "sessions": sessions,
        "needReupload": unique_reupload,
        "needCount": len(unique_reupload),
        "complete": not unique_reupload,
        "rowSum": row_sum,
        "repeatRows": max(0, row_sum - unique_phones),
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


def _filled_fields(raw: str) -> list[str]:
    wanted: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        field = part.strip()
        if not field or field in seen:
            continue
        if field not in FIELDS:
            raise HTTPException(status_code=400, detail="Mục lọc không có trong kho.")
        seen.add(field)
        wanted.append(field)
    return wanted


@router.get("/v1/customers")
def customers_list(
    request: Request,
    q: str = Query(default=""),
    has: str = Query(default=""),
    before: int = Query(default=0, ge=0),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=LIST_LIMIT, ge=1, le=LIST_LIMIT),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _manager(_actor(request))
    db.init_db(settings.db_path)
    total = _listed_customer_total()
    filled = _filled_fields(has)
    fields = [{"id": field, "label": FIELD_LABELS[field]} for field in FIELDS]
    if collapse(q) and not filled and before <= 0 and after <= 0:
        rows, matched = db.search_customers(settings.db_path, q, limit=limit)
        shown = [public_customer(row) for row in rows]
        return {
            "count": len(shown),
            "matched": matched,
            "total": total,
            "items": shown,
            "hasMore": matched > len(shown),
            "nextBefore": None,
            "nextAfter": None,
            "fields": fields,
        }
    rows, next_before, next_after, has_more = db.browse_customers(
        settings.db_path,
        limit=limit,
        before=before or None,
        after=after or None,
        filled=filled,
        query=q,
    )
    shown = [public_customer(row) for row in rows]
    return {
        "count": len(shown),
        "matched": None if filled or before or after else total,
        "total": total,
        "items": shown,
        "hasMore": has_more,
        "nextBefore": next_before,
        "nextAfter": next_after,
        "fields": fields,
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
    count_rows = len(uploaded) <= 40
    previews: list[dict[str, Any]] = []
    for name, payload in uploaded:
        try:
            previews.append(preview_table(name, payload, count_rows=count_rows))
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
        "applyNote": (
            "Mỗi file tự khớp cột theo tiêu đề của file đó. Không giới hạn số file và dung lượng. "
            "File nạp thành công được giữ lại trên đĩa. Nạp từng dòng, ghi từng lô vào SQLite, cộng vào kho, không xóa khách cũ. "
            "Khớp tên cột trước; nếu thiếu tên hoặc SĐT thì lấy cột có tên người / số điện thoại / địa chỉ. "
            "Cột giới tính, ngày sinh, link thì bỏ qua."
            + (
                " Nhiều file nên chưa đếm hết số dòng trước khi nạp. Bấm nạp vẫn đọc từng file."
                if not count_rows
                else ""
            )
        ),
        "headerMismatch": different,
    }


@router.post("/v1/customers/preview")
async def customers_preview(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _manager(_actor(request))
    if _is_json(request):
        payload = await _json_object(request)
        uploaded = _resolve_uploads(payload.get("uploads"))
        return await run_in_threadpool(_preview_uploaded, uploaded, [])
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


def _parse_mapping(mapping_text: str, *, allow_empty: bool = False) -> dict[str, Any]:
    try:
        parsed = mapping_from_form(json.loads(mapping_text or "{}"))
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Bảng chọn cột không đọc được.") from exc
    has_field = any(_mapped(parsed.get(field) or {}) for field in FIELDS) or any(
        _mapped((spec or {}).get(field) or {})
        for spec in (parsed.get("fileMaps") or {}).values()
        if isinstance(spec, dict)
        for field in FIELDS
    )
    if not has_field and not allow_empty:
        raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một cột dữ liệu.")
    return parsed


def _import_uploaded(
    uploaded: list[tuple[str, Path]],
    fields: dict[str, str],
    problems: list[str],
) -> dict[str, Any]:
    return _import_files(uploaded, fields.get("mapping") or "", problems)


def _import_files(
    uploaded: list[tuple[str, Path]],
    mapping_text: str,
    problems: list[str],
    progress: Any = None,
    link: bool = False,
    start_index: int = 0,
) -> dict[str, Any]:
    parsed = _parse_mapping(mapping_text, allow_empty=True)
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
    start_index = max(0, int(start_index or 0))
    if start_index >= len(uploaded):
        stats["total"] = _remembered_total()
        return {
            "stats": stats,
            "files": [],
            "fileErrors": [],
            "emptyFiles": [],
            "keptFiles": [],
            "items": [],
            "total": stats["total"],
            "alreadyDone": True,
        }
    for file_index, (name, payload) in enumerate(uploaded):
        if file_index < start_index:
            continue
        mapped: Any = None
        file_counts = {"fileRows": 0, "unmapped": 0, "phonesOnFile": 0}
        before = dict(stats)

        def report(part: dict[str, int], name: str = name, file_index: int = file_index, before: dict = before) -> None:
            if progress is None:
                return
            progress(
                {
                    "phase": "import",
                    "file": name,
                    "fileIndex": file_index,
                    "fileCount": len(uploaded),
                    **{key: int(before.get(key) or 0) + int(part.get(key) or 0) for key in part},
                }
            )

        try:
            if progress is not None:
                progress({"phase": "map", "file": name, "fileIndex": file_index, "fileCount": len(uploaded)})
            file_mapping = mapping_for_file(parsed, name, payload)
            first_width, mapped = iter_mapped_file(
                name,
                payload,
                file_mapping,
                first_width,
                counts=file_counts,
                chosen=chosen_mapping_for_file(parsed, name),
            )
            part, preview = ingest_customers(
                settings.db_path,
                mapped,
                now,
                flush_every=IMPORT_BATCH,
                cleaned=True,
                progress=report,
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
        if progress is not None:
            progress(
                {
                    "phase": "import",
                    "file": name,
                    "fileIndex": file_index,
                    "fileCount": len(uploaded),
                    "completedFiles": file_index + 1,
                }
            )
        _release_memory()
    problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    problems = [item for item in problems if item]
    if incoming_total == 0:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được dòng khách nào. " + " ".join(problems),
        )
    stats["total"] = _remembered_total()
    empty_files = [item["name"] for item in file_reports if not item.get("rows") and not item.get("error")]
    kept_files = _keep_imported_files(uploaded, now, link=link)
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
                "emptyFiles": empty_files[:200],
                "keptFiles": kept_files[:200],
                "files": file_reports[:200],
            },
            ensure_ascii=False,
        ),
        at=now,
    )
    _release_memory()
    return {
        "stats": stats,
        "files": file_reports,
        "fileErrors": problems,
        "emptyFiles": empty_files,
        "keptFiles": kept_files,
        "items": changed[:200],
        "total": stats["total"],
    }


def _is_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("content-type") or "").lower()


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Dữ liệu gửi lên không đọc được.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Dữ liệu gửi lên không đọc được.")
    return payload


def _upload_error(exc: khach_uploads.UploadError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status)


def _resolve_uploads(raw: Any) -> list[tuple[str, Path]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="Danh sách file không đọc được.")
    try:
        return khach_uploads.resolve(raw)
    except khach_uploads.UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _release_memory() -> None:
    """Trả bộ nhớ về hệ thống sau mỗi file, để lần nạp dài không bị giết vì RSS chỉ tăng."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        return


def _import_job(
    context: khach_jobs.JobContext,
    uploaded: list[tuple[str, Path]],
    mapping_text: str,
    start_index: int = 0,
) -> dict[str, Any]:
    start = max(0, int(start_index or 0))
    context.report(
        {
            "phase": "waiting",
            "fileIndex": start,
            "completedFiles": start,
            "fileCount": len(uploaded),
            "file": uploaded[start][0] if uploaded and start < len(uploaded) else "",
        },
        force=True,
    )

    def report(progress: dict[str, Any]) -> None:
        context.report(progress, force="completedFiles" in progress)

    with khach_jobs.import_lock(context):
        return _import_files(
            uploaded,
            mapping_text,
            [],
            progress=report,
            link=True,
            start_index=start,
        )


@router.post("/v1/customers/uploads")
async def customers_upload_start(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Any:
    _actor(request)
    payload = await _json_object(request)
    try:
        size = int(payload.get("size") or 0)
        modified = int(payload.get("lastModified") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Dung lượng file không đọc được.") from exc
    try:
        return khach_uploads.create_upload(str(payload.get("name") or ""), size, modified)
    except khach_uploads.UploadError as exc:
        return _upload_error(exc)


@router.get("/v1/customers/uploads/{upload_id}")
def customers_upload_status(
    upload_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Any:
    _actor(request)
    try:
        return khach_uploads.status(upload_id)
    except khach_uploads.UploadError as exc:
        return _upload_error(exc)


@router.put("/v1/customers/uploads/{upload_id}")
async def customers_upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None),
) -> Any:
    _actor(request)
    try:
        return await khach_uploads.append_chunk(upload_id, offset, request.stream())
    except khach_uploads.UploadError as exc:
        return _upload_error(exc)


@router.get("/v1/customers/jobs/{job_id}")
def customers_job(job_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    actor = _actor(request)
    return {"job": _visible_job(actor, khach_jobs.get_job(job_id))}


@router.post("/v1/customers/import")
async def customers_import(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _manager(_actor(request))
    db.init_db(settings.db_path)
    if _is_json(request):
        payload = await _json_object(request)
        mapping = payload.get("mapping")
        mapping_text = mapping if isinstance(mapping, str) else json.dumps(mapping or {}, ensure_ascii=False)
        _parse_mapping(mapping_text)
        uploaded = _resolve_uploads(payload.get("uploads"))
        if not uploaded:
            raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một file Excel.")
        info = {
            "files": [name for name, _path in uploaded],
            "rows": int(payload.get("expectedRows") or 0),
            "uploads": [str(item) for item in payload.get("uploads") or [] if str(item or "")],
            "mapping": mapping_text,
        }
        job = khach_jobs.start_job(
            "import",
            str(payload.get("requestId") or ""),
            lambda context: _import_job(context, uploaded, mapping_text),
            info,
        )
        waited = await run_in_threadpool(khach_jobs.wait_job, job["id"], 1.5)
        return {"job": waited or job}
    uploaded, fields, problems = await _read_customer_form(request)
    try:
        async with _heavy_lock:
            return await run_in_threadpool(_import_locked, uploaded, fields, problems)
    finally:
        _cleanup_uploads(uploaded)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _checkpoint(job: dict[str, Any]) -> int | None:
    """Mốc file tiếp theo của một việc. Thiếu khóa thì không coi là 0."""
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    info = job.get("info") if isinstance(job.get("info"), dict) else {}
    points: list[int] = []
    if "completedFiles" in progress:
        points.append(_as_int(progress.get("completedFiles")))
    if "fileIndex" in progress and progress.get("fileIndex") is not None:
        points.append(_as_int(progress.get("fileIndex")))
    if "resumeFrom" in info and info.get("resumeFrom") is not None:
        points.append(_as_int(info.get("resumeFrom")))
    if not points:
        return None
    return max(points)


def _best_start(job: dict[str, Any]) -> int:
    """Mốc cao nhất trên cả chuỗi nối tiếp. Việc con đang chờ, chưa có tiến độ, không kéo về file 1."""
    seen: set[str] = set()
    best: int | None = None
    current: dict[str, Any] | None = job
    while current is not None:
        job_id = str(current.get("id") or "")
        if job_id and job_id in seen:
            break
        if job_id:
            seen.add(job_id)
        point = _checkpoint(current)
        if point is not None:
            best = point if best is None else max(best, point)
        info = current.get("info") if isinstance(current.get("info"), dict) else {}
        parent_id = str(info.get("resumeOf") or "")
        if not parent_id or parent_id in seen:
            break
        current = khach_jobs.get_job(parent_id)
    return 0 if best is None else best


def _upload_ids(uploaded: list[tuple[str, Path]]) -> list[str]:
    root = khach_uploads.uploads_root().resolve()
    ids: list[str] = []
    for _name, path in uploaded:
        try:
            parent = path.resolve().parent
        except OSError:
            continue
        if parent.parent == root and re.fullmatch(r"[0-9a-f]{32}", parent.name):
            ids.append(parent.name)
    return ids


def _files_for_job(job: dict[str, Any]) -> list[tuple[str, Path]]:
    info = job.get("info") if isinstance(job.get("info"), dict) else {}
    uploads = info.get("uploads")
    if isinstance(uploads, list) and any(str(item or "") for item in uploads):
        return _resolve_uploads(uploads)
    names = info.get("files")
    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail="Lần nạp này không còn danh sách file để nối tiếp.")
    try:
        return khach_uploads.resolve_names([str(name or "") for name in names])
    except khach_uploads.UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _mapping_text_for_job(job: dict[str, Any]) -> str:
    info = job.get("info") if isinstance(job.get("info"), dict) else {}
    raw = info.get("mapping")
    if isinstance(raw, dict):
        text = json.dumps(raw, ensure_ascii=False)
    elif isinstance(raw, str) and raw.strip():
        text = raw
    else:
        text = "{}"
    try:
        _parse_mapping(text, allow_empty=True)
    except HTTPException:
        return "{}"
    return text


def _resume_anchor(job_id: str) -> dict[str, Any]:
    """Đi theo lần nối tiếp mới nhất. Việc đang chạy hoặc đã xong thì trả về, không nạp thêm."""
    seen: set[str] = set()
    current = khach_jobs.get_job(job_id)
    if current is None or current.get("kind") != "import":
        raise HTTPException(status_code=404, detail="Không thấy lần nạp để nối tiếp.")
    while str(current.get("id") or "") not in seen:
        seen.add(str(current["id"]))
        info = current.get("info") if isinstance(current.get("info"), dict) else {}
        child_id = str(info.get("resumedBy") or "")
        if not child_id:
            child_id = khach_jobs.request_job_id(f"resume-{current['id']}") or ""
        if not child_id or child_id in seen:
            break
        child = khach_jobs.get_job(child_id)
        if child is None or child.get("kind") != "import":
            break
        if child.get("status") in ("queued", "waiting", "running", "done"):
            return child
        if child.get("status") in ("interrupted", "error"):
            current = child
            continue
        break
    return current


def _file_at_best(job: dict[str, Any]) -> str:
    """Tên file ở mốc cao nhất. Việc con đang chờ thường không có tên file."""
    seen: set[str] = set()
    best: int | None = None
    label = ""
    current: dict[str, Any] | None = job
    while current is not None:
        job_id = str(current.get("id") or "")
        if job_id and job_id in seen:
            break
        if job_id:
            seen.add(job_id)
        point = _checkpoint(current)
        progress = current.get("progress") if isinstance(current.get("progress"), dict) else {}
        name = str(progress.get("file") or "")
        if point is not None and (best is None or point >= best):
            best = point
            if name:
                label = name
        info = current.get("info") if isinstance(current.get("info"), dict) else {}
        parent_id = str(info.get("resumeOf") or "")
        if not parent_id or parent_id in seen:
            break
        current = khach_jobs.get_job(parent_id)
    if label:
        return label
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    return str(progress.get("file") or "")


def _paused_summary(job: dict[str, Any]) -> dict[str, Any]:
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    info = job.get("info") if isinstance(job.get("info"), dict) else {}
    files = info.get("files") if isinstance(info.get("files"), list) else []
    uploads = info.get("uploads") if isinstance(info.get("uploads"), list) else []
    file_count = _as_int(progress.get("fileCount")) or len(files)
    start = _best_start(job)
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "file": _file_at_best(job),
        "fileIndex": _as_int(progress.get("fileIndex")) if progress.get("fileIndex") is not None else start,
        "fileCount": file_count,
        "resumeFrom": start,
        "incoming": _as_int(progress.get("incoming")),
        "canResume": bool(files or uploads),
    }


def _child_id(job: dict[str, Any]) -> str:
    info = job.get("info") if isinstance(job.get("info"), dict) else {}
    child_id = str(info.get("resumedBy") or "")
    if child_id:
        return child_id
    return khach_jobs.request_job_id(f"resume-{job.get('id')}") or ""


def _paused_import_jobs() -> list[dict[str, Any]]:
    """Lần nạp dừng mà chưa có lần nối tiếp mới hơn. Không kèm danh sách file."""
    found: list[dict[str, Any]] = []
    try:
        paths = list(khach_jobs.jobs_root().glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
            continue
        job = khach_jobs.get_job(path.stem)
        if not job or job.get("kind") != "import":
            continue
        status = job.get("status")
        info = job.get("info") if isinstance(job.get("info"), dict) else {}
        if status == "interrupted" or (status == "error" and info.get("resumeOf")):
            found.append(job)
    known = {str(job.get("id") or "") for job in found}
    visible: list[dict[str, Any]] = []
    for job in found:
        child_id = _child_id(job)
        if child_id and child_id != str(job.get("id") or "") and (child_id in known or khach_jobs.get_job(child_id) is not None):
            continue
        visible.append(job)
    return visible


def _archived_imports() -> dict[str, Path]:
    """Tên file → bản lưu mới nhất trong thư mục các lần nạp."""
    root = settings.imports_dir()
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    try:
        folders = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        return found
    folders.sort(key=lambda path: path.name, reverse=True)
    for folder in folders:
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for path in children:
            if not path.is_file() or path.name in found:
                continue
            try:
                if path.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            found[path.name] = path
    return found


@router.post("/v1/customers/imports/reread")
def customers_reread(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Đọc lại các file chưa nạp mà máy chủ còn giữ. Bấm lần nữa không tạo thêm việc đang chạy."""
    _manager(_actor(request))
    db.init_db(settings.db_path)
    wanted = [str(name) for name in _import_sessions().get("needReupload") or [] if str(name or "")]
    if not wanted:
        return {
            "job": None,
            "found": 0,
            "missing": [],
            "detail": "Không còn file nào chưa nạp. Các lần nạp đã nằm trong kho.",
        }
    archive = _archived_imports()
    uploaded = [(name, archive[name]) for name in wanted if name in archive]
    missing = [name for name in wanted if name not in archive]
    if not uploaded:
        raise HTTPException(
            status_code=400,
            detail="File chưa nạp không còn bản lưu trên máy chủ. Hãy chọn lại các file đó rồi nạp.",
        )
    mapping_text = "{}"
    digest = hashlib.sha1("\n".join(name for name, _path in uploaded).encode("utf-8")).hexdigest()[:20]
    info = {
        "files": [name for name, _path in uploaded],
        "mapping": mapping_text,
        "reread": True,
        "absent": missing[:80],
    }

    def work(
        context: khach_jobs.JobContext,
        uploaded: list[tuple[str, Path]] = uploaded,
        mapping_text: str = mapping_text,
    ) -> dict[str, Any]:
        return _import_job(context, uploaded, mapping_text)

    job = khach_jobs.start_job("import", f"reread-{digest}", work, info)
    return {"job": job, "found": len(uploaded), "missing": missing[:80]}


@router.get("/v1/customers/imports/paused")
def customers_imports_paused(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Các lần nạp dừng giữa chừng. Không kèm danh sách file."""
    _manager(_actor(request))
    visible = [_paused_summary(job) for job in _paused_import_jobs()]
    visible.sort(key=lambda item: (int(item.get("fileCount") or 0), str(item.get("id") or "")), reverse=True)
    return {"jobs": visible[:20]}


def _begin_resume(job_id: str) -> dict[str, Any]:
    """Tạo lần nối tiếp từ mốc cao nhất. Việc đang chạy thì trả về việc đó."""
    anchor = _resume_anchor(job_id)
    if anchor.get("status") in ("queued", "waiting", "running", "done"):
        return anchor
    uploaded = _files_for_job(anchor)
    mapping_text = _mapping_text_for_job(anchor)
    start = _best_start(anchor)
    info = anchor.get("info") if isinstance(anchor.get("info"), dict) else {}
    child_info = {
        "files": [name for name, _path in uploaded],
        "rows": _as_int(info.get("rows")),
        "uploads": _upload_ids(uploaded),
        "mapping": mapping_text,
        "resumeOf": anchor["id"],
        "resumeFrom": start,
    }

    def work(
        context: khach_jobs.JobContext,
        uploaded: list[tuple[str, Path]] = uploaded,
        mapping_text: str = mapping_text,
        start: int = start,
    ) -> dict[str, Any]:
        return _import_job(context, uploaded, mapping_text, start)

    job = khach_jobs.start_job("import", f"resume-{anchor['id']}", work, child_info)
    khach_jobs.remember_resume(str(anchor["id"]), str(job["id"]))
    return job


def _resume_sort_key(job: dict[str, Any]) -> tuple[int, float]:
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    info = job.get("info") if isinstance(job.get("info"), dict) else {}
    files = info.get("files") if isinstance(info.get("files"), list) else []
    file_count = _as_int(progress.get("fileCount")) or len(files)
    start = _best_start(job)
    remaining = max(0, file_count - start) if file_count else 10**9
    return (remaining, -float(job.get("updatedAt") or 0))


def continue_paused_imports() -> None:
    """Sau khi máy chủ sống lại, nối tiếp từng lần nạp dở. Phần còn ít chạy trước."""
    if os.environ.get("KHACH_AUTO_RESUME", "1") == "0":
        return
    time.sleep(2)
    try:
        db.init_db(settings.db_path)
        leaves = _paused_import_jobs()
    except Exception:
        _LOG.exception("Không liệt kê được lần nạp dở để nối tiếp")
        return
    leaves.sort(key=_resume_sort_key)
    for job in leaves:
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        try:
            child = _begin_resume(job_id)
        except HTTPException:
            continue
        except Exception:
            _LOG.exception("Không nối tiếp được lần nạp %s", job_id)
            continue
        if child.get("status") in ("queued", "waiting", "running"):
            khach_jobs.wait_job(str(child.get("id") or ""), 7 * 24 * 3600)


@router.post("/v1/customers/imports/{job_id}/resume")
async def customers_import_resume(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Nối tiếp từ file dở. Bấm lần nữa trả về đúng việc đang chạy, không nạp trùng."""
    _manager(_actor(request))
    db.init_db(settings.db_path)
    job = await run_in_threadpool(_begin_resume, job_id)
    waited = await run_in_threadpool(khach_jobs.wait_job, job["id"], 1.5)
    return {"job": waited or job}


def _import_locked(uploaded: list[tuple[str, Path]], fields: dict[str, str], problems: list[str]) -> dict[str, Any]:
    with khach_jobs.import_lock():
        return _import_uploaded(uploaded, fields, problems)


@router.get("/v1/customers/sessions")
def customers_sessions(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _manager(_actor(request))
    db.init_db(settings.db_path)
    return _import_sessions()


def _unique_phones(*groups: list[str]) -> tuple[list[str], bool]:
    """Bỏ số trùng theo khóa SĐT (0901… và 84901… là một). True nếu vượt giới hạn."""
    seen: set[str] = set()
    phones: list[str] = []
    for group in groups:
        for phone in group:
            key = db.phone_key(phone) or phone
            if key in seen:
                continue
            if len(phones) >= MAX_LOOKUP:
                return phones, True
            seen.add(key)
            phones.append(phone)
    return phones, False


def _encode_phone_groups(groups: list[list[str]]) -> str:
    return "\n".join("\t".join(group) for group in groups)


def _decode_phone_groups(text: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        phones = [part.strip() for part in line.split("\t") if part.strip()]
        if phones:
            groups.append(phones)
    return groups


def _flatten_phone_groups(groups: list[list[str]]) -> list[str]:
    return [phone for group in groups for phone in real_lookup_phones(group)]


def _collect_lookup_groups(
    text: str,
    uploaded: list[tuple[str, Path]],
    problems: list[str],
    progress: Any = None,
) -> tuple[list[list[str]], dict[str, Any], list[dict[str, Any]]]:
    """Mỗi dòng dán, và mỗi dòng file có số, là một khách. Số đã gặp ở dòng khác vẫn giữ dòng đó."""
    pasted, parsed = phone_groups_from_text(text)
    file_groups: list[list[str]] = []
    file_reports: list[dict[str, Any]] = []
    if uploaded:
        file_groups, file_reports = phone_groups_from_files(uploaded, progress=progress)
        problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    groups = list(pasted)
    kept = sum(len(real_lookup_phones(group)) for group in groups)
    truncated = bool(parsed.get("truncated"))
    for group in file_groups:
        if not group:
            continue
        if kept >= MAX_LOOKUP:
            truncated = True
            break
        room = MAX_LOOKUP - kept
        chosen = group if len(group) <= room else group[:room]
        groups.append(list(chosen))
        kept += len(chosen)
        if len(chosen) < len(group):
            truncated = True
            break
    if not groups:
        raise HTTPException(
            status_code=400,
            detail="Không thấy số điện thoại nào trong danh sách." + ((" " + " ".join(problems)) if problems else ""),
        )
    if truncated:
        parsed["truncated"] = 1
    return groups, parsed, file_reports


def _lookup_result(
    groups: list[list[str]],
    parsed: dict[str, Any],
    file_reports: list[dict[str, Any]],
    problems: list[str],
    progress: Any = None,
) -> dict[str, Any]:
    shown, stats = lookup_phone_groups(
        settings.db_path, groups, keep=LOOKUP_RETURN_LIMIT, progress=progress
    )
    rows = int(stats.pop("rows", len(shown)))
    stats.update(parsed)
    if rows > len(shown):
        stats["shown"] = len(shown)
        stats["hidden"] = rows - len(shown)
    errors = [item for item in problems if item]
    stats["fileErrors"] = errors
    return {"stats": stats, "files": file_reports, "fileErrors": errors, "items": shown}


def _lookup_now(
    text: str,
    uploaded: list[tuple[str, Path]],
    problems: list[str],
    actor: accounts.Actor,
) -> dict[str, Any]:
    groups, parsed, reports = _collect_lookup_groups(text, uploaded, problems)
    result = _lookup_result(groups, parsed, reports, problems)
    result["quota"] = _charge(actor, _flatten_phone_groups(groups))
    return result


def _lookup_job(
    context: khach_jobs.JobContext,
    text: str,
    uploaded: list[tuple[str, Path]],
    actor: accounts.Actor,
) -> dict[str, Any]:
    problems: list[str] = []
    groups, parsed, reports = _collect_lookup_groups(
        text,
        uploaded,
        problems,
        progress=lambda progress: context.report({"phase": "read", **progress}),
    )
    phones = _flatten_phone_groups(groups)
    khach_jobs.job_file(context.id, ".phones").write_text(_encode_phone_groups(groups), encoding="utf-8")
    context.report({"phase": "match", "matched": 0, "total": len(phones)}, force=True)
    result = _lookup_result(
        groups,
        parsed,
        reports,
        problems,
        progress=lambda progress: context.report({"phase": "match", **progress}),
    )
    result["lookupId"] = context.id
    result["quota"] = _charge(actor, phones)
    return result


async def _lookup_request(request: Request) -> tuple[str, list[tuple[str, Path]], list[str], str]:
    """Đọc yêu cầu tra cứu: JSON (dán số / mã file đã gửi), form, hoặc multipart kiểu cũ."""
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await _json_object(request)
        uploaded = _resolve_uploads(payload.get("uploads"))
        return str(payload.get("text") or ""), uploaded, [], str(payload.get("requestId") or "")
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        try:
            return str(form.get("text") or ""), [], [], ""
        finally:
            await form.close()
    raise ValueError("multipart")


@router.post("/v1/customers/lookup")
async def customers_lookup(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    actor = _actor(request)
    db.init_db(settings.db_path)
    try:
        text, uploaded, problems, request_id = await _lookup_request(request)
    except ValueError:
        spooled, fields, problems = await _read_customer_form(request)
        try:
            async with _heavy_lock:
                return await run_in_threadpool(_lookup_now, fields.get("text") or "", spooled, problems, actor)
        finally:
            _cleanup_uploads(spooled)
    if request_id:
        job = khach_jobs.start_job(
            "lookup",
            request_id,
            lambda context: _lookup_job(context, text, uploaded, actor),
            {"files": [name for name, _path in uploaded], "accountId": actor.id},
        )
        waited = await run_in_threadpool(khach_jobs.wait_job, job["id"], 2.0)
        return {"job": waited or job}
    return await run_in_threadpool(_lookup_now, text, uploaded, problems, actor)


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


def _tsv_field(value: Any) -> str:
    """Giữ xuống dòng trong một ô khi dán vào Excel."""
    text = str(value if value is not None else "")
    if any(mark in text for mark in ("\t", "\n", "\r", '"')):
        return '"' + text.replace('"', '""') + '"'
    return text


def _write_lookup_copy(groups: list[list[str]]) -> Path:
    """Mỗi dòng danh sách một dòng kết quả. Số không có địa chỉ thì ô địa chỉ là dấu chấm."""
    handle = tempfile.NamedTemporaryFile(prefix="tra-cuu-copy-", suffix=".txt", delete=False)
    dest = Path(handle.name)
    handle.close()
    try:
        with dest.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("Số điện thoại\tĐịa chỉ\n")
            for item in iter_lookup_groups(settings.db_path, groups):
                phone = str(item.get("query") or "")
                address = str(item.get("resultAddress") or ".") or "."
                handle.write(f"{_tsv_field(phone)}\t{_tsv_field(address)}\n")
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return dest


def _write_lookup_xlsx(groups: list[list[str]]) -> Path:
    """Ghi kết quả tra cứu ra Excel theo từng dòng danh sách, kể cả số không có địa chỉ."""
    headers = ["Số tra cứu", "Có địa chỉ", *FIELD_LABELS.values()]

    def rows() -> Any:
        for item in iter_lookup_groups(settings.db_path, groups):
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
    return dest


def _export_phones(
    text: str,
    uploaded: list[tuple[str, Path]],
    problems: list[str],
    actor: accounts.Actor,
) -> Path:
    groups, _parsed, _reports = _collect_lookup_groups(text, uploaded, problems)
    _charge(actor, _flatten_phone_groups(groups))
    return _write_lookup_xlsx(groups)


@router.post("/v1/customers/lookup/export")
async def customers_lookup_export(
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    actor = _actor(request)
    db.init_db(settings.db_path)
    try:
        text, uploaded, problems, _request_id = await _lookup_request(request)
    except ValueError:
        spooled, fields, problems = await _read_customer_form(request)
        try:
            async with _heavy_lock:
                dest = await run_in_threadpool(
                    _export_phones, fields.get("text") or "", spooled, problems, actor
                )
        finally:
            _cleanup_uploads(spooled)
        return _xlsx_file_response(dest, "tra-cuu-khach.xlsx")
    dest = await run_in_threadpool(_export_phones, text, uploaded, problems, actor)
    return _xlsx_file_response(dest, "tra-cuu-khach.xlsx")


@router.get("/v1/customers/lookup/jobs/{job_id}/export")
def customers_lookup_job_export(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    """Tải Excel đúng danh sách số của lần tra cứu đã chạy, không gửi lại file."""
    actor = _actor(request)
    db.init_db(settings.db_path)
    groups = _lookup_job_groups(actor, job_id)
    _charge(actor, _flatten_phone_groups(groups))
    return _xlsx_file_response(_write_lookup_xlsx(groups), "tra-cuu-khach.xlsx")


def _lookup_job_groups(actor: accounts.Actor, job_id: str) -> list[list[str]]:
    job = _visible_job(actor, khach_jobs.get_job(job_id))
    if job.get("kind") != "lookup":
        raise HTTPException(status_code=404, detail="Không thấy lần tra cứu này, hãy tra cứu lại.")
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="Tra cứu chưa xong.")
    try:
        text = khach_jobs.job_file(job_id, ".phones").read_text(encoding="utf-8")
    except (OSError, khach_jobs.JobError) as exc:
        raise HTTPException(status_code=404, detail="Kết quả tra cứu đã quá hạn, hãy tra cứu lại.") from exc
    return _decode_phone_groups(text)


@router.get("/v1/customers/lookup/jobs/{job_id}/copy")
def customers_lookup_job_copy(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    """Hai cột của khách tra được, số điện thoại bên trái và địa chỉ bên phải, để dán vào Excel."""
    actor = _actor(request)
    db.init_db(settings.db_path)
    groups = _lookup_job_groups(actor, job_id)
    _charge(actor, _flatten_phone_groups(groups))
    dest = _write_lookup_copy(groups)
    return FileResponse(
        path=dest,
        media_type="text/plain; charset=utf-8",
        filename="dia-chi-so-dien-thoai.txt",
        background=BackgroundTask(_unlink_temp, str(dest)),
    )


@router.get("/v1/customers/export")
def customers_export(request: Request, authorization: str | None = Header(default=None)) -> FileResponse:
    _manager(_actor(request))
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
