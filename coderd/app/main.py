from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from app import db
from app.hub import Hub

ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).resolve().parent / "static" / "index.html"
EVENT_KINDS = {"text", "tool", "done", "error", "cancelled"}


def db_path() -> Path:
    raw = os.environ.get("CODERD_DB")
    if raw:
        return Path(raw)
    return ROOT / "var" / "coderd.db"


def bare_path() -> Path:
    raw = os.environ.get("GIT_BARE")
    if raw:
        return Path(raw)
    return ROOT / "var" / "git" / "workspace.git"


def require_user(request: Request) -> None:
    expected = os.environ.get("AUTH_TOKEN", "")
    header = request.headers.get("authorization", "")
    if not expected or header != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="thiếu token")


def require_worker(request: Request) -> None:
    expected = os.environ.get("WORKER_TOKEN", "")
    header = request.headers.get("authorization", "")
    if not expected or header != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="thiếu token worker")


def create_app() -> FastAPI:
    application = FastAPI(title="coderd", docs_url=None, redoc_url=None, openapi_url=None)
    database = db_path()
    db.init_db(database)
    db.detach_all(database)
    application.state.database = database
    application.state.hub = Hub(database)

    @application.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/", response_class=HTMLResponse)
    def home() -> str:
        return PAGE.read_text(encoding="utf-8")

    @application.get("/v1/sessions")
    def get_sessions(request: Request) -> list[dict]:
        require_user(request)
        return db.list_sessions(database)

    @application.post("/v1/sessions")
    def create_session(request: Request) -> dict:
        require_user(request)
        return db.create_session(database)

    @application.get("/v1/sessions/{session_id}/messages")
    def get_messages(session_id: str, request: Request) -> list[dict]:
        require_user(request)
        if db.get_session(database, session_id) is None:
            raise HTTPException(status_code=404, detail="không có phiên")
        return db.list_messages(database, session_id)

    @application.post("/v1/sessions/{session_id}/messages")
    async def post_message(session_id: str, request: Request) -> dict:
        require_user(request)
        if db.get_session(database, session_id) is None:
            raise HTTPException(status_code=404, detail="không có phiên")
        body = await request.json()
        text = str(body.get("text") or "")
        if not text.strip():
            raise HTTPException(status_code=400, detail="tin trống")
        if len(text) > 32000:
            raise HTTPException(status_code=400, detail="tin quá dài")
        message = db.add_message(database, session_id, text)
        await application.state.hub.dispatch()
        current = db.get_message(database, message["id"])
        return current or message

    @application.get("/v1/workers")
    def get_workers(request: Request) -> list[dict]:
        require_user(request)
        return db.list_workers(database)

    @application.post("/v1/messages/{message_id}/cancel")
    async def cancel_message(message_id: str, request: Request) -> dict:
        require_user(request)
        message = db.get_message(database, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="không có tin")
        if message["state"] in db.TERMINAL:
            return message
        db.set_message_state(database, message_id, "cancelled")
        hub: Hub = application.state.hub
        if hub.ws is not None and hub.running_id == message_id:
            try:
                await hub.ws.send_json({"type": "cancel", "message_id": message_id})
            except Exception:
                pass
            hub.running_id = None
        return db.get_message(database, message_id) or message

    @application.post("/v1/workspace/bundle")
    async def receive_bundle(request: Request) -> dict[str, bool]:
        require_worker(request)
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="bundle trống")
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="bundle quá lớn")
        bare = bare_path()
        bare.parent.mkdir(parents=True, exist_ok=True)
        if not (bare / "HEAD").exists():
            subprocess.run(["git", "init", "--bare", str(bare)], check=True)
            subprocess.run(
                ["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"],
                check=True,
            )
        handle, name = tempfile.mkstemp(suffix=".bundle")
        try:
            os.write(handle, data)
            os.close(handle)
            handle = -1
            subprocess.run(
                ["git", "--git-dir", str(bare), "fetch", name, "main:main"],
                check=True,
                capture_output=True,
            )
        finally:
            if handle >= 0:
                os.close(handle)
            os.unlink(name)
        return {"ok": True}

    @application.websocket("/v1/workers/connect")
    async def worker_socket(socket: WebSocket) -> None:
        await socket.accept()
        hub: Hub = application.state.hub
        hostname = ""
        try:
            hello = await socket.receive_json()
            expected = os.environ.get("WORKER_TOKEN", "")
            if hello.get("type") != "hello" or not expected or hello.get("token") != expected:
                await socket.send_json({"type": "reject", "reason": "unauthorized"})
                await socket.close()
                return
            hostname = str(hello.get("hostname") or "").strip().replace("\n", "")[:200]
            if not hostname:
                await socket.send_json({"type": "reject", "reason": "hostname"})
                await socket.close()
                return
            async with hub.lock:
                if hub.ws is not None:
                    await socket.send_json({"type": "reject", "reason": "already-attached"})
                    await socket.close()
                    return
                hub.ws = socket
                hub.hostname = hostname
            db.upsert_worker(
                database,
                hostname,
                int(hello.get("cpu") or 0),
                int(hello.get("ram") or 0),
                int(hello.get("ram_free") or 0),
                True,
            )
            await socket.send_json({"type": "hello_ok"})
            await hub.dispatch()
            while True:
                incoming = await socket.receive_json()
                kind = incoming.get("type")
                if kind == "heartbeat":
                    db.upsert_worker(
                        database,
                        hostname,
                        int(incoming.get("cpu") or 0),
                        int(incoming.get("ram") or 0),
                        int(incoming.get("ram_free") or 0),
                        True,
                    )
                elif kind == "event":
                    event_kind = str(incoming.get("kind") or "")
                    if event_kind not in EVENT_KINDS:
                        continue
                    await hub.finish(
                        str(incoming.get("message_id") or ""),
                        event_kind,
                        str(incoming.get("payload") or ""),
                    )
        except Exception:
            pass
        finally:
            await hub.detach(socket)

    @application.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return application


app = create_app()
