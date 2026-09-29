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
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from control_plane import db
from control_plane.carddav import (
    carddav_password,
    issue_profile_ticket,
    list_contacts,
    profile_ticket_open,
    render_profile,
    serve_carddav,
)
from control_plane.danhba_store import (
    get_book,
    import_people,
    issue_vcard,
    list_books,
    mark_used,
    pack_vcard_zip,
    read_vcard,
    reconcile,
)
from control_plane.delivery import DANHBA_NAME, PACKAGE_NAME, ensure_danhba_package, ensure_package
from control_plane.people import apply_novel, complete_rows
from control_plane.version import DANHBA_BUILD, IPHONE_BUILD
from control_plane.recordings import (
    blanks_of,
    default_title,
    events_to_steps,
    prepare_clip_steps,
    resolve_scenario,
    sanitize_events,
    sanitize_parts,
    sanitize_steps,
    script_lines,
    steps_to_events,
)
from control_plane.settings import ROOT, settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="fb-poller high-bandwidth control plane", version="1.3.0")
app.add_middleware(
    GZipMiddleware,
    minimum_size=500,
    exclude_content_types=(
        *DEFAULT_EXCLUDED_CONTENT_TYPES,
        "application/x-apple-aspen-config",
    ),
)
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


_ACTION_KINDS = {"note", "cli", "proxy_check", "proxy_upload", "package_upload", "input_replay"}


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
        "danhbaBuild": DANHBA_BUILD,
        "danhba": "/danhba/",
        "delivery": "/tai",
    }


def _html(name: str) -> HTMLResponse:
    path = STATIC_DIR / name
    if not path.exists():
        return HTMLResponse("<p>Missing page.</p>", status_code=404)
    text = path.read_text(encoding="utf-8").replace("__IPHONE_BUILD__", str(IPHONE_BUILD))
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


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
    """App trên iPhone: lướt để lưu tên, điều khiển để ghi và làm lại thao tác."""
    return _html("iphone.html")


@app.get("/tai", response_class=HTMLResponse)
def delivery_page() -> HTMLResponse:
    """Đường truyền tải: mở app trên iPhone hoặc tải gói zip."""
    return _html("tai.html")


@app.get("/v1/delivery")
def delivery_info() -> dict[str, Any]:
    pkg = ensure_package(STATIC_DIR, settings.data_dir)
    body: dict[str, Any] = {
        "iphoneBuild": IPHONE_BUILD,
        "iphonePath": "/iphone",
        "installPath": "/tai",
        "danhbaBuild": DANHBA_BUILD,
        "danhbaPath": "/danhba/",
        "package": {
            "name": pkg.name,
            "bytes": pkg.stat().st_size,
            "sha256": _sha256(pkg),
            "path": "/tai/goi.zip",
        },
    }
    danhba = ensure_danhba_package(ROOT / "ios" / "DanhBa", settings.data_dir)
    if danhba is not None and danhba.is_file():
        body["danhba"] = {
            "name": danhba.name,
            "bytes": danhba.stat().st_size,
            "sha256": _sha256(danhba),
            "path": "/tai/danhba.zip",
        }
    return body


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


