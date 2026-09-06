"""In-memory pub/sub hub for War Room websocket fanout."""
from __future__ import annotations

import asyncio
from typing import Any


class WarRoomHub:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self.last_snapshot: dict[str, Any] = {}

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        if self.last_snapshot:
            await q.put({"type": "snapshot", "payload": self.last_snapshot})
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(self, event: dict[str, Any]) -> int:
        delivered = 0
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                    q.put_nowait(event)
                    delivered += 1
                except Exception:
                    continue
        return delivered

    def publish_sync(self, event: dict[str, Any]) -> int:
        if event.get("type") == "snapshot":
            self.last_snapshot = event.get("payload") or {}
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.publish(event))
                return len(self._subscribers)
            return int(loop.run_until_complete(self.publish(event)))
        except RuntimeError:
            return 0


_hub: WarRoomHub | None = None


def get_war_room_hub() -> WarRoomHub:
    global _hub
    if _hub is None:
        _hub = WarRoomHub()
    return _hub
