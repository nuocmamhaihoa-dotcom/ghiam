"""API kho khách: xem danh sách, hỏi cột Excel, rồi nạp và gộp trùng."""

from __future__ import annotations

import asyncio
import gc
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
    iter_lookup_items,
    lookup_phones,
    phones_from_text,
    public_customer,
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
    phones_from_files,
    preview_table,
    write_xlsx_to_path,
)
from control_plane.settings import settings

router = APIRouter()
_heavy_lock = asyncio.Lock()


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
    row_sum = sum(int(item.get("incoming") or 0) for item in sessions)
    unique_phones = int(warehouse["uniquePhones"] or 0)
    return {
        "total": warehouse["total"],
        "uniquePhones": unique_phones,
        "emptyPhone": warehouse["emptyPhone"],
        "duplicatePhones": warehouse["duplicatePhones"],
        "sessions": sessions,
        "needReupload": unique_reupload[:80],
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


@router.get("/v1/customers")
def customers_list(
    request: Request,
    q: str = Query(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _manager(_actor(request))
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


def _parse_mapping(mapping_text: str) -> dict[str, Any]:
    try:
        parsed = mapping_from_form(json.loads(mapping_text or ""))
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Bảng chọn cột không đọc được.") from exc
    if not any(_mapped(parsed.get(field) or {}) for field in FIELDS) and not any(
        _mapped((spec or {}).get(field) or {})
        for spec in (parsed.get("fileMaps") or {}).values()
        if isinstance(spec, dict)
        for field in FIELDS
    ):
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
) -> dict[str, Any]:
    parsed = _parse_mapping(mapping_text)
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
    for file_index, (name, payload) in enumerate(uploaded):
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
    problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    problems = [item for item in problems if item]
    if incoming_total == 0:
        raise HTTPException(
            status_code=400,
            detail="Không đọc được dòng khách nào. " + " ".join(problems),
        )
    stats["total"] = db.customer_count(settings.db_path)
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


def _import_job(context: khach_jobs.JobContext, uploaded: list[tuple[str, Path]], mapping_text: str) -> dict[str, Any]:
    with khach_jobs.import_lock(context):
        return _import_files(
            uploaded,
            mapping_text,
            [],
            progress=lambda progress: context.report(progress),
            link=True,
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
        info = {"files": [name for name, _path in uploaded], "rows": int(payload.get("expectedRows") or 0)}
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


def _collect_lookup_phones(
    text: str,
    uploaded: list[tuple[str, Path]],
    problems: list[str],
    progress: Any = None,
) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    pasted, parsed = phones_from_text(text)
    file_phones: list[str] = []
    file_reports: list[dict[str, Any]] = []
    if uploaded:
        file_phones, file_reports = phones_from_files(uploaded, progress=progress)
        problems.extend(str(item.get("error") or "") for item in file_reports if item.get("error"))
    phones, cut = _unique_phones(pasted, file_phones)
    if not phones:
        raise HTTPException(
            status_code=400,
            detail="Không thấy số điện thoại nào trong danh sách." + ((" " + " ".join(problems)) if problems else ""),
        )
    if cut:
        parsed["truncated"] = 1
    return phones, parsed, file_reports


def _lookup_result(
    phones: list[str],
    parsed: dict[str, Any],
    file_reports: list[dict[str, Any]],
    problems: list[str],
    progress: Any = None,
) -> dict[str, Any]:
    shown, stats = lookup_phones(settings.db_path, phones, keep=LOOKUP_RETURN_LIMIT, progress=progress)
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
    phones, parsed, reports = _collect_lookup_phones(text, uploaded, problems)
    result = _lookup_result(phones, parsed, reports, problems)
    result["quota"] = _charge(actor, phones)
    return result


def _lookup_job(
    context: khach_jobs.JobContext,
    text: str,
    uploaded: list[tuple[str, Path]],
    actor: accounts.Actor,
) -> dict[str, Any]:
    problems: list[str] = []
    phones, parsed, reports = _collect_lookup_phones(
        text,
        uploaded,
        problems,
        progress=lambda progress: context.report({"phase": "read", **progress}),
    )
    khach_jobs.job_file(context.id, ".phones").write_text("\n".join(phones), encoding="utf-8")
    context.report({"phase": "match", "matched": 0, "total": len(phones)}, force=True)
    result = _lookup_result(
        phones,
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


def _write_lookup_xlsx(phones: list[str]) -> Path:
    """Ghi kết quả tra cứu ra Excel theo từng lô số, không giữ cả triệu dòng trong RAM."""
    headers = ["Số tra cứu", "Có trong kho", *FIELD_LABELS.values()]

    def rows() -> Any:
        for item in iter_lookup_items(settings.db_path, phones):
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
    phones, _parsed, _reports = _collect_lookup_phones(text, uploaded, problems)
    _charge(actor, phones)
    return _write_lookup_xlsx(phones)


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
    job = _visible_job(actor, khach_jobs.get_job(job_id))
    if job.get("kind") != "lookup":
        raise HTTPException(status_code=404, detail="Không thấy lần tra cứu này, hãy tra cứu lại.")
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="Tra cứu chưa xong.")
    try:
        text = khach_jobs.job_file(job_id, ".phones").read_text(encoding="utf-8")
    except (OSError, khach_jobs.JobError) as exc:
        raise HTTPException(status_code=404, detail="Kết quả tra cứu đã quá hạn, hãy tra cứu lại.") from exc
    phones = [line for line in text.split("\n") if line]
    _charge(actor, phones)
    return _xlsx_file_response(_write_lookup_xlsx(phones), "tra-cuu-khach.xlsx")


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