@app.get("/tai/danhba.zip")
def danhba_package() -> FileResponse:
    pkg = ensure_danhba_package(ROOT / "ios" / "DanhBa", settings.data_dir)
    if pkg is None or not pkg.is_file() or pkg.name != DANHBA_NAME:
        raise HTTPException(status_code=404, detail="package missing")
    return FileResponse(
        pkg,
        media_type="application/zip",
        filename=pkg.name,
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/danhba")
def danhba_open() -> RedirectResponse:
    """Đường cài app Danh bạ. Dấu / cuối để iPhone cập nhật đúng thư mục."""
    return RedirectResponse(url="/danhba/", status_code=302)


@app.get("/danhba/")
def danhba_app() -> HTMLResponse:
    """App Danh bạ chạy trên iPhone. Bản mới có hiệu lực lần mở sau."""
    path = STATIC_DIR / "danhba.html"
    if not path.exists():
        return HTMLResponse("<p>Missing page.</p>", status_code=404)
    text = path.read_text(encoding="utf-8").replace("__DANHBA_BUILD__", str(DANHBA_BUILD))
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


@app.get("/danhba/app.js")
def danhba_script() -> FileResponse:
    path = STATIC_DIR / "danhba-app.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="script missing")
    return FileResponse(
        path,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/danhba/sw.js")
def danhba_worker() -> HTMLResponse:
    path = STATIC_DIR / "danhba-sw.js"
    if not path.exists():
        return HTMLResponse("missing", status_code=404)
    text = path.read_text(encoding="utf-8").replace("__DANHBA_BUILD__", str(DANHBA_BUILD))
    return Response(
        content=text,
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/danhba/",
        },
    )


@app.get("/danhba/manifest.webmanifest")
def danhba_manifest() -> FileResponse:
    path = STATIC_DIR / "danhba-manifest.webmanifest"
    if not path.exists():
        raise HTTPException(status_code=404, detail="manifest missing")
    return FileResponse(
        path,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/danhba/nap", response_class=HTMLResponse)
def danhba_pc() -> HTMLResponse:
    """Trang máy tính: nạp tên và số lên hub."""
    path = STATIC_DIR / "danhba-nap.html"
    if not path.exists():
        return HTMLResponse("<p>Missing page.</p>", status_code=404)
    text = path.read_text(encoding="utf-8").replace("__DANHBA_BUILD__", str(DANHBA_BUILD))
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


class DanhBaImportBody(BaseModel):
    title: str = "Khach"
    text: str = ""


class DanhBaSyncBody(BaseModel):
    text: str = ""
    phones: list[str] = Field(default_factory=list)
    full: bool = False


def _danhba_books() -> dict[str, Any]:
    return list_books(settings.db_path)


@app.get("/v1/danhba/books")
def danhba_books(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    return _danhba_books()


@app.get("/v1/danhba/books/{book_id}")
def danhba_book(book_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    book = get_book(settings.db_path, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Không thấy danh bạ")
    return book


@app.post("/v1/danhba/books/{book_id}/use")
def danhba_use(book_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    book = mark_used(settings.db_path, book_id, utcnow())
    if book is None:
        raise HTTPException(status_code=404, detail="Không thấy danh bạ")
    return book


@app.post("/v1/danhba/import")
def danhba_import(body: DanhBaImportBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    if len(body.text) > 20_000_000:
        raise HTTPException(status_code=413, detail="Danh sách quá lớn")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Dán danh sách gồm tên ghi nhớ và số điện thoại")
    return import_people(settings.db_path, body.text, body.title, utcnow())


@app.post("/v1/danhba/sync")
def danhba_sync(body: DanhBaSyncBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Khớp danh bạ đã lưu với số đang có trên iPhone. Không ghi số lạ vào hub."""
    _auth(authorization)
    if len(body.text) > 20_000_000:
        raise HTTPException(status_code=413, detail="Danh sách quá lớn")
    try:
        return reconcile(settings.db_path, body.text, body.phones[:200_000], body.full, utcnow())
    except ValueError:
        raise HTTPException(status_code=400, detail="Không thấy số điện thoại để đối chiếu") from None


class DanhBaExportBody(BaseModel):
    bookId: str = ""


@app.post("/v1/danhba/xuat")
def danhba_issue_vcard(
    body: DanhBaExportBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Vé để Safari mở thẳng hộp thêm liên hệ. Không kèm số điện thoại."""
    _auth(authorization)
    issued = issue_vcard(settings.db_path, body.bookId.strip() or None, utcnow())
    if issued is None:
        raise HTTPException(status_code=404, detail="Chưa có danh bạ chờ")
    return issued


@app.get("/danhba/xuat/{ticket}.zip")
def danhba_vcard_zip(ticket: str) -> Response:
    """File zip chứa cả danh sách. iPhone mở từ app Tệp rồi hiện Thêm tất cả."""
    card = read_vcard(settings.db_path, ticket, utcnow())
    if card is None:
        raise HTTPException(status_code=404, detail="Liên kết nạp đã hết hạn")
    return Response(
        content=pack_vcard_zip(card["body"]),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="DanhBa.zip"',
            "Cache-Control": "no-store",
        },
    )


def _public_endpoint(request: Request) -> tuple[str, int, bool]:
    forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
    use_ssl = forwarded == "https"
    host_header = request.headers.get("host") or request.url.netloc
    if host_header.startswith("[") and "]" in host_header:
        end = host_header.find("]")
        host = host_header[: end + 1]
        rest = host_header[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else (443 if use_ssl else 80)
        return host, port, use_ssl
    if host_header.count(":") == 1:
        host, port_text = host_header.rsplit(":", 1)
        if port_text.isdigit():
            return host, int(port_text), use_ssl
    return host_header, 443 if use_ssl else 80, use_ssl


@app.post("/v1/danhba/dongbo")
def danhba_issue_profile(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Vé để Safari tải hồ sơ. iPhone tự đồng bộ số sau khi cài hồ sơ."""
    _auth(authorization)
    count = len(list_contacts(settings.db_path))
    if count == 0:
        raise HTTPException(status_code=404, detail="Dán số, rồi bấm Nạp lên iPhone.")
    ticket = issue_profile_ticket(settings.db_path, utcnow())
    return {"url": f"/danhba/dongbo/{ticket}.mobileconfig", "count": count}


@app.get("/danhba/dongbo/{ticket}.mobileconfig")
def danhba_profile(ticket: str, request: Request) -> Response:
    if not profile_ticket_open(settings.db_path, ticket, utcnow()):
        raise HTTPException(status_code=404, detail="Liên kết cài hồ sơ đã hết hạn")
    host, port, use_ssl = _public_endpoint(request)
    token = settings.token or os.environ.get("CONTROL_TOKEN") or ""
    xml = render_profile(host, port, use_ssl, carddav_password(token))
    return Response(
        content=xml,
        media_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": 'inline; filename="DanhBa.mobileconfig"',
            "Cache-Control": "no-store",
        },
    )


async def _carddav(request: Request, rest: str = "") -> Response:
    del rest
    status, headers, payload = serve_carddav(
        method=request.method,
        path=request.url.path,
        authorization=request.headers.get("authorization"),
        depth=request.headers.get("depth", "0"),
        body=await request.body(),
        db_path=settings.db_path,
        token=settings.token or os.environ.get("CONTROL_TOKEN") or "",
    )
    if request.method == "HEAD":
        payload = b""
    return Response(content=payload, status_code=status, headers=headers)


app.add_api_route(
    "/.well-known/carddav",
    _carddav,
    methods=["GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT"],
)
app.add_api_route(
    "/carddav",
    _carddav,
    methods=["GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT"],
)
app.add_api_route(
    "/carddav/{rest:path}",
    _carddav,
    methods=["GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT"],
)


@app.get("/danhba/version")
def danhba_version() -> dict[str, int]:
    return {"build": DANHBA_BUILD}


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


class RecordingBody(BaseModel):
    title: str = Field(default="", max_length=200)
    events: list[dict[str, Any]] = Field(min_length=1, max_length=3000)


class ParseBody(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=3000)


class StepsBody(BaseModel):
    title: str = Field(default="", max_length=200)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=400)


def _events_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = steps_to_events(steps)
    if not events:
        raise HTTPException(400, "no replayable steps")
    return events


@app.post("/v1/recordings")
def create_recording(body: RecordingBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Store a keyboard / pointer recording that can be replayed in order."""
    _auth(authorization)
    events = sanitize_events(body.events)
    if not events:
        raise HTTPException(400, "no replayable events")
    steps = script_lines(events)
    title = " ".join(body.title.split()) or default_title(events)
    recording_id = db.save_recording(
        settings.db_path,
        at=utcnow(),
        actor="me",
        title=title,
        steps=steps,
        events=events,
    )
    _remember_hub("input_replay", title, detail=" → ".join(steps[:20]) or None)
    return {"ok": True, "id": recording_id, "title": title, "steps": steps, "event_count": len(events)}


@app.post("/v1/recordings/parse")
def recordings_parse(body: ParseBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Collapse a raw recording into steps that can be edited."""
    _auth(authorization)
    steps = events_to_steps(sanitize_events(body.events))
    return {"steps": steps}


@app.post("/v1/recordings/compile")
def recordings_compile(body: StepsBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Turn edited steps into replay events without saving."""
    _auth(authorization)
    events = _events_from_steps(body.steps)
    title = " ".join(body.title.split()) or default_title(events)
    return {
        "title": title,
        "events": events,
        "lines": script_lines(events),
        "steps": sanitize_steps(body.steps),
        "event_count": len(events),
    }


@app.post("/v1/recordings/from-steps")
def recordings_from_steps(body: StepsBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Save an edited script as a new recording and keep the original."""
    _auth(authorization)
    events = _events_from_steps(body.steps)
    lines = script_lines(events)
    title = " ".join(body.title.split()) or default_title(events)
    recording_id = db.save_recording(
        settings.db_path,
        at=utcnow(),
        actor="me",
        title=title,
        steps=lines,
        events=events,
    )
    _remember_hub("input_replay", title, detail=" → ".join(lines[:20]) or None)
    return {"ok": True, "id": recording_id, "title": title, "steps": lines, "event_count": len(events)}


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


class ClipBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=400)


class ScenarioBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parts: list[dict[str, Any]] = Field(min_length=1, max_length=40)


def _clip_payload(name: str, steps: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    title = " ".join(name.split())
    if not title:
        raise HTTPException(400, "name is empty")
    try:
        cleaned = prepare_clip_steps(steps)
    except ValueError as exc:
        raise HTTPException(400, "blank used for text and control") from exc
    if not cleaned:
        raise HTTPException(400, "no replayable steps")
    return title, cleaned


def _scenario_payload(name: str, parts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    title = " ".join(name.split())
    if not title:
        raise HTTPException(400, "name is empty")
    cleaned = sanitize_parts(parts)
    if not cleaned:
        raise HTTPException(400, "scenario has no clips")
    found = db.clips_by_id(settings.db_path, [part["clipId"] for part in cleaned])
    missing = [part["clipId"] for part in cleaned if part["clipId"] not in found]
    if missing:
        raise HTTPException(400, "missing clip")
    return title, cleaned, found


def _public_clip(item: dict[str, Any]) -> dict[str, Any]:
    steps = item["steps"]
    try:
        blanks = blanks_of(steps)
    except ValueError:
        blanks = []
    return {
        "id": item["id"],
        "at": item["at"],
        "name": item["name"],
        "steps": steps,
        "captions": [str(step.get("caption") or "") for step in steps],
        "blanks": blanks,
        "scenarioCount": item.get("scenarioCount", 0),
        "scenarioNames": item.get("scenarioNames", []),
    }


@app.get("/v1/clips")
def clips_list(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=50),
) -> dict[str, Any]:
    _auth(authorization)
    items = [_public_clip(item) for item in db.list_clips(settings.db_path, limit=limit)]
    return {"count": len(items), "items": items}


@app.post("/v1/clips")
def clips_create(body: ClipBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Save a named clip. Scenarios keep a link to this id, not a frozen copy."""
    _auth(authorization)
    title, steps = _clip_payload(body.name, body.steps)
    clip_id = db.save_clip(settings.db_path, at=utcnow(), name=title, steps=steps)
    return _public_clip({"id": clip_id, "at": utcnow(), "name": title, "steps": steps, "scenarioCount": 0, "scenarioNames": []})


@app.get("/v1/clips/{clip_id}")
def clips_get(clip_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    item = db.get_clip(settings.db_path, clip_id)
    if item is None:
        raise HTTPException(404, "clip not found")
    listed = next((row for row in db.list_clips(settings.db_path) if row["id"] == clip_id), None)
    if listed:
        item["scenarioCount"] = listed["scenarioCount"]
        item["scenarioNames"] = listed["scenarioNames"]
    return _public_clip(item)


@app.put("/v1/clips/{clip_id}")
def clips_update(clip_id: int, body: ClipBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Replace a clip. Every scenario that names it plays the new steps."""
    _auth(authorization)
    title, steps = _clip_payload(body.name, body.steps)
    if not db.update_clip(settings.db_path, clip_id, at=utcnow(), name=title, steps=steps):
        raise HTTPException(404, "clip not found")
    item = db.get_clip(settings.db_path, clip_id)
    if item is None:
        raise HTTPException(404, "clip not found")
    listed = next((row for row in db.list_clips(settings.db_path) if row["id"] == clip_id), None)
    if listed:
        item["scenarioCount"] = listed["scenarioCount"]
        item["scenarioNames"] = listed["scenarioNames"]
    return _public_clip(item)


@app.delete("/v1/clips/{clip_id}")
def clips_delete(clip_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    result = db.delete_clip(settings.db_path, clip_id)
    if result == "missing":
        raise HTTPException(404, "clip not found")
    if result == "used":
        raise HTTPException(409, "clip is used by a scenario")
    return {"ok": True}


def _public_scenario(item: dict[str, Any], clips: dict[int, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    parts = []
    for part in item["parts"]:
        clip_id = int(part["clipId"])
        steps = (clips or {}).get(clip_id)
        blanks: list[dict[str, Any]] = []
        if steps is not None:
            try:
                blanks = blanks_of(steps)
            except ValueError:
                blanks = []
        parts.append(
            {
                "clipId": clip_id,
                "clipName": part.get("clipName") or "",
                "fills": part.get("fills") or {},
                "blanks": blanks,
            }
        )
    return {"id": item["id"], "at": item["at"], "name": item["name"], "parts": parts}


@app.get("/v1/scenarios")
def scenarios_list(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=50),
) -> dict[str, Any]:
    _auth(authorization)
    items = db.list_scenarios(settings.db_path, limit=limit)
    return {"count": len(items), "items": [_public_scenario(item) for item in items]}


@app.post("/v1/scenarios/compile")
def scenarios_compile(body: ScenarioBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Resolve linked clips with this scenario's fills. Does not save."""
    _auth(authorization)
    title, parts, clips = _scenario_payload(body.name, body.parts)
    try:
        steps = resolve_scenario(clips, parts)
    except KeyError as exc:
        raise HTTPException(400, "missing clip") from exc
    events = steps_to_events(steps)
    if not events:
        raise HTTPException(400, "no replayable steps")
    return {"title": title, "events": events, "lines": script_lines(events), "steps": steps, "event_count": len(events)}


@app.post("/v1/scenarios")
def scenarios_create(body: ScenarioBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    title, parts, clips = _scenario_payload(body.name, body.parts)
    scenario_id = db.save_scenario(settings.db_path, at=utcnow(), name=title, parts=parts)
    stored = db.get_scenario(settings.db_path, scenario_id)
    if stored is None:
        raise HTTPException(404, "scenario not found")
    return _public_scenario(stored, clips)


@app.get("/v1/scenarios/{scenario_id}")
def scenarios_get(scenario_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    item = db.get_scenario(settings.db_path, scenario_id)
    if item is None:
        raise HTTPException(404, "scenario not found")
    clips = db.clips_by_id(settings.db_path, [part["clipId"] for part in item["parts"]])
    return _public_scenario(item, clips)


@app.put("/v1/scenarios/{scenario_id}")
def scenarios_update(scenario_id: int, body: ScenarioBody, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    title, parts, clips = _scenario_payload(body.name, body.parts)
    if not db.update_scenario(settings.db_path, scenario_id, at=utcnow(), name=title, parts=parts):
        raise HTTPException(404, "scenario not found")
    stored = db.get_scenario(settings.db_path, scenario_id)
    if stored is None:
        raise HTTPException(404, "scenario not found")
    return _public_scenario(stored, clips)


@app.delete("/v1/scenarios/{scenario_id}")
def scenarios_delete(scenario_id: int, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization)
    if not db.delete_scenario(settings.db_path, scenario_id):
        raise HTTPException(404, "scenario not found")
    return {"ok": True}


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
