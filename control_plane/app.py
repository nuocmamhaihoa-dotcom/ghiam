"""
High-bandwidth LAN control plane — run on a PC server.

Features:
- Agent register / heartbeat (unlimited PCs)
- Serve update manifest + packages over LAN (prefer vs Internet)
- Bulk comment sync from scanner PCs
- GZip, large uploads, keep-alive friendly defaults
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from control_plane import db
from control_plane.delivery import PACKAGE_NAME, ensure_package
from control_plane.people import apply_novel, clean_name, clean_username, complete_rows, fold_name, profile_from_line
from control_plane.version import IPHONE_BUILD
from control_plane.screen_steps import ScreenVideoError, analyze_screen_video, clean_ocr, read_screen_image, seen_line
from control_plane.settings import settings

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


class LimitUploadSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        cl = request.headers.get("content-length")
        if cl and int(cl) > settings.max_upload_mb * 1024 * 1024:
            return JSONResponse({"detail": "upload too large"}, status_code=413)
        return await call_next(request)


app.add_middleware(LimitUploadSizeMiddleware)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACTION_KINDS = {"note", "cli", "proxy_check", "proxy_upload", "package_upload", "screen"}


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
    # Import proxy list into DB immediately; live/die loop runs in background
    from control_plane.proxy_check import load_proxy_lines, start_background_checker

    lines = load_proxy_lines(settings.proxies_file)
    if lines:
        db.upsert_proxy_endpoints(settings.db_path, lines, "static")
        cd_copy = settings.data_dir / "proxies_static.txt"
        if not cd_copy.exists():
            cd_copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    }


def _html(name: str, status_code: int = 200) -> HTMLResponse:
    path = STATIC_DIR / name
    if not path.exists():
        return HTMLResponse("<p>Missing page.</p>", status_code=404)
    text = path.read_text(encoding="utf-8").replace("__IPHONE_BUILD__", str(IPHONE_BUILD))
    if name == "iphone.html":
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


@app.get("/cai-app", response_class=HTMLResponse)
def install_ios_app() -> HTMLResponse:
    """Cách cài app đọc chữ trên ứng dụng khác."""
    return _html("cai-app.html")


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


@app.get("/v1/people")
def people_list(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    rows = db.list_people(settings.db_path)
    ready = complete_rows(rows)
    return {"count": len(ready), "items": ready, "known": rows}


@app.post("/v1/people/sightings")
def people_sightings(
    body: SightingsBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization)
    stored = db.list_people(settings.db_path)
    folded, added = apply_novel(stored, [item.model_dump() for item in body.items])
    if added:
        db.save_people(settings.db_path, folded, utcnow())
    ready = complete_rows(folded)
    return {"count": len(ready), "items": ready, "saved": added, "known": folded}


@app.post("/v1/people/confirm")
def people_confirm(body: PeopleConfirmBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Ghi những dòng người dùng đã giữ. Cột đã có thì không ghi đè."""
    _auth(authorization)
    items: list[dict[str, str]] = []
    for row in body.rows[:40]:
        name = clean_name(row.name)
        contact_name = clean_name(row.contactName)
        username = clean_username(row.username)
        if len(name) < 2 or not contact_name or not username:
            continue
        if fold_name(contact_name) == fold_name(name):
            continue
        items.append({"kind": "contact", "name": name, "contactName": contact_name, "username": ""})
        items.append({"kind": "profile", "name": name, "contactName": "", "username": username})
    stored = db.list_people(settings.db_path)
    folded, added = apply_novel(stored, items)
    if added:
        db.save_people(settings.db_path, folded, utcnow())
    ready = complete_rows(folded)
    return {"ok": True, "saved": added, "count": len(ready), "items": ready}


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
        limit = settings.max_upload_mb * 1024 * 1024
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file is too large")
            tmp.write(chunk)
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
        steps, people = analyze_screen_video(dest)
    except ScreenVideoError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        dest.unlink(missing_ok=True)
    lines = [str(step["caption"]) for step in steps]
    if not save:
        return {
            "ok": True,
            "id": None,
            "title": "Chữ trên màn hình",
            "steps": steps,
            "count": len(steps),
            "people": people,
        }
    stored = _store_seen(lines)
    stored["people"] = people
    return stored


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


def _live_line(text: str) -> str:
    cleaned = clean_ocr(text)
    if not cleaned:
        return ""
    return seen_line(cleaned)[:180]


@app.post("/v1/screen/live")
def screen_live_post(body: LiveTextBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Store one line read from the iPhone screen while another app is open."""
    _auth(authorization)
    line = _live_line(body.text)
    if not line:
        return {"ok": True, "line": "", "saved": False, "people": 0}
    saved = db.append_screen_line(settings.db_path, at=utcnow(), line=line)
    added = 0
    sighting = profile_from_line(line)
    if sighting is not None:
        stored = db.list_people(settings.db_path)
        folded, added = apply_novel(stored, [sighting])
        if added:
            db.save_people(settings.db_path, folded, utcnow())
    if saved:
        _remember_hub("screen", line)
    return {"ok": True, "line": line, "saved": saved, "people": added}


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
