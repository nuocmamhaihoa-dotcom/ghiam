"""War Room AI engine — live ops snapshot, alerts, realtime fanout."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from war_room.alerts import detect_alerts
from war_room.quality import evaluate_quality
from war_room.realtime import get_war_room_hub
from war_room.store import WarRoomStore
from war_room.types import new_id, now_iso


class WarRoomEngine:
    def __init__(self, store: WarRoomStore | None = None) -> None:
        self.store = store or WarRoomStore()
        self.hub = get_war_room_hub()
        self._agents: dict[str, dict[str, Any]] = {}
        self._calls: dict[str, dict[str, Any]] = {}
        self._kpi: dict[str, Any] = {
            "conversion_rate": 0.22,
            "baseline_conversion": 0.25,
            "queue_size": 8,
            "avg_wait_sec": 20,
        }

    def upsert_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(agent.get("agent_id") or new_id("agent"))
        row = {
            "agent_id": agent_id,
            "name": agent.get("name") or agent_id,
            "status": agent.get("status") or "available",
            "active_calls": int(agent.get("active_calls") or 0),
            "idle_sec": float(agent.get("idle_sec") or 0),
            "conversion_rate": float(agent.get("conversion_rate") or 0),
            "last_seen_at": now_iso(),
        }
        self._agents[agent_id] = row
        event = {"type": "agent_upsert", "ts": now_iso(), "payload": row}
        self.store.append_event(event)
        self.hub.publish_sync({"type": "agent_upsert", "payload": row})
        return {"ok": True, "agent": row}

    def upsert_call(self, call: dict[str, Any]) -> dict[str, Any]:
        call_id = str(call.get("call_id") or new_id("call"))
        row = {
            "call_id": call_id,
            "agent_id": str(call.get("agent_id") or ""),
            "lead_id": str(call.get("lead_id") or ""),
            "status": call.get("status") or "live",
            "duration_sec": int(call.get("duration_sec") or 0),
            "sentiment": call.get("sentiment") or "neutral",
            "objection": call.get("objection"),
            "buy_signal": float(call.get("buy_signal") or 0),
            "started_at": call.get("started_at") or now_iso(),
        }
        self._calls[call_id] = row
        event = {"type": "call_upsert", "ts": now_iso(), "payload": row}
        self.store.append_event(event)
        self.hub.publish_sync({"type": "call_upsert", "payload": row})
        return {"ok": True, "call": row}

    def update_kpi(self, kpi: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        for key in ("conversion_rate", "baseline_conversion", "queue_size", "avg_wait_sec"):
            if kpi.get(key) is not None:
                updates[key] = kpi[key]
        if updates:
            if "conversion_rate" in updates:
                updates["conversion_rate"] = float(updates["conversion_rate"])
            if "baseline_conversion" in updates:
                updates["baseline_conversion"] = float(updates["baseline_conversion"])
            if "queue_size" in updates:
                updates["queue_size"] = int(updates["queue_size"])
            if "avg_wait_sec" in updates:
                updates["avg_wait_sec"] = float(updates["avg_wait_sec"])
            self._kpi.update(updates)
        event = {"type": "kpi_update", "ts": now_iso(), "payload": dict(self._kpi)}
        self.store.append_event(event)
        self.hub.publish_sync(event)
        return {"ok": True, "kpi": dict(self._kpi)}

    def scan_alerts(self) -> dict[str, Any]:
        alerts = detect_alerts(
            agents=list(self._agents.values()),
            calls=list(self._calls.values()),
            kpi=self._kpi,
        )
        payloads: list[dict[str, Any]] = []
        for alert in alerts:
            payload = alert.to_dict()
            self.store.append_alert(payload)
            payloads.append(payload)
            self.hub.publish_sync({"type": "alert", "payload": payload})
        self._refresh_metrics(extra_alerts=len(payloads))
        return {"ok": True, "alerts": payloads, "count": len(payloads)}

    def acknowledge_alert(self, alert_id: str) -> dict[str, Any]:
        rows = self.store.list_alerts(limit=1000)
        found = None
        for row in rows:
            if row.get("alert_id") == alert_id:
                row["acknowledged"] = True
                found = row
                break
        if not found:
            return {"ok": False, "error": "alert_not_found"}
        self.store.append_alert(found)
        self.hub.publish_sync({"type": "alert_ack", "payload": found})
        return {"ok": True, "alert": found}

    def snapshot(self) -> dict[str, Any]:
        agents = list(self._agents.values())
        calls = [c for c in self._calls.values() if c.get("status") in {"ringing", "live", "hold"}]
        alerts = [a for a in self.store.list_alerts(limit=100) if not a.get("acknowledged")]
        online = sum(1 for a in agents if a.get("status") != "offline")
        avg_buy = (
            sum(float(c.get("buy_signal") or 0) for c in calls) / max(1, len(calls)) if calls else 0.0
        )
        data = {
            "ok": True,
            "ts": now_iso(),
            "agents": agents,
            "calls": calls,
            "alerts": alerts[-50:],
            "kpi": dict(self._kpi),
            "widgets": {
                "active_calls": len(calls),
                "online_agents": online,
                "queue_size": int(self._kpi.get("queue_size") or 0),
                "avg_wait_sec": float(self._kpi.get("avg_wait_sec") or 0),
                "conversion_rate": float(self._kpi.get("conversion_rate") or 0),
                "open_alerts": len(alerts),
                "critical_alerts": sum(1 for a in alerts if a.get("severity") == "critical"),
                "avg_buy_signal": round(avg_buy, 4),
            },
            "realtime_enabled": True,
        }
        self.hub.last_snapshot = data
        self.hub.publish_sync({"type": "snapshot", "payload": data})
        return data

    def dashboard(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "ok": True,
            "widgets": snap["widgets"],
            "recent_alerts": snap["alerts"][-20:],
            "recent_events": self.store.list_events(limit=30),
            "metrics": self.store.get_metrics(),
            "realtime_enabled": True,
        }

    def quality_snapshot(self) -> dict[str, Any]:
        return evaluate_quality(
            self.store,
            snapshot={
                "agents": list(self._agents.values()),
                "calls": list(self._calls.values()),
                "kpi": dict(self._kpi),
            },
        )

    def _refresh_metrics(self, *, extra_alerts: int = 0) -> None:
        metrics = self.store.get_metrics()
        calls = [c for c in self._calls.values() if c.get("status") in {"ringing", "live", "hold"}]
        agents = list(self._agents.values())
        alerts = self.store.list_alerts(limit=500)
        metrics["events"] = int(metrics.get("events") or 0) + 1
        metrics["alerts"] = len(alerts)
        metrics["active_calls"] = len(calls)
        metrics["online_agents"] = sum(1 for a in agents if a.get("status") != "offline")
        metrics["critical_alerts"] = sum(
            1 for a in alerts if a.get("severity") == "critical" and not a.get("acknowledged")
        )
        metrics["avg_buy_signal"] = round(
            (sum(float(c.get("buy_signal") or 0) for c in calls) / max(1, len(calls))) if calls else 0.0,
            4,
        )
        metrics["last_alert_batch"] = extra_alerts
        self.store.save_metrics(metrics)


_engine: WarRoomEngine | None = None


def get_war_room_engine(root: Path | None = None) -> WarRoomEngine:
    global _engine
    if root is not None:
        return WarRoomEngine(store=WarRoomStore(root=root))
    if _engine is None:
        _engine = WarRoomEngine()
    return _engine
