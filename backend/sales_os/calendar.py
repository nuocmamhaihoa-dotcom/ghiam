"""Calendar sync + call schedule helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


class CalendarService:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def sync(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        connector: str = "google_calendar",
    ) -> dict[str, Any]:
        incoming = events or []
        for ev in incoming:
            self._events.append({**ev, "connector": connector, "synced": True})
        return {
            "ok": True,
            "connector": connector,
            "records_in": len(incoming),
            "records_out": len(incoming),
            "stored": len(self._events),
        }

    def schedule_call(
        self,
        *,
        lead_id: str | None = None,
        agent_id: str | None = None,
        when: str | None = None,
        duration_min: int = 15,
        title: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        lead_id = lead_id or kwargs.get("lead_id") or "unknown"
        agent_id = agent_id or kwargs.get("agent_id") or "unknown"
        start = when or kwargs.get("when") or (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        event = {
            "event_id": f"evt_{uuid4().hex[:10]}",
            "type": "call",
            "lead_id": lead_id,
            "agent_id": agent_id,
            "start": start,
            "duration_min": duration_min,
            "title": title or f"Call lead {lead_id}",
            "status": "scheduled",
        }
        self._events.append(event)
        return {"ok": True, "event": event}

    def list_events(self) -> list[dict[str, Any]]:
        return list(self._events)


__all__ = ["CalendarService"]
