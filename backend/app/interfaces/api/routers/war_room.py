"""War Room AI API + websocket stream."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.application.services.war_room import WarRoomService
from app.core.deps import CurrentUser, require_permissions
from war_room.realtime import get_war_room_hub

router = APIRouter(prefix="/war-room", tags=["war-room"])


class AgentRequest(BaseModel):
    agent_id: str | None = None
    name: str | None = None
    status: str = "available"
    active_calls: int = 0
    idle_sec: float = 0
    conversion_rate: float = 0


class CallRequest(BaseModel):
    call_id: str | None = None
    agent_id: str
    lead_id: str | None = None
    status: str = "live"
    duration_sec: int = 0
    sentiment: str = "neutral"
    objection: str | None = None
    buy_signal: float = 0


class KpiRequest(BaseModel):
    conversion_rate: float | None = None
    baseline_conversion: float | None = None
    queue_size: int | None = None
    avg_wait_sec: float | None = None


class AckRequest(BaseModel):
    alert_id: str = Field(...)


@router.post("/agents")
async def upsert_agent(
    body: AgentRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().upsert_agent(body.model_dump())}


@router.post("/calls")
async def upsert_call(
    body: CallRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().upsert_call(body.model_dump())}


@router.post("/kpi")
async def update_kpi(
    body: KpiRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().update_kpi(body.model_dump(exclude_none=True))}


@router.post("/alerts/scan")
async def scan_alerts(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().scan_alerts()}


@router.post("/alerts/ack")
async def ack_alert(
    body: AckRequest,
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().acknowledge_alert(body.alert_id)}


@router.get("/snapshot")
async def snapshot(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().snapshot()}


@router.get("/dashboard")
async def dashboard(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().dashboard()}


@router.get("/quality")
async def quality(
    user: CurrentUser = Depends(require_permissions("dashboard:read")),
) -> dict[str, Any]:
    return {"status": "ok", **WarRoomService().quality()}


@router.websocket("/ws")
async def war_room_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    hub = get_war_room_hub()
    queue = await hub.subscribe()
    try:
        snap = WarRoomService().snapshot()
        await websocket.send_json({"type": "snapshot", "payload": snap})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "payload": {"ok": True}})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(queue)
