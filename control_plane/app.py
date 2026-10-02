"""
High-bandwidth LAN control plane — run on a PC server.

Features:
- Agent register / heartbeat (unlimited PCs)
- Serve update manifest + packages over LAN (prefer vs Internet)
- Bulk comment sync from scanner PCs
- GZip, large uploads, keep-alive friendly defaults
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import uuid
import zipfile

import anyio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from control_plane import db
from control_plane.delivery import PACKAGE_NAME, ensure_package
from control_plane.handles import exact_line, profile_from_share
from control_plane.people import (
    apply_novel,
    clean_name,
    clean_username,
    complete_rows,
    complete_sightings,
    name_key,
    profile_from_line,
)
from control_plane.version import IPHONE_BUILD, VIDEO_WORKER_BUILD
from control_plane.video_package import SETUP_NAME, ensure_pc_setup_package, ensure_video_package, render_pc_launcher
from control_plane import video_helpers
from control_plane.screen_steps import (
    ScreenVideoError,
    analyze_screen_video,
    clean_ocr,
    cut_video_part,
    discard_video_work,
    faststart_video,
    read_screen_image,
    seen_line,
    video_duration,
)
from control_plane.screen_people import locate_tesseract, propose_rows, reading_counts
from control_plane.settings import settings
from control_plane.video_jobs import JobProgress, VideoJob, jobs

STATIC_DIR = Path(__file__).resolve().parent / "static"
IOS_DIR = Path(__file__).resolve().parents[1] / "ios"

app = FastAPI(title="fb-poller high-bandwidth control plane", version="1.3.0")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class _TransferGuard:
    """Chặn body quá cỡ mà không gom cả file video vào bộ nhớ. Video trả về không nén gzip."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            path = str(scope.get("path") or "")
            method = str(scope.get("method") or "")
            headers = list(scope.get("headers") or [])
            if method == "GET" and path.endswith("/video"):
                scope["headers"] = [(key, value) for key, value in headers if key != b"accept-encoding"]
            else:
                limit = _upload_limit_bytes()
                if limit is not None:
                    for key, value in headers:
                        if key != b"content-length":
                            continue
                        try:
                            size = int(value)
                        except ValueError:
                            size = 0
                        if size > limit:
                            response = JSONResponse({"detail": "upload too large"}, status_code=413)
                            await response(scope, receive, send)
                            return
                        break
        await self.app(scope, receive, send)


app.add_middleware(_TransferGuard)


def _upload_limit_bytes() -> int | None:
    """0 nghĩa là không chặn dung lượng từng video."""
    if settings.max_upload_mb <= 0:
        return None
    return settings.max_upload_mb * 1024 * 1024


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACTION_KINDS = {"note", "cli", "proxy_check", "proxy_upload", "package_upload", "screen", "issue"}


def _remember_hub(kind: str, summary: str, detail: str | None = None, actor: str = "me") -> None:
    """Persist an operator action. A journal failure must not break the action itself."""
    try:
        db.record_action(
            settings.db_path,
            at=utcnow(),
            actor=actor,
            source="hub",
            kind=kind,
            summary=summary,
            detail=detail,
        )
    except Exception:
        return


@app.on_event("startup")
async def _startup() -> None:
    settings.ensure_dirs()
    db.init_db(settings.db_path)
    # Việc đọc video chiếm luồng. Nhịp sống và lệnh nhận video không xếp hàng sau chúng.
    anyio.to_thread.current_default_thread_limiter().total_tokens = 80
    # Import proxy list into DB immediately; live/die loop runs in background
    from control_plane.proxy_check import load_proxy_lines, start_background_checker

    lines = load_proxy_lines(settings.proxies_file)
    if lines:
        db.upsert_proxy_endpoints(settings.db_path, lines, "static")
        cd_copy = settings.data_dir / "proxies_static.txt"
        if not cd_copy.exists():
            cd_copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _load_uploads()
    start_background_checker()


def _auth(authorization: str | None) -> None:
    expected = settings.token or os.environ.get("CONTROL_TOKEN")
    if not expected:
        return
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")


class RegisterBody(BaseModel):
    machine_id: str
    hostname: str | None = None
    agent_version: str | None = None
    poller_version: str | None = None
    os: str | None = None
    channel: str | None = None
    started_at: str | None = None
    link_speed_mbps: float | None = None
    ip: str | None = None


class HeartbeatBody(BaseModel):
    machine_id: str
    hostname: str | None = None
    agent_version: str | None = None
    poller_version: str | None = None
    poller_running: bool | None = None
    comments_local: int | None = None
    ts: str | None = None
    link_speed_mbps: float | None = None


class CommentItem(BaseModel):
    comment_id: str
    post_url: str | None = None
    post_id: str | int | None = None
    text: str | None = None
    author_name: str | None = None
    author_id: str | None = None
    created_time: str | None = None
    first_seen_at: str | None = None
    raw: dict[str, Any] | None = None


class SyncCommentsBody(BaseModel):
    machine_id: str
    comments: list[CommentItem] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "time": utcnow(),
        "mode": "vps-hub-high-bandwidth",
        "max_upload_mb": settings.max_upload_mb,
        "packages_dir": str(settings.packages_dir),
        "dashboard": "/",
        "iphoneBuild": IPHONE_BUILD,
        "delivery": "/tai",
        "videoHelper": video_helpers.helpers.public(),
    }


def _html(name: str, status_code: int = 200) -> HTMLResponse:
    path = STATIC_DIR / name
    if not path.exists():
        return HTMLResponse("<p>Missing page.</p>", status_code=404)
    text = path.read_text(encoding="utf-8").replace("__IPHONE_BUILD__", str(IPHONE_BUILD))
    if name in {"iphone.html", "dashboard.html"}:
        text = text.replace("__CONTROL_TOKEN_JSON__", json.dumps(settings.token or ""))
    return HTMLResponse(text, status_code=status_code, headers={"Cache-Control": "no-cache"})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse | JSONResponse:
    path = request.url.path
    if exc.status_code == 404 and not path.startswith("/v1/") and not path.endswith(".zip"):
        return _html("missing.html", 404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """Web UI — hiển thị comment đã sync từ các PC scanner."""
    return _html("dashboard.html")


@app.get("/phone", response_class=HTMLResponse)
def phone() -> HTMLResponse:
    """Khung điện thoại trên PC. Trang trong khung là dashboard, chuột được ghi như ngón tay."""
    return _html("phone.html")


@app.get("/iphone", response_class=HTMLResponse)
def iphone_app() -> HTMLResponse:
    """App trên iPhone. Token được gắn sẵn. Ghi thì ẩn app và chỉ còn nút Kết thúc."""
    return _html("iphone.html")


@app.post("/iphone")
async def iphone_share_target(
    title: str = Form(default=""),
    text: str = Form(default=""),
    url: str = Form(default=""),
) -> RedirectResponse:
    """PWA share target: nhận link / chữ từ share sheet rồi mở lại trang iPhone."""
    query = urlencode(
        {key: value.strip() for key, value in (("url", url), ("text", text), ("title", title)) if value.strip()}
    )
    return RedirectResponse(url=f"/iphone?{query}" if query else "/iphone", status_code=303)


@app.get("/cai-app", response_class=HTMLResponse)
def install_ios_app() -> HTMLResponse:
    """Cách cài app đọc chữ trên ứng dụng khác."""
    return _html("cai-app.html")


def _pc_launcher_bat(hub: str, token: str) -> str:
    root = Path(__file__).resolve().parents[1]
    template = (root / "pc_agent" / "windows" / "FbPoller.bat").read_text(encoding="utf-8")
    return render_pc_launcher(template, hub=hub, token=token)


@app.get("/tai-pc", response_class=HTMLResponse)
def pc_download_page() -> HTMLResponse:
    """Trang tải phần mềm nối PC. File mở là chạy, token nằm trong file."""
    return _html("tai-pc.html")


@app.get("/tai-pc/FbPoller.bat")
def pc_launcher_bat(request: Request) -> Response:
    """File người dùng bấm đúp. Hub và token được gắn lúc tải, không lưu trong git."""
    hub = str(request.base_url).rstrip("/")
    body = _pc_launcher_bat(hub, settings.token or "")
    return Response(
        content=body.encode("utf-8"),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": 'attachment; filename="FbPoller.bat"',
        },
    )


@app.get("/tai-pc/FbPollerVideo.zip")
def pc_setup_zip(request: Request) -> Response:
    repo = Path(__file__).resolve().parents[1]
    path = ensure_pc_setup_package(repo, settings.data_dir / "delivery")
    if path.name != SETUP_NAME or not path.is_file():
        raise HTTPException(404, "package missing")
    hub = str(request.base_url).rstrip("/")
    config = json.dumps({"hub": hub, "token": settings.token or ""}, ensure_ascii=False)
    launcher = _pc_launcher_bat(hub, settings.token or "")
    buffer = io.BytesIO()
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for info in source.infolist():
            if info.filename in {"config.json", "FbPoller.bat"}:
                continue
            dest.writestr(info, source.read(info.filename))
        dest.writestr("config.json", config)
        dest.writestr("FbPoller.bat", launcher)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": 'attachment; filename="FbPollerVideo.zip"',
        },
    )


