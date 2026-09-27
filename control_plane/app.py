"""
Minimal control plane for unlimited Windows agents.
Run on a server/PC:  uvicorn control_plane.app:app --host 0.0.0.0 --port 8088

Agents POST /v1/agents/register and /v1/agents/heartbeat
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="fb-poller control plane", version="1.0.0")
_lock = Lock()
_agents: dict[str, dict[str, Any]] = {}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegisterBody(BaseModel):
    machine_id: str
    hostname: str | None = None
    agent_version: str | None = None
    poller_version: str | None = None
    os: str | None = None
    channel: str | None = None
    started_at: str | None = None


class HeartbeatBody(BaseModel):
    machine_id: str
    hostname: str | None = None
    agent_version: str | None = None
    poller_version: str | None = None
    poller_running: bool | None = None
    ts: str | None = None


def _auth(authorization: str | None, expected: str | None) -> None:
    # optional shared token via env CONTROL_TOKEN
    import os

    expected = expected or os.environ.get("CONTROL_TOKEN")
    if not expected:
        return
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": utcnow()}


@app.post("/v1/agents/register")
def register(
    body: RegisterBody,
    authorization: str | None = Header(default=None),
    x_machine_id: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization, None)
    mid = body.machine_id or x_machine_id
    if not mid:
        raise HTTPException(400, "machine_id required")
    with _lock:
        row = _agents.get(mid, {})
        row.update(body.model_dump())
        row["machine_id"] = mid
        row["registered_at"] = row.get("registered_at") or utcnow()
        row["last_seen_at"] = utcnow()
        row["status"] = "connected"
        _agents[mid] = row
    return {"ok": True, "machine_id": mid}


@app.post("/v1/agents/heartbeat")
def heartbeat(
    body: HeartbeatBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(authorization, None)
    with _lock:
        row = _agents.get(body.machine_id, {"machine_id": body.machine_id})
        row.update({k: v for k, v in body.model_dump().items() if v is not None})
        row["last_seen_at"] = utcnow()
        row["status"] = "online" if body.poller_running else "agent_only"
        _agents[body.machine_id] = row
    return {"ok": True}


@app.get("/v1/agents")
def list_agents(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(authorization, None)
    with _lock:
        items = list(_agents.values())
    return {"count": len(items), "agents": items}
