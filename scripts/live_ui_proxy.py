#!/usr/bin/env python3
"""Phục vụ giao diện build mới, chuyển API về VPS live để iPhone chỉ cần mở link."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "control_plane" / "static"
UPSTREAM = os.environ.get("LIVE_UPSTREAM", "http://14.225.224.16:8088").rstrip("/")
TOKEN = os.environ.get("LIVE_TOKEN", "")
BUILD = os.environ.get("LIVE_UI_BUILD", "61")
HOST = os.environ.get("PROXY_HOST", "0.0.0.0")
PORT = int(os.environ.get("PROXY_PORT", "8920"))

LOCAL_PATHS = {
    "/",
    "/iphone",
    "/phone",
    "/cai-app",
    "/sw.js",
}

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def _token() -> str:
    if TOKEN:
        return TOKEN
    try:
        html = httpx.get(f"{UPSTREAM}/iphone", timeout=10.0).text
    except Exception:
        return ""
    match = re.search(r'hubToken\s*=\s*"([^"]+)"', html)
    return match.group(1) if match else ""


def _page(name: str) -> HTMLResponse:
    path = STATIC / name
    text = path.read_text(encoding="utf-8")
    text = text.replace("__IPHONE_BUILD__", str(BUILD))
    if name in {"iphone.html", "dashboard.html"}:
        text = text.replace("__CONTROL_TOKEN_JSON__", json.dumps(_token()))
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


@app.get("/")
def dashboard() -> HTMLResponse:
    return _page("dashboard.html")


@app.get("/iphone")
def iphone() -> HTMLResponse:
    return _page("iphone.html")


@app.get("/phone")
def phone() -> HTMLResponse:
    return _page("phone.html")


@app.get("/cai-app")
def cai_app() -> HTMLResponse:
    return _page("cai-app.html")


@app.get("/sw.js")
def service_worker() -> Response:
    path = STATIC / "upload-sw.js"
    return Response(
        path.read_bytes(),
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request) -> Response:
    target = f"{UPSTREAM}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection", "transfer-encoding"}
    }
    body = await request.body()
    async with httpx.AsyncClient(follow_redirects=False, timeout=None) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=headers,
            content=body,
        )
    excluded = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    out_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded
    }
    # Báo đúng build UI đang phục vụ, để /health không còn 53.
    if path == "health" and upstream.headers.get("content-type", "").startswith("application/json"):
        try:
            payload = upstream.json()
            payload["iphoneBuild"] = int(BUILD)
            payload["uiProxy"] = True
            return Response(
                json.dumps(payload),
                status_code=upstream.status_code,
                media_type="application/json",
                headers={"Cache-Control": "no-cache"},
            )
        except Exception:
            pass
    return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)


if __name__ == "__main__":
    print(f"UI proxy build={BUILD} upstream={UPSTREAM} on {HOST}:{PORT}", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
