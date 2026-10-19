from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import WebSocket

from app import db

TERMINAL_KINDS = {"done": "done", "error": "error", "cancelled": "cancelled"}


class Hub:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.ws: WebSocket | None = None
        self.hostname: str | None = None
        self.running_id: str | None = None
        self.lock = asyncio.Lock()

    async def dispatch(self) -> None:
        async with self.lock:
            if self.ws is None or self.running_id:
                return
            message = db.oldest_queued(self.database)
            if message is None:
                return
            if not db.set_message_state(self.database, message["id"], "running"):
                return
            self.running_id = message["id"]
            socket = self.ws
            payload = {
                "type": "assign",
                "message_id": message["id"],
                "session_id": message["session_id"],
                "text": message["body"],
                "history": db.prior_dialogue(self.database, message["session_id"], message["id"]),
                "workspace_id": "default",
            }
        try:
            await socket.send_json(payload)
        except Exception:
            async with self.lock:
                if self.running_id == payload["message_id"]:
                    self.running_id = None
            db.set_message_state(self.database, payload["message_id"], "queued")

    async def finish(self, message_id: str, kind: str, payload: str) -> None:
        db.add_event(self.database, message_id, kind, payload)
        state = TERMINAL_KINDS.get(kind)
        if state:
            db.set_message_state(self.database, message_id, state)
            async with self.lock:
                if self.running_id == message_id:
                    self.running_id = None
            await self.dispatch()

    async def detach(self, socket: WebSocket) -> None:
        async with self.lock:
            if self.ws is not socket:
                return
            self.ws = None
            running = self.running_id
            self.running_id = None
            hostname = self.hostname
            self.hostname = None
        if running:
            message = db.get_message(self.database, running)
            if message and message["state"] == "running":
                db.set_message_state(self.database, running, "queued")
        if hostname:
            db.set_attached(self.database, hostname, False)
