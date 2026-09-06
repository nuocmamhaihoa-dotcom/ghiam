"""Application service for War Room AI."""
from __future__ import annotations

from typing import Any

from war_room import WarRoomEngine, get_war_room_engine


class WarRoomService:
    def __init__(self, engine: WarRoomEngine | None = None) -> None:
        self._engine = engine or get_war_room_engine()

    def upsert_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        return self._engine.upsert_agent(agent)

    def upsert_call(self, call: dict[str, Any]) -> dict[str, Any]:
        return self._engine.upsert_call(call)

    def update_kpi(self, kpi: dict[str, Any]) -> dict[str, Any]:
        return self._engine.update_kpi(kpi)

    def scan_alerts(self) -> dict[str, Any]:
        return self._engine.scan_alerts()

    def acknowledge_alert(self, alert_id: str) -> dict[str, Any]:
        return self._engine.acknowledge_alert(alert_id)

    def snapshot(self) -> dict[str, Any]:
        return self._engine.snapshot()

    def dashboard(self) -> dict[str, Any]:
        return self._engine.dashboard()

    def quality(self) -> dict[str, Any]:
        return self._engine.quality_snapshot()