@app.get("/tai")
def delivery_page() -> RedirectResponse:
    """Đường ngắn trên điện thoại: vào thẳng app."""
    return RedirectResponse(url="/iphone", status_code=302)


@app.get("/v1/delivery")
def delivery_info() -> dict[str, Any]:
    pkg = ensure_package(STATIC_DIR, settings.data_dir)
    return {
        "iphoneBuild": IPHONE_BUILD,
        "iphonePath": "/iphone",
        "installPath": "/tai",
        "package": {
            "name": pkg.name,
            "bytes": pkg.stat().st_size,
            "sha256": _sha256(pkg),
            "path": "/tai/goi.zip",
        },
    }


@app.get("/tai/ios.zip")
def ios_source_zip() -> Response:
    """Xcode project for the iPhone app. No token and no database."""
    if not IOS_DIR.is_dir():
        raise HTTPException(status_code=404, detail="ios project missing")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(IOS_DIR.rglob("*")):
            if not path.is_file() or "xcuserdata" in path.parts:
                continue
            archive.write(path, Path("ios") / path.relative_to(IOS_DIR))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=fb-poller-ios.zip",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/tai/goi.zip")
def delivery_package() -> FileResponse:
    pkg = ensure_package(STATIC_DIR, settings.data_dir)
    if pkg.name != PACKAGE_NAME:
        raise HTTPException(status_code=404, detail="package missing")
    return FileResponse(
        pkg,
        media_type="application/zip",
        filename=pkg.name,
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest")
def web_manifest() -> FileResponse:
    path = STATIC_DIR / "manifest.webmanifest"
    if not path.exists():
        raise HTTPException(status_code=404, detail="manifest missing")
    return FileResponse(path, media_type="application/manifest+json")


@app.get("/apple-touch-icon.png")
def apple_touch_icon() -> FileResponse:
    path = STATIC_DIR / "apple-touch-icon.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="icon missing")
    return FileResponse(path, media_type="image/png")


@app.get("/sample-people", response_class=HTMLResponse)
def sample_people() -> HTMLResponse:
    """Trang lướt mẫu: danh bạ rồi hồ sơ, để khung điện thoại tự ghép tên trùng."""
    return _html("sample-people.html")


class Sighting(BaseModel):
    kind: str
    name: str
    contactName: str = ""
    username: str = ""


class SightingsBody(BaseModel):
    items: list[Sighting] = Field(default_factory=list)


class PeopleRow(BaseModel):
    name: str = ""
    contactName: str = ""
    username: str = ""


class PeopleConfirmBody(BaseModel):
    rows: list[PeopleRow] = Field(default_factory=list)


_VIDEO_RESULT_LIMIT = 20_000
_STORE_FULL = "Kho đã đủ 50 triệu kết quả. Người mới chưa ghi."


@app.get("/v1/people")
def people_list(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str = Query(default=""),
) -> dict[str, Any]:
    _auth(authorization)
    items, next_cursor = db.list_people_page(settings.db_path, limit=limit, cursor=cursor)
    _total, ready = db.people_counts(settings.db_path)
    return {
        "count": ready,
        "capacity": db.PEOPLE_CAPACITY,
        "items": items,
        "known": items,
        "cursor": next_cursor,
    }


@app.get("/v1/people/duplicates")
def people_duplicates(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str = Query(default=""),
) -> dict[str, Any]:
    """Người vừa quét đã có trong kho. Không gồm người mới."""
    _auth(authorization)
    items, next_cursor = db.list_duplicates_page(settings.db_path, limit=limit, cursor=cursor)
    return {"count": db.duplicate_count(settings.db_path), "items": items, "cursor": next_cursor}


@app.post("/v1/people/sightings")
def people_sightings(
    body: SightingsBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    items = [item.model_dump() for item in body.items]
    keys = [name_key(clean_name(item.get("name") or "")) for item in items]
    with db.people_write_lock:
        stored = list(db.people_by_keys(settings.db_path, keys).values())
        folded, added = apply_novel(stored, items)
        skipped: list[str] = []
        if added:
            skipped = db.save_people(settings.db_path, folded, utcnow())
    if skipped:
        blocked = set(skipped)
        folded = [row for row in folded if row.get("nameKey") not in blocked]
        added = 0
    ready = complete_rows(folded)
    return {"count": len(ready), "items": ready, "saved": added, "known": folded}


def _video_archive(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Các dòng đủ ba cột của video vừa đọc, không phải cả kho."""
    return [
        {
            "name": row.get("name") or "",
            "contactName": row.get("contactName") or "",
            "username": row.get("username") or "",
        }
        for row in rows
        if row.get("contactName") and row.get("username")
    ]


def _write_people(rows: list[dict[str, str]]) -> list[str]:
    """Ghi rồi đọc lại đúng các khóa này. Lần ghi không thấy trên đĩa thì ghi một lần nữa."""
    skipped = set(db.save_people(settings.db_path, rows, utcnow()))
    pending = [row for row in rows if row.get("nameKey") not in skipped]
    if _people_match(pending):
        return list(skipped)
    skipped.update(db.save_people(settings.db_path, pending, utcnow()))
    pending = [row for row in rows if row.get("nameKey") not in skipped]
    if not _people_match(pending):
        raise HTTPException(500, "Chưa ghi được kết quả. Chọn lại video.")
    return list(skipped)


def _people_match(rows: list[dict[str, str]]) -> bool:
    saved = db.people_by_keys(settings.db_path, [row.get("nameKey") or "" for row in rows])
    for row in rows:
        found = saved.get(row.get("nameKey") or "")
        if found is None:
            return False
        if found.get("contactName") != (row.get("contactName") or ""):
            return False
        if found.get("username") != (row.get("username") or ""):
            return False
    return True


def _display_person(row: dict[str, str]) -> dict[str, str]:
    return {
        "name": row.get("name") or "",
        "contactName": row.get("contactName") or "",
        "username": row.get("username") or "",
    }


def _prepared_person(row: dict[str, str]) -> dict[str, str] | None:
    """Dòng đủ ba cột, đã làm sạch. Thiếu cột thì bỏ."""
    if not complete_sightings([row]):
        return None
    name = clean_name(row.get("name") or "")
    key = name_key(name)
    username = clean_username(row.get("username") or "")
    if not key or not username:
        return None
    return {
        "nameKey": key,
        "name": name,
        "contactName": clean_name(row.get("contactName") or ""),
        "username": username,
    }


def _save_proposed(
    rows: list[dict[str, str]],
) -> tuple[int, int, list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Ghi người mới. Người đã đủ ba cột trong kho thì đưa sang bảng trùng.

    Trả về (lần nhìn thấy mới, số người mới, khóa bị bỏ, dòng trùng, người mới).
    """
    prepared: list[dict[str, str]] = []
    for row in rows:
        item = _prepared_person(row)
        if item is not None:
            prepared.append(item)
    if not prepared:
        return 0, 0, [], [], []
    with db.people_write_lock:
        stored = db.people_by_keys(settings.db_path, [row["nameKey"] for row in prepared])
        by_username = db.people_by_usernames(
            settings.db_path, [row["username"] for row in prepared]
        )
        duplicate_rows: dict[str, dict[str, str]] = {}
        fresh: list[dict[str, str]] = []
        for row in prepared:
            existing = stored.get(row["nameKey"])
            owner = existing if existing and existing.get("contactName") and existing.get("username") else None
            if owner is None:
                match = by_username.get(row["username"].casefold())
                if match and match.get("contactName") and match.get("username"):
                    owner = match
            if owner is not None:
                duplicate_rows[owner["nameKey"]] = {
                    "nameKey": owner["nameKey"],
                    "name": row["name"],
                    "contactName": row["contactName"],
                    "username": row["username"],
                }
                continue
            fresh.append(row)
        sightings_saved = 0
        people_saved = 0
        skipped: list[str] = []
        if fresh:
            fresh_keys = {row["nameKey"] for row in fresh}
            before = {
                key: (row["name"], row["contactName"], row["username"])
                for key, row in stored.items()
                if key in fresh_keys
            }
            current = [row for key, row in stored.items() if key in fresh_keys]
            for row in fresh:
                items = complete_sightings([row])
                current, added = apply_novel(current, items)
                if added:
                    people_saved += 1
                    sightings_saved += added
            if sightings_saved:
                to_write = [
                    row
                    for row in current
                    if before.get(row["nameKey"])
                    != (row["name"], row.get("contactName") or "", row.get("username") or "")
                ]
                if to_write:
                    skipped = _write_people(to_write)
        duplicates = list(duplicate_rows.values())
        if duplicates:
            db.save_duplicates(settings.db_path, duplicates, utcnow())
    fresh_by_key: dict[str, dict[str, str]] = {}
    for row in fresh:
        fresh_by_key[row["nameKey"]] = row
    fresh_display = [_display_person(row) for row in fresh_by_key.values()]
    duplicate_display = [_display_person(row) for row in duplicates]
    return sightings_saved, people_saved, skipped, duplicate_display, fresh_display


@app.post("/v1/people/confirm")
def people_confirm(body: PeopleConfirmBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Ghi những dòng người dùng đã giữ. Cột đã có thì không ghi đè."""
    _auth(authorization)
    added, _people, _skipped, _duplicates, _fresh = _save_proposed(
        [row.model_dump() for row in body.rows]
    )
    items, _cursor = db.list_people_page(settings.db_path, limit=50)
    _total, ready = db.people_counts(settings.db_path)
    return {"ok": True, "saved": added, "count": ready, "items": items}


@app.get("/v1/server/stats")
def server_stats(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    base = db.transfer_summary(settings.db_path)
    base["proxies"] = db.proxy_summary(settings.db_path)
    return base


@app.get("/v1/proxies")
def proxies_list(
    authorization: str | None = Header(default=None),
    status: str | None = Query(default=None, description="live|die|unknown"),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    _auth(authorization)
    items = db.list_proxies(settings.db_path, status=status, limit=limit)
    return {"summary": db.proxy_summary(settings.db_path), "count": len(items), "items": items}


@app.get("/v1/proxies/die")
def proxies_die(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    """Dead proxies — highlighted on VPS homepage."""
    _auth(authorization)
    items = db.list_proxies(settings.db_path, status="die", limit=limit)
    return {"count": len(items), "items": items, "summary": db.proxy_summary(settings.db_path)}


@app.get("/v1/proxies/live.txt", response_class=PlainTextResponse)
def proxies_live_txt(authorization: str | None = Header(default=None)) -> PlainTextResponse:
    """Plaintext list of live proxies for scanner PCs."""
    _auth(authorization)
    items = db.list_proxies(settings.db_path, status="live", limit=2000)
    body = "\n".join(i["endpoint"] for i in items) + ("\n" if items else "")
    return PlainTextResponse(body)


@app.post("/v1/proxies/check")
async def proxies_check_now(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Trigger an immediate live/die sweep."""
    _auth(authorization)
    from control_plane.proxy_check import run_proxy_check_once

    result = await run_proxy_check_once()
    _remember_hub(
        "proxy_check",
        f"Check proxy: live={result.get('live', 0)} die={result.get('die', 0)}",
        detail=f"checked={result.get('checked', 0)} elapsed_ms={result.get('elapsed_ms', 0)}",
    )
    return result


class ProxiesUploadBody(BaseModel):
    """Replace static proxy list. One endpoint per line: host:port or host:port:user:pass."""

    text: str
    run_check: bool = True


@app.post("/v1/proxies/upload")
async def proxies_upload(
    body: ProxiesUploadBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    lines = [ln.strip() for ln in body.text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    path = settings.data_dir / "proxies_static.txt"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    settings.proxies_file = path
    # Also mirror into repo data path for scanner PCs that rsync later
    repo_data = Path(__file__).resolve().parents[1] / "data" / "proxies_static.txt"
    try:
        repo_data.write_text(
            "# Static proxies (synced from VPS hub)\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    inserted = db.upsert_proxy_endpoints(settings.db_path, lines, "static")
    result: dict[str, Any] = {"ok": True, "saved": inserted, "file": str(path)}
    try:
        if body.run_check:
            from control_plane.proxy_check import run_proxy_check_once

            result["check"] = await run_proxy_check_once()
    finally:
        check = result.get("check") or {}
        extra = f" · live={check.get('live', 0)} die={check.get('die', 0)}" if check else ""
        _remember_hub("proxy_upload", f"Cập nhật {inserted} proxy{extra}")
    return result


@app.get("/v1/comments")
def list_comments(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default=""),
) -> dict[str, Any]:
    _auth(authorization)
    items = db.list_comments(settings.db_path, limit=limit, offset=offset, q=q)
    return {"count": len(items), "offset": offset, "items": items}


@app.post("/v1/agents/register")
def register(
    body: RegisterBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    payload = body.model_dump()
    payload["last_seen_at"] = utcnow()
    payload["client_host"] = request.client.host if request.client else None
    db.upsert_agent(settings.db_path, body.machine_id, payload, status="connected")
    return {
        "ok": True,
        "machine_id": body.machine_id,
        "server": {
            "prefer_lan_updates": True,
            "manifest_path": "/v1/updates/manifest",
            "sync_path": "/v1/sync/comments",
            "max_upload_mb": settings.max_upload_mb,
        },
    }


@app.post("/v1/agents/heartbeat")
def heartbeat(body: HeartbeatBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    payload = body.model_dump()
    payload["last_seen_at"] = utcnow()
    status = "online" if body.poller_running else "agent_only"
    db.upsert_agent(settings.db_path, body.machine_id, payload, status=status)
    return {"ok": True, "server_time": utcnow()}


@app.get("/v1/agents")
def list_agents(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    items = db.list_agents(settings.db_path)
    return {"count": len(items), "agents": items}


def _latest_package() -> Path | None:
    files = sorted(settings.packages_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _video_worker_info() -> dict[str, str]:
    repo = Path(__file__).resolve().parents[1]
    dest = ensure_video_package(repo, settings.data_dir / "delivery")
    return {
        "version": str(VIDEO_WORKER_BUILD),
        "package_url": "/v1/updates/video-worker.zip",
        "sha256": _sha256(dest),
        "engine": "cpu",
    }


def _public_script(path: Path, *, bom: bool) -> PlainTextResponse:
    if not path.is_file():
        raise HTTPException(404, "missing")
    text = path.read_text(encoding="utf-8")
    if bom and not text.startswith("\ufeff"):
        text = "\ufeff" + text
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/v1/updates/manifest")
def updates_manifest(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """LAN manifest — agents should prefer this over GitHub for high bandwidth."""
    _auth(authorization)
    pkg = _latest_package()
    manifest_path = settings.data_dir / "update-manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Rewrite package URL to LAN endpoint when package exists
        if pkg is not None:
            data.setdefault("agent", {})
            data["agent"]["package_url"] = f"/v1/updates/packages/{pkg.name}"
            data["agent"]["sha256"] = _sha256(pkg)
            data["agent"].setdefault("version", "1.0.0")
        data["channel"] = data.get("channel") or "stable"
        data["transport"] = "lan-high-bandwidth"
        data["video_worker"] = _video_worker_info()
        return data

    if pkg is None:
        raise HTTPException(404, "No package on server. Put a .zip into control_data/packages/")

    root = Path(__file__).resolve().parents[1]
    agent_ver = root / "pc_agent" / "windows" / "VERSION"
    poller_ver = root / "VERSION"
    return {
        "schema_version": 1,
        "channel": "stable",
        "released_at": utcnow(),
        "transport": "lan-high-bandwidth",
        "agent": {
            "version": agent_ver.read_text(encoding="utf-8").strip() if agent_ver.exists() else "1.0.0",
            "package_url": f"/v1/updates/packages/{pkg.name}",
            "sha256": _sha256(pkg),
        },
        "poller": {
            "version": poller_ver.read_text(encoding="utf-8").strip() if poller_ver.exists() else "0.1.0",
            "notes": "Served from LAN PC server",
        },
        "rollout": {"force_update": False, "min_agent_version": "1.0.0"},
        "video_worker": _video_worker_info(),
    }


@app.get("/v1/updates/packages/{name}")
def download_package(
    name: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    _auth(authorization)
    # Prevent path traversal
    safe = Path(name).name
    path = settings.packages_dir / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "package not found")
    size = path.stat().st_size
    mid = request.headers.get("x-machine-id", "unknown")
    db.record_transfer(settings.db_path, mid, "package_download", size, 0, utcnow())
    return FileResponse(
        path,
        media_type="application/zip",
        filename=safe,
        headers={
            "Cache-Control": "no-cache",
            "Accept-Ranges": "bytes",
            "X-Transfer-Mode": "lan-high-bandwidth",
        },
    )


@app.get("/v1/updates/video-worker/manifest")
def video_worker_manifest(authorization: str | None = Header(default=None)) -> dict[str, str]:
    """Bản mã PC đọc video. Khác gói máy quét comment."""
    _auth(authorization)
    return _video_worker_info()


@app.get("/v1/updates/video-worker.zip")
def video_worker_zip(authorization: str | None = Header(default=None)) -> FileResponse:
    _auth(authorization)
    info = _video_worker_info()
    path = settings.data_dir / "delivery" / f"fb-poller-video-worker-{VIDEO_WORKER_BUILD}.zip"
    if info["version"] != str(VIDEO_WORKER_BUILD):
        raise HTTPException(500, "package version mismatch")
    if not path.is_file():
        raise HTTPException(404, "package missing")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={"Cache-Control": "no-cache"},
    )


_FAST_MODELS = ("vie", "eng")


def _fast_model_path(name: str) -> Path | None:
    folders: list[Path] = []
    forced = os.environ.get("CONTROL_TESSDATA_FAST", "").strip()
    if forced:
        folders.append(Path(forced))
    _exe, data = locate_tesseract()
    if data is not None:
        folders.append(data)
    for folder in folders:
        path = folder / f"{name}.traineddata"
        if path.is_file():
            return path
    return None


@app.get("/v1/updates/tessdata/{name}")
def fast_tessdata(name: str, authorization: str | None = Header(default=None)) -> FileResponse:
    """Bộ chữ máy chủ đang dùng, để PC đọc giống máy chủ."""
    _auth(authorization)
    if name not in _FAST_MODELS:
        raise HTTPException(404, "không có bộ chữ này")
    path = _fast_model_path(name)
    if path is None:
        raise HTTPException(404, "máy chủ chưa có bộ chữ này")
    return FileResponse(path, media_type="application/octet-stream", filename=f"{name}.traineddata")


@app.get("/cai-video-open.ps1")
def cai_video_open() -> PlainTextResponse:
    root = Path(__file__).resolve().parents[1]
    return _public_script(root / "pc_agent" / "windows" / "Open-FbPoller.ps1", bom=True)


@app.get("/cai-video.ps1")
def cai_video_installer() -> PlainTextResponse:
    root = Path(__file__).resolve().parents[1]
    return _public_script(root / "pc_agent" / "windows" / "Install-VideoWorker.ps1", bom=True)


@app.get("/cai-video-run.ps1")
def cai_video_runner() -> PlainTextResponse:
    root = Path(__file__).resolve().parents[1]
    return _public_script(root / "pc_agent" / "windows" / "Run-VideoWorker.ps1", bom=True)


@app.get("/cai-video-watchdog.py")
def cai_video_watchdog() -> PlainTextResponse:
    root = Path(__file__).resolve().parents[1]
    return _public_script(root / "pc_agent" / "video_watchdog.py", bom=False)


@app.post("/v1/updates/packages/upload")
async def upload_package(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Admin: push a release zip onto the server PC over LAN."""
    _auth(authorization)
    name = Path(file.filename or "package.zip").name
    if not name.endswith(".zip"):
        raise HTTPException(400, "only .zip supported")
    dest = settings.packages_dir / name
    started = time.time()
    nbytes = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(8 * 1024 * 1024)  # 8MB chunks
            if not chunk:
                break
            out.write(chunk)
            nbytes += len(chunk)
    ms = int((time.time() - started) * 1000)
    db.record_transfer(settings.db_path, "admin", "package_upload", nbytes, ms, utcnow())
    # Refresh local manifest helper file
    manifest = {
        "schema_version": 1,
        "channel": "stable",
        "released_at": utcnow(),
        "transport": "lan-high-bandwidth",
        "agent": {
            "version": utcnow()[:10].replace("-", "."),
            "package_url": f"/v1/updates/packages/{name}",
            "sha256": _sha256(dest),
        },
        "poller": {"version": utcnow()[:10].replace("-", "."), "notes": name},
        "rollout": {"force_update": False, "min_agent_version": "1.0.0"},
    }
    (settings.data_dir / "update-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    mbps = (nbytes * 8 / 1_000_000) / (ms / 1000) if ms > 0 else 0
    _remember_hub(
        "package_upload",
        f"Upload gói {name} ({nbytes} bytes)",
        detail=f"ms={ms} approx_mbps={round(mbps, 1)}",
    )
    return {"ok": True, "name": name, "bytes": nbytes, "ms": ms, "approx_mbps": round(mbps, 1)}


class ActionBody(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="note", max_length=40)
    detail: str | None = Field(default=None, max_length=2000)
    source: str = Field(default="manual", max_length=40)
    actor: str = Field(default="me", max_length=80)


@app.post("/v1/actions")
def create_action(body: ActionBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Remember one thing the operator just did."""
    _auth(authorization)
    kind = body.kind.strip() or "note"
    if kind not in _ACTION_KINDS:
        raise HTTPException(400, "kind must be note, cli, proxy_check, proxy_upload, or package_upload")
    summary = " ".join(body.summary.split())
    if not summary:
        raise HTTPException(400, "summary is empty")
    action_id = db.record_action(
        settings.db_path,
        at=utcnow(),
        actor=body.actor.strip() or "me",
        source=body.source.strip() or "manual",
        kind=kind,
        summary=summary,
        detail=body.detail,
    )
    return {"ok": True, "id": action_id}


@app.get("/v1/actions")
def get_actions(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    q: str = Query(default=""),
    kind: str | None = Query(default=None),
) -> dict[str, Any]:
    """List remembered operator actions, newest first."""
    _auth(authorization)
    if kind is not None and kind not in _ACTION_KINDS:
        raise HTTPException(400, "unknown kind")
    items = db.list_actions(settings.db_path, limit=limit, q=q, kind=kind)
    return {"count": len(items), "items": items}


@app.get("/v1/issues")
def get_issues(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=80, ge=1, le=200),
    q: str = Query(default=""),
) -> dict[str, Any]:
    """Nhật ký lỗi và vấn đề (trang chủ)."""
    from control_plane.issues import parse_detail

    _auth(authorization)
    raw = db.list_actions(settings.db_path, limit=limit, q=q, kind="issue")
    items: list[dict[str, Any]] = []
    for row in raw:
        meta = parse_detail(row.get("detail"))
        items.append(
            {
                "id": row.get("id"),
                "at": row.get("at"),
                "source": row.get("source") or meta.get("source") or "hub",
                "summary": row.get("summary"),
                "level": meta.get("level") or "warn",
                "jobId": meta.get("jobId"),
                "detail": meta.get("detail") or "",
            }
        )
    return {"count": len(items), "items": items}


class SeenBody(BaseModel):
    lines: list[str] = Field(default_factory=list, max_length=20)


def _store_seen(lines: list[str]) -> dict[str, Any]:
    title = "Chữ trên màn hình"
    recording_id = db.save_recording(
        settings.db_path,
        at=utcnow(),
        actor="me",
        title=title,
        steps=lines,
        events=[],
    )
    _remember_hub("screen", title, detail=" → ".join(lines[:12]) or None)
    steps = [{"t": 0.0, "caption": line} for line in lines]
    return {"ok": True, "id": recording_id, "title": title, "steps": steps, "count": len(steps)}


def _clean_seen_lines(raw_lines: list[str]) -> list[str]:
    lines: list[str] = []
    for raw in raw_lines:
        text = " ".join(str(raw).split())[:180]
        if text and text not in lines:
            lines.append(text)
        if len(lines) == 20:
            break
    return lines


async def _store_upload(file: UploadFile, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(prefix="fb-video-", suffix=suffix, delete=False) as tmp:
        dest = Path(tmp.name)
        total = 0
        limit = _upload_limit_bytes()
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if limit is not None and total > limit:
                    raise HTTPException(413, "file is too large")
                tmp.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        except OSError as error:
            dest.unlink(missing_ok=True)
            raise HTTPException(507, "Hết chỗ trống trên máy chủ.") from error
    return dest


@app.post("/v1/recordings/from-video")
async def recordings_from_video(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    save: int = Query(default=1, ge=0, le=1),
) -> dict[str, Any]:
    """Read an iPhone screen recording and list the names and words on screen."""
    _auth(authorization)
    suffix = Path(file.filename or "clip.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        suffix = ".mp4"
    dest = await _store_upload(file, suffix)
    try:
        _steps, people = analyze_screen_video(dest)
    except ScreenVideoError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        discard_video_work(dest)
    if not save:
        return {
            "ok": True,
            "id": None,
            "title": "Người đủ ba cột",
            "steps": [],
            "count": len(people),
            "people": people,
            "saved": 0,
            "savedPeople": 0,
        }
    stored = _store_seen([f"{len(people)} người"] if people else ["Đã đọc video"])
    added, people_saved, skipped, duplicates, fresh = _save_proposed(people)
    archive = _video_archive(people)
    _total, ready = db.people_counts(settings.db_path)
    stored["steps"] = []
    stored["count"] = len(fresh)
    stored["people"] = fresh
    stored["duplicates"] = duplicates
    stored["saved"] = added
    stored["savedPeople"] = people_saved
    stored["archive"] = archive
    stored["archiveCount"] = ready
    if skipped:
        stored["problems"] = [_STORE_FULL]
    return stored


def _commit_people(job: VideoJob, people: list[dict[str, str]], worker_id: str | None) -> bool:
    """Ghi dòng đủ ba cột. PC phụ chỉ ghi khi vẫn đang giữ video."""
    rows = list(people)
    if len(rows) > _VIDEO_RESULT_LIMIT:
        job.add_problem(f"Video có hơn {_VIDEO_RESULT_LIMIT} người. Phần sau chưa ghi.")
        rows = rows[:_VIDEO_RESULT_LIMIT]
    job.update(97, "Ghi kết quả")
    try:
        _store_seen([f"{len(rows)} người"] if rows else ["Đã đọc video"])
        _added, people_saved, skipped, duplicates, fresh = _save_proposed(rows)
        if skipped:
            job.add_problem(_STORE_FULL)
        archive = _video_archive(rows)
        _total, ready = db.people_counts(settings.db_path)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else "Chưa ghi được kết quả. Chọn lại video."
        if worker_id:
            job.fail_from_worker(worker_id, detail)
        else:
            job.fail(detail)
        raise
    if worker_id:
        return job.finish_from_worker(worker_id, fresh, people_saved, archive, ready, duplicates)
    job.finish(fresh, people_saved, archive, ready, duplicates)
    return True


# Video từ 4 phút trở lên, lúc có hai PC rảnh, thì chia đôi. Hai phần chồng nhau vài giây để không cắt mất dòng ở giữa.
_SPLIT_MIN_SECONDS = 240.0
_SPLIT_OVERLAP = 5.0


def _discard_job(job: VideoJob) -> None:
    path = job.source_path()
    job.discard()
    if path is not None:
        _forget_upload_path(path)


def _job_finished(job: VideoJob) -> None:
    """Việc thường xong thì xóa video. Một phần video xong thì xem việc cha đã đủ để ghép chưa."""
    if job.parent_id:
        parent = jobs.get(job.parent_id)
        if parent is not None:
            _merge_parts(parent)
        return
    if job.succeeded():
        _discard_job(job)


def _merge_parts(parent: VideoJob) -> None:
    """Đủ các phần thì ghép mọi lần nhìn thấy rồi mới áp quy tắc ghi, để danh bạ ở phần này ghép được tài khoản ở phần kia."""
    parts = jobs.parts(parent)
    live = [part for part in parts if part is not None]
    if len(live) != len(parts):
        parent.fail("Mất một phần video. Chọn lại video.")
        return
    if not all(part.done for part in live) or not parent.begin_merge():
        return
    for part in live:
        for text in part.public().get("problems", []):
            parent.add_problem(str(text))
        parent.note_learned(str(part.public().get("learned") or ""))
    failed = [part for part in live if part.error]
    if failed:
        parent.end_merge()
        parent.fail(failed[0].error)
        return
    sightings: list[dict[str, str]] = []
    seen: dict[str, list[int]] = {"pc": [0, 0], "hub": [0, 0]}
    for part in live:
        frames = part.remembered()
        for key in sorted(frames, key=float):
            sightings.extend(frames[key][1])
        for source, (words_seen, words_kept) in part.words().items():
            seen[source][0] += words_seen
            seen[source][1] += words_kept
    for source, (words_seen, words_kept) in seen.items():
        if words_seen or words_kept:
            parent.note_words(source, words_seen, words_kept)
    counts = reading_counts(sightings)
    parent.note_tally(counts["contacts"], counts["accounts"], counts["saved"])
    try:
        _commit_people(parent, propose_rows(sightings), None)
    except HTTPException:
        return
    if parent.succeeded():
        for part in live:
            _discard_job(part)
        _discard_job(parent)


def _split_video_job(parent: VideoJob, path: Path) -> bool:
    """Hai PC rảnh và video dài thì cắt hai phần cho hai PC. Không cắt được thì đọc cả video như cũ."""
    if video_helpers.helpers.idle_count() < 2:
        return False
    duration = video_duration(path)
    if duration is None or duration < _SPLIT_MIN_SECONDS:
        return False
    middle = duration / 2
    pieces = [(0.0, middle + _SPLIT_OVERLAP), (max(0.0, middle - _SPLIT_OVERLAP), None)]
    made: list[Path] = []
    for index, (start, end) in enumerate(pieces):
        dest = path.with_name(f"{path.stem}-phan{index + 1}.mp4")
        if not cut_video_part(path, dest, start, end):
            for done in made:
                discard_video_work(done)
            return False
        made.append(dest)
    children: list[VideoJob] = []
    for index, dest in enumerate(made):
        child = jobs.create()
        child.parent_id = parent.id
        child.part_label = f"Phần {index + 1}"
        child.update(8, "Đã nhận phần video")
        child.bind(dest)
        children.append(child)
    parent.set_parts([child.id for child in children])
    parent.bind(path)
    parent.update(10, "Chia video cho hai PC")
    for child in children:
        threading.Thread(target=_schedule_video_job, args=(child.id, child.source_path()), daemon=True).start()
    return True


def _finish_read(job: VideoJob, people: list[dict[str, str]], worker_id: str | None) -> bool:
    """Việc thường thì ghi người ngay. Một phần video thì chờ phần kia rồi ghép."""
    if job.parent_id:
        kept = job.finish_part(worker_id)
        if kept:
            _job_finished(job)
        return kept
    return _commit_people(job, people, worker_id)


def _reopen_job(job: VideoJob) -> bool:
    """Đọc nối việc đã lỗi. Video chia hai phần thì chỉ đọc lại phần lỗi."""
    if job.part_ids:
        if not job.reopen():
            return False
        for part in jobs.parts(job):
            if part is None or not part.done or not part.error:
                continue
            path = part.source_path()
            if path is not None and part.reopen():
                threading.Thread(target=_schedule_video_job, args=(part.id, path), daemon=True).start()
        return True
    path = job.source_path()
    if path is None or not job.reopen():
        return False
    threading.Thread(target=_schedule_video_job, args=(job.id, path), daemon=True).start()
    return True


def _job_public(job: VideoJob) -> dict[str, Any]:
    """Việc chia hai phần thì trang thấy phần trăm và việc đang làm của cả hai."""
    body = job.public()
    if not job.part_ids or job.done:
        return body
    parts = [part.public() for part in jobs.parts(job) if part is not None]
    if not parts:
        return body
    percent = sum(min(99, int(part.get("percent") or 0)) for part in parts) // len(parts)
    body["percent"] = max(int(body.get("percent") or 0), percent)
    labels = [
        f"Phần {index + 1} {int(part.get('percent') or 0)}%: {part.get('task') or ''}"
        for index, part in enumerate(parts)
    ]
    body["task"] = "Hai PC cùng đọc. " + ". ".join(labels)
    problems = list(body.get("problems") or [])
    for part in parts:
        for text in part.get("problems") or []:
            if text not in problems:
                problems.append(text)
    body["problems"] = problems[:20]
    learned = next((str(part["learned"]) for part in parts if part.get("learned")), "")
    if learned and not body.get("learned"):
        body["learned"] = learned
    for key in ("wordSeen", "wordKept", "hubWordSeen", "hubWordKept"):
        values = [int(part[key]) for part in parts if part.get(key) is not None]
        if values:
            body[key] = sum(values)
    return body


def _run_video_job(job_id: str, path: Path) -> None:
    """Đọc video ở luồng riêng để trang hỏi được phần trăm."""
    job = jobs.get(job_id)
    if job is None:
        discard_video_work(path)
        return
    try:
        if job.done:
            return
        _steps, people = analyze_screen_video(path, JobProgress(job))
        if job.done:
            return
        _finish_read(job, people, None)
    except ScreenVideoError as error:
        job.fail(str(error))
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else "Chưa ghi được kết quả. Chọn lại video."
        job.fail(detail)
    except Exception:
        job.fail("Không xử lý được video.")
    finally:
        if job.done:
            _job_finished(job)


def _watch_helper_job(job_id: str, path: Path) -> None:
    """PC đang giữ video. Hết hạn giữ thì hub đọc tiếp."""
    job = jobs.get(job_id)
    if job is None:
        discard_video_work(path)
        return
    while True:
        if job.done:
            _job_finished(job)
            return
        owner = job.owner_id()
        if not owner:
            if job.take_hub():
                _run_video_job(job_id, path)
                return
            time.sleep(0.2)
            continue
        if owner != "hub" and job.stale(video_helpers.LEASE_SECONDS):
            if job.release(owner):
                video_helpers.helpers.mark_idle(owner)
                job.add_problem("PC phụ ngừng gửi tiến trình. Máy chủ đọc tiếp.")
            continue
        time.sleep(0.4)


def _pc_wait_seconds() -> float:
    raw = os.environ.get("CONTROL_PC_WAIT_SEC", "45").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 45.0


def _wait_for_helper(job: VideoJob) -> None:
    """PC đang nối thì để PC nhận. PC đang bận thì video nằm chờ, hub không đọc chen."""
    job.update(4, "Chờ PC phụ nhận video")
    started = time.monotonic()
    while not job.owner_id() and not job.done:
        if not video_helpers.helpers.has_fresh():
            return
        if not video_helpers.helpers.has_idle():
            if time.monotonic() - started >= _pc_wait_seconds():
                return
            time.sleep(0.2)
            continue
        deadline = time.monotonic() + video_helpers.OFFER_SECONDS
        while (
            time.monotonic() < deadline
            and not job.owner_id()
            and not job.done
            and video_helpers.helpers.has_idle()
        ):
            time.sleep(0.1)
        if job.owner_id() or job.done:
            return
        if not video_helpers.helpers.has_fresh():
            return
        if time.monotonic() - started >= _pc_wait_seconds():
            try:
                from control_plane.issues import record_issue

                record_issue(
                    "PC phụ không nhận video trong thời gian chờ. Máy chủ sẽ đọc.",
                    source="schedule",
                    job_id=job.id,
                    level="info",
                )
            except Exception:
                pass
            return


def _schedule_video_job(job_id: str, path: Path) -> None:
    """PC đang nối thì video chờ PC. PC mất hoặc PC rảnh không nhận thì hub đọc."""
    job = jobs.get(job_id)
    if job is None:
        discard_video_work(path)
        return
    if video_helpers.helpers.has_fresh() and not job.owner_id():
        _wait_for_helper(job)
    if job.done:
        _job_finished(job)
        return
    owner = job.owner_id()
    if owner and owner != "hub":
        _watch_helper_job(job_id, path)
        return
    if job.take_hub():
        _run_video_job(job_id, path)
        return
    _watch_helper_job(job_id, path)


class HelperBeatBody(BaseModel):
    name: str = ""
    cpus: int = Field(default=1, ge=1, le=256)
    workerId: str = ""
    gpu: bool = False
    gpuName: str = ""
    workers: int = Field(default=0, ge=0, le=256)
    build: int = Field(default=0, ge=0, le=1_000_000)
    models: str = ""
    readerOk: bool | None = None
    readerNote: str = ""
    readerMode: str = ""
    hardware: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)


class WorkerJobBody(BaseModel):
    workerId: str


class WorkerProgressBody(BaseModel):
    workerId: str
    percent: int = 0
    task: str = ""
    problems: list[str] = Field(default_factory=list)
    learned: str = Field(default="", max_length=600)


class WorkerPeopleBody(BaseModel):
    workerId: str
    people: list[dict[str, str]] = Field(default_factory=list)
    seenContacts: int | None = None
    seenAccounts: int | None = None
    readSaved: int | None = None
    noText: bool = False
    wordSeen: int = Field(default=0, ge=0)
    wordKept: int = Field(default=0, ge=0)


class WorkerSamplesBody(BaseModel):
    workerId: str
    images: list[str] = Field(default_factory=list)


class WorkerFailBody(BaseModel):
    workerId: str
    error: str = ""


@app.post("/v1/video-workers/heartbeat")
async def video_worker_heartbeat(
    body: HelperBeatBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """PC phụ báo còn sống, số lõi, bản đang chạy, và kết quả đọc thử ảnh mẫu. Hub trả bản mới nhất."""
    _auth(authorization)
    worker_id = video_helpers.helpers.beat(
        body.workerId,
        body.name,
        body.cpus,
        body.gpu,
        body.gpuName,
        body.workers,
        body.build,
        body.models,
        body.readerOk,
        body.readerNote,
        body.readerMode,
        body.hardware,
        body.timing,
    )
    return {
        "ok": True,
        "workerId": worker_id,
        "build": VIDEO_WORKER_BUILD,
        "videoHelper": video_helpers.helpers.public(),
    }


@app.post("/v1/recordings/from-video/job")
async def recordings_from_video_job(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Nhận video và trả mã tiến trình ngay. Trang hỏi phần trăm sau đó."""
    _auth(authorization)
    suffix = Path(file.filename or "clip.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        suffix = ".mp4"
    dest = await _store_upload(file, suffix)
    return _begin_video_job(dest)


@app.post("/v1/recordings/jobs/claim")
async def claim_video_job(body: WorkerJobBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """PC kéo một video chưa ai giữ. Không có video thì jobId rỗng."""
    _auth(authorization)
    if not video_helpers.helpers.fresh(body.workerId) and not video_helpers.helpers.note(body.workerId):
        raise HTTPException(409, "PC phụ chưa nối")
    if not video_helpers.helpers.try_hold(body.workerId):
        return {"ok": True, "jobId": ""}
    spread = video_helpers.helpers.idle_count(skip=body.workerId) > 0
    job = jobs.claim_next(body.workerId, spread=spread)
    if job is None:
        video_helpers.helpers.mark_idle(body.workerId)
        return {"ok": True, "jobId": ""}
    return {"ok": True, "jobId": job.id, "resume": job.resume_public()}


def _prepare_and_schedule(job_id: str, path: Path) -> None:
    """Sắp mục lục lên đầu rồi mới cho PC nhận, để PC đọc được phần đã tải."""
    ready = faststart_video(path)
    job = jobs.get(job_id)
    if job is None:
        discard_video_work(ready)
        return
    if _split_video_job(job, ready):
        return
    job.bind(ready)
    job.update(8, "Đã nhận video")
    _schedule_video_job(job_id, ready)


def _begin_video_job(dest: Path) -> dict[str, Any]:
    job = jobs.create()
    job.update(4, "Đang sắp xếp video")
    threading.Thread(target=_prepare_and_schedule, args=(job.id, dest), daemon=True).start()
    return {"ok": True, "jobId": job.id}


def _byte_range(header: str, size: int) -> tuple[int, int] | None:
    text = header.strip().lower()
    if not text.startswith("bytes=") or "," in text or size <= 0:
        return None
    spec = text[6:]
    if spec.startswith("-"):
        try:
            count = int(spec[1:])
        except ValueError:
            return None
        if count <= 0:
            return None
        return max(0, size - count), size - 1
    start_text, _, end_text = spec.partition("-")
    try:
        start = int(start_text)
    except ValueError:
        return None
    if start < 0 or start >= size:
        return None
    if not end_text:
        return start, size - 1
    try:
        end = int(end_text)
    except ValueError:
        return None
    end = min(end, size - 1)
    if end < start:
        return None
    return start, end


_VIDEO_BLOCK = 64 * 1024


def _video_chunks(path: Path, job: Any, worker_id: str, start: int, length: int) -> Any:
    sent = 0
    with path.open("rb") as handle:
        handle.seek(start)
        while sent < length:
            block = handle.read(min(_VIDEO_BLOCK, length - sent))
            if not block:
                break
            sent += len(block)
            job.note_worker(worker_id)
            video_helpers.helpers.touch(worker_id)
            yield block


@app.get("/v1/recordings/jobs/{job_id}/video")
def download_job_video(
    job_id: str,
    workerId: str = "",
    range_header: str | None = Header(default=None, alias="range"),
    authorization: str | None = Header(default=None),
) -> Response:
    """PC đã nhận thì tải đúng file video đó. Đứt giữa chừng thì tải tiếp từ byte đã có."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    path = job.video_path(workerId)
    if path is None or not path.is_file():
        raise HTTPException(404, "không thấy video")
    size = path.stat().st_size
    start = 0
    end = max(0, size - 1)
    status = 200
    if range_header:
        if size <= 0:
            return Response(status_code=416, headers={"Content-Range": "bytes */0", "Accept-Ranges": "bytes"})
        found = _byte_range(range_header, size)
        if found is None:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        start, end = found
        status = 206
    length = 0 if size <= 0 else end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "no-transform",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if length <= 0:
        return Response(b"", status_code=status, media_type="application/octet-stream", headers=headers)
    return StreamingResponse(
        _video_chunks(path, job, workerId, start, length),
        status_code=status,
        media_type="application/octet-stream",
        headers=headers,
    )


_CHUNK_MAX = 8 * 1024 * 1024
_uploads: dict[str, dict[str, Any]] = {}
_uploads_lock = threading.Lock()


class UploadStartBody(BaseModel):
    name: str = "video.mp4"
    size: int = Field(ge=1, le=1024 * 1024 * 1024 * 1024)


def _upload_frontier(ranges: list[tuple[int, int]]) -> int:
    if ranges and ranges[0][0] == 0:
        return ranges[0][1]
    return 0


def _upload_overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < stop and end > begin for begin, stop in ranges)


def _upload_add(ranges: list[tuple[int, int]], start: int, end: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for begin, stop in sorted([*ranges, (start, end)]):
        if not merged or begin > merged[-1][1]:
            merged.append((begin, stop))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
    return merged


def _close_upload(item: dict[str, Any]) -> None:
    fd = item.get("fd")
    if not isinstance(fd, int) or fd < 0:
        return
    item["fd"] = -1
    try:
        os.close(fd)
    except OSError:
        return


def _upload_dir() -> Path:
    """Video đang gửi nằm trên đĩa của hub, cùng sổ ghi các khúc đã nhận. Hub khởi động lại vẫn gửi tiếp được."""
    folder = settings.data_dir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _upload_record(upload_id: str) -> Path:
    return _upload_dir() / f"{upload_id}.json"


def _save_upload(upload_id: str, item: dict[str, Any]) -> None:
    """Gọi khi đang giữ khóa của lần gửi."""
    record = {
        "path": str(item["path"]),
        "name": str(item.get("name") or ""),
        "size": int(item["size"]),
        "spans": sorted([int(start), int(end)] for start, end in item["spans"]),
        "created": float(item["created"]),
        "finished": bool(item.get("finished")),
        "jobId": str(item.get("jobId") or ""),
    }
    target = _upload_record(upload_id)
    temporary = target.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)


def _load_uploads() -> None:
    """Đọc lại các lần gửi dở sau khi hub khởi động lại. File video đã mất thì bỏ sổ ghi."""
    for record in sorted(_upload_dir().glob("*.json")):
        upload_id = record.stem
        if not upload_id.isalnum():
            continue
        with _uploads_lock:
            if upload_id in _uploads:
                continue
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            path = Path(str(data["path"]))
            size = int(data["size"])
            spans = {(int(start), int(end)) for start, end in data.get("spans") or []}
            created = float(data.get("created") or time.time())
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            record.unlink(missing_ok=True)
            continue
        if size <= 0 or not path.is_file():
            record.unlink(missing_ok=True)
            continue
        ranges: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            ranges = _upload_add(ranges, start, end)
        with _uploads_lock:
            _uploads.setdefault(
                upload_id,
                {
                    "path": path,
                    "name": str(data.get("name") or ""),
                    "size": size,
                    "fd": -1,
                    "ranges": ranges,
                    "spans": spans,
                    "created": created,
                    "finished": bool(data.get("finished")),
                    "jobId": str(data.get("jobId") or ""),
                    "lock": threading.Lock(),
                },
            )


def _forget_upload(upload_id: str, *, remove_video: bool) -> None:
    with _uploads_lock:
        item = _uploads.pop(upload_id, None)
    if item is not None:
        _close_upload(item)
        if remove_video:
            Path(item["path"]).unlink(missing_ok=True)
    _upload_record(upload_id).unlink(missing_ok=True)


def _forget_upload_path(path: Path) -> None:
    """Video đã đọc xong và đã xóa thì bỏ luôn sổ ghi lần gửi của nó."""
    with _uploads_lock:
        matches = [key for key, item in _uploads.items() if Path(item["path"]) == path]
    for key in matches:
        _forget_upload(key, remove_video=False)


def _upload_job_alive(item: dict[str, Any]) -> bool:
    """Tiến trình của lần gửi còn trong bộ nhớ, kể cả lúc đang sắp xếp video hay đã lỗi chờ đọc nối."""
    job_id = str(item.get("jobId") or "")
    if not job_id:
        return False
    job = jobs.get(job_id)
    return job is not None and not job.succeeded()


def _drop_old_uploads() -> None:
    now = time.time()
    stale: list[str] = []
    with _uploads_lock:
        for key, item in list(_uploads.items()):
            if now - float(item["created"]) > 6 * 3600 and not _upload_job_alive(item):
                stale.append(key)
    for key in stale:
        _forget_upload(key, remove_video=True)


def _upload_fd(item: dict[str, Any]) -> int:
    """Mở file khi khúc đầu tới sau lúc hub khởi động lại. Gọi khi đang giữ khóa của lần gửi."""
    fd = item.get("fd")
    if isinstance(fd, int) and fd >= 0:
        return fd
    try:
        opened = os.open(item["path"], os.O_RDWR)
    except OSError as error:
        raise HTTPException(404, "không thấy lần gửi") from error
    item["fd"] = opened
    return opened


@app.post("/v1/recordings/uploads")
def start_video_upload(body: UploadStartBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Mở một lần gửi video theo từng khúc, để mạng đứt thì gửi tiếp khúc chưa xong."""
    _auth(authorization)
    limit = _upload_limit_bytes()
    if limit is not None and body.size > limit:
        raise HTTPException(413, "file is too large")
    suffix = Path(body.name or "clip.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        suffix = ".mp4"
    _drop_old_uploads()
    upload_id = uuid.uuid4().hex
    path = _upload_dir() / f"fb-video-{upload_id}{suffix}"
    try:
        path.touch()
        fd = os.open(path, os.O_RDWR)
    except OSError as error:
        raise HTTPException(507, "Hết chỗ trống trên máy chủ.") from error
    item: dict[str, Any] = {
        "path": path,
        "name": " ".join(str(body.name or "").split())[:120],
        "size": body.size,
        "fd": fd,
        "ranges": [],
        "spans": set(),
        "created": time.time(),
        "finished": False,
        "jobId": "",
        "lock": threading.Lock(),
    }
    with item["lock"]:
        _save_upload(upload_id, item)
    with _uploads_lock:
        _uploads[upload_id] = item
    return {"ok": True, "uploadId": upload_id, "offset": 0}


def _live_upload(upload_id: str) -> dict[str, Any]:
    with _uploads_lock:
        item = _uploads.get(upload_id)
    if item is None:
        _load_uploads()
        with _uploads_lock:
            item = _uploads.get(upload_id)
    if item is None:
        raise HTTPException(404, "không thấy lần gửi")
    if not Path(item["path"]).is_file():
        _forget_upload(upload_id, remove_video=False)
        raise HTTPException(404, "không thấy lần gửi")
    return item


@app.get("/v1/recordings/uploads/{upload_id}")
def read_video_upload(upload_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Báo trang đã nhận những khúc nào, để trang gửi khúc sau mà không cần lời trả của lần gửi."""
    _auth(authorization)
    item = _live_upload(upload_id)
    lock = item["lock"]
    with lock:
        spans = sorted((int(start), int(end)) for start, end in item["spans"])
        return {
            "ok": True,
            "uploadId": upload_id,
            "offset": _upload_frontier(item["ranges"]),
            "size": int(item["size"]),
            "spans": [[start, end] for start, end in spans],
            "finished": bool(item.get("finished")),
            "jobId": str(item.get("jobId") or ""),
        }


@app.put("/v1/recordings/uploads/{upload_id}")
async def write_video_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(ge=0),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _auth(authorization)
    item = _live_upload(upload_id)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            announced = int(declared)
        except ValueError:
            announced = _CHUNK_MAX + 1
        if announced > _CHUNK_MAX:
            raise HTTPException(413, "chunk is too large")
    raw = await request.body()
    if len(raw) > _CHUNK_MAX:
        raise HTTPException(413, "chunk is too large")
    if not raw:
        raise HTTPException(400, "chunk trống")
    lock = item["lock"]
    with lock:
        size = int(item["size"])
        end = offset + len(raw)
        if end > size:
            raise HTTPException(400, "chunk vượt quá dung lượng video")
        spans: set[tuple[int, int]] = item["spans"]
        ranges: list[tuple[int, int]] = item["ranges"]
        if (offset, end) in spans:
            return JSONResponse({"ok": True, "offset": _upload_frontier(ranges), "end": end})
        if _upload_overlaps(ranges, offset, end):
            return JSONResponse({"ok": False, "offset": _upload_frontier(ranges)}, status_code=409)
        try:
            stored = os.pwrite(_upload_fd(item), raw, offset)
        except OSError as error:
            raise HTTPException(507, "Hết chỗ trống trên máy chủ.") from error
        if stored != len(raw):
            raise HTTPException(507, "Hết chỗ trống trên máy chủ.")
        spans.add((offset, end))
        item["ranges"] = _upload_add(ranges, offset, end)
        _save_upload(upload_id, item)
        return JSONResponse({"ok": True, "offset": _upload_frontier(item["ranges"]), "end": end})


@app.post("/v1/recordings/uploads/{upload_id}/finish")
def finish_video_upload(upload_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Nhận đủ thì mở tiến trình đọc. Tiến trình mất vì hub khởi động lại thì đọc lại từ file đã nhận."""
    _auth(authorization)
    item = _live_upload(upload_id)
    lock = item["lock"]
    with lock:
        frontier = _upload_frontier(item["ranges"])
        if frontier != int(item["size"]):
            return JSONResponse({"ok": False, "offset": frontier}, status_code=409)
        if item.get("finished") and _upload_job_alive(item):
            job = jobs.get(str(item["jobId"]))
            if job is not None and job.done and job.error:
                _reopen_job(job)
            return {"ok": True, "jobId": str(item["jobId"])}
        _close_upload(item)
        started = _begin_video_job(Path(item["path"]))
        item["finished"] = True
        item["jobId"] = str(started["jobId"])
        _save_upload(upload_id, item)
    return started


@app.post("/v1/recordings/jobs/{job_id}/samples")
def video_job_samples(
    job_id: str,
    body: WorkerSamplesBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """PC gửi vài khung đã chọn để trang chỉ hình đang đọc."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not job.note_worker(body.workerId):
        raise HTTPException(409, "PC phụ không giữ video này")
    decoded: list[bytes] = []
    for item in body.images[:3]:
        try:
            raw = base64.b64decode(item, validate=True)
        except (ValueError, TypeError):
            continue
        if raw and len(raw) <= 150_000:
            decoded.append(raw)
    job.note_samples("pc", decoded)
    parent = jobs.get(job.parent_id) if job.parent_id else None
    if parent is not None:
        parent.note_samples("pc", decoded)
    video_helpers.helpers.touch(body.workerId)
    return {"ok": True, "sampleCount": len(decoded)}


@app.get("/v1/recordings/jobs/{job_id}/samples/{index}")
def video_job_sample_image(
    job_id: str,
    index: int,
    authorization: str | None = Header(default=None),
) -> Response:
    """Một khung JPEG. Trang tải bằng token, không nhét ảnh vào lần hỏi tiến trình."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    data = job.sample_jpeg(index)
    if data is None:
        raise HTTPException(404, "không thấy khung")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.post("/v1/recordings/jobs/{job_id}/progress")
def video_job_progress(
    job_id: str,
    body: WorkerProgressBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not job.note_worker(body.workerId):
        raise HTTPException(409, "PC phụ không giữ video này")
    video_helpers.helpers.touch(body.workerId)
    if body.task:
        job.update(body.percent, body.task)
    for item in body.problems[:20]:
        job.add_problem(item)
    if body.learned:
        job.note_learned(body.learned)
    return job.public()


@app.post("/v1/recordings/jobs/{job_id}/complete")
def video_job_complete(
    job_id: str,
    body: WorkerPeopleBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """PC gửi dòng đã đọc. Hub ghi bảng, giữ cột đã có."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not job.note_worker(body.workerId):
        raise HTTPException(409, "PC phụ không giữ video này")
    if body.seenContacts is not None and body.seenAccounts is not None and body.readSaved is not None:
        job.note_tally(body.seenContacts, body.seenAccounts, body.readSaved)
    if (
        body.noText
        and not body.people
        and body.wordKept == 0
        and job.source_path() is not None
        and not job.reread_started()
    ):
        job.note_words("pc", body.wordSeen, body.wordKept)
        if job.mark_reread():
            job.add_problem("PC không đọc được chữ. Máy chủ đọc lại.")
            job.update(8, "Máy chủ đọc lại")
            job.release(body.workerId)
            video_helpers.helpers.mark_idle(body.workerId)
            return job.public()
    if body.wordSeen or body.wordKept:
        job.note_words("pc", body.wordSeen, body.wordKept)
    try:
        kept = _finish_read(job, list(body.people), body.workerId)
    finally:
        video_helpers.helpers.mark_idle(body.workerId)
    if not kept:
        raise HTTPException(409, "PC phụ không giữ video này")
    return job.public()


class WorkerCheckpointBody(BaseModel):
    workerId: str
    frames: list[dict[str, Any]] = Field(default_factory=list)
    people: list[dict[str, str]] | None = None
    seenContacts: int | None = None
    seenAccounts: int | None = None
    readSaved: int | None = None


@app.post("/v1/recordings/jobs/{job_id}/checkpoint")
def video_job_checkpoint(
    job_id: str,
    body: WorkerCheckpointBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """PC gửi khung đã đọc để lần bấm Tiếp tục không đọc lại."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not job.note_worker(body.workerId):
        raise HTTPException(409, "PC phụ không giữ video này")
    video_helpers.helpers.touch(body.workerId)
    for frame in body.frames[:80]:
        raw_t = frame.get("t")
        if isinstance(raw_t, bool) or not isinstance(raw_t, (int, float)):
            continue
        captions = frame.get("captions")
        sightings = frame.get("sightings")
        job.remember_frame(
            float(raw_t),
            list(captions) if isinstance(captions, list) else [],
            list(sightings) if isinstance(sightings, list) else [],
        )
    if body.people is not None:
        job.stage_people(body.people)
    if body.seenContacts is not None and body.seenAccounts is not None and body.readSaved is not None:
        job.note_tally(body.seenContacts, body.seenAccounts, body.readSaved)
    return {"ok": True}


@app.post("/v1/recordings/jobs/{job_id}/continue")
def continue_video_job(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Đọc nối video đã lỗi. Khung và người đã xong được giữ."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not _reopen_job(job):
        raise HTTPException(409, "Video này không đọc tiếp được.")
    return _job_public(job)


@app.post("/v1/recordings/jobs/{job_id}/fail")
def video_job_fail(
    job_id: str,
    body: WorkerFailBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Video hỏng thì dừng. Hub không tự đọc lại. Trang bấm Tiếp tục thì đọc nối."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not job.fail_from_worker(body.workerId, body.error or "Không đọc được video."):
        raise HTTPException(409, "PC phụ không giữ video này")
    video_helpers.helpers.mark_idle(body.workerId)
    _job_finished(job)
    return job.public()


@app.post("/v1/recordings/jobs/{job_id}/release")
def video_job_release(
    job_id: str,
    body: WorkerJobBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """PC trả video. Hub đọc tiếp."""
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    if not job.release(body.workerId):
        raise HTTPException(409, "PC phụ không giữ video này")
    video_helpers.helpers.mark_idle(body.workerId)
    job.add_problem("PC phụ trả video. Máy chủ đọc tiếp.")
    return job.public()


@app.get("/v1/recordings/jobs/{job_id}")
def recordings_job(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "không thấy tiến trình")
    return _job_public(job)


@app.post("/v1/recordings/from-frame")
async def recordings_from_frame(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Read one live screen frame while a recording is still running."""
    _auth(authorization)
    suffix = Path(file.filename or "khung.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "ảnh không đọc được")
    dest = await _store_upload(file, suffix)
    try:
        line = read_screen_image(dest)
    finally:
        dest.unlink(missing_ok=True)
    return {"ok": True, "line": line}


class LiveTextBody(BaseModel):
    text: str = ""
    source: str = "vision"


class FromLinkBody(BaseModel):
    text: str = ""
    url: str = ""
    source: str = "share"


_LIVE_SOURCES = frozenset({"vision", "system", "share"})


def _live_line(text: str) -> str:
    cleaned = clean_ocr(text)
    if not cleaned:
        return ""
    return seen_line(cleaned)[:180]


def _live_source(value: str) -> str:
    key = str(value or "").strip().casefold()
    if key in _LIVE_SOURCES:
        return key
    return "vision"


def _save_live_sighting(sighting: dict[str, str] | None) -> int:
    if sighting is None:
        return 0
    key = name_key(clean_name(sighting.get("name") or ""))
    if not key:
        return 0
    with db.people_write_lock:
        stored = list(db.people_by_keys(settings.db_path, [key]).values())
        _folded, added = apply_novel(stored, [sighting])
        if added:
            skipped = db.save_people(settings.db_path, _folded, utcnow())
            if key in skipped:
                return 0
        return added


def _ingest_live_text(text: str, source: str) -> dict[str, Any]:
    """Lưu chữ màn hình / dán / share. system và share giữ nguyên, không lọc OCR."""
    raw = " ".join(str(text or "").split())
    kind = _live_source(source)
    if not raw:
        return {"ok": True, "line": "", "saved": False, "people": 0, "username": "", "source": kind}
    sighting = profile_from_share(raw) or profile_from_line(raw)
    if kind in {"system", "share"}:
        line = exact_line(raw)
    else:
        line = _live_line(raw)
        username = (sighting or {}).get("username") or ""
        if username and username not in line:
            line = f"{line} {username}".strip()[:400]
    if not line:
        return {"ok": True, "line": "", "saved": False, "people": 0, "username": "", "source": kind}
    saved = db.append_screen_line(settings.db_path, at=utcnow(), line=line)
    added = _save_live_sighting(sighting)
    if saved:
        _remember_hub("screen", line)
    return {
        "ok": True,
        "line": line,
        "saved": saved,
        "people": added,
        "username": (sighting or {}).get("username") or "",
        "source": kind,
    }


@app.post("/v1/people/from-link")
def people_from_link(body: FromLinkBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Lấy @handle từ link / chữ dán / QR. Không đọc lại bằng Tesseract."""
    _auth(authorization)
    blob = " ".join(part for part in (body.url, body.text) if str(part or "").strip())
    return _ingest_live_text(blob, body.source or "share")


@app.post("/v1/screen/live")
def screen_live_post(body: LiveTextBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Store one line read from the iPhone screen while another app is open."""
    _auth(authorization)
    return _ingest_live_text(body.text, body.source)


@app.get("/v1/screen/live")
def screen_live_list(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=30, ge=1, le=50),
) -> dict[str, Any]:
    _auth(authorization)
    items = db.list_screen_lines(settings.db_path, limit=limit)
    return {"count": len(items), "items": items}


@app.post("/v1/recordings/seen")
def recordings_seen(body: SeenBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Save the lines collected during one continuous screen recording."""
    _auth(authorization)
    lines = _clean_seen_lines(body.lines)
    if not lines:
        raise HTTPException(400, "lines is empty")
    return _store_seen(lines)


@app.get("/v1/recordings")
def recordings_list(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    _auth(authorization)
    items = db.list_recordings(settings.db_path, limit=limit)
    return {"count": len(items), "items": items}


@app.get("/v1/recordings/{recording_id}")
def recordings_get(recording_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    item = db.get_recording(settings.db_path, recording_id)
    if item is None:
        raise HTTPException(404, "recording not found")
    return item



@app.post("/v1/sync/comments")
def sync_comments(body: SyncCommentsBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Bulk sync comments from scanner PCs → server PC database."""
    _auth(authorization)
    started = time.time()
    payload = [c.model_dump() for c in body.comments]
    inserted = db.upsert_comments(settings.db_path, body.machine_id, payload, utcnow())
    raw_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    ms = int((time.time() - started) * 1000)
    db.record_transfer(settings.db_path, body.machine_id, "comments_sync", raw_bytes, ms, utcnow())
    return {
        "ok": True,
        "received": len(body.comments),
        "inserted_new": inserted,
        "bytes": raw_bytes,
        "ms": ms,
    }
