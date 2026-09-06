"""Alert detection for live telesales operations."""
from __future__ import annotations

from typing import Any

from war_room.types import ALERT_KINDS, WarRoomAlert, new_id


def detect_alerts(
    *,
    agents: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    kpi: dict[str, Any] | None = None,
) -> list[WarRoomAlert]:
    kpi = kpi or {}
    alerts: list[WarRoomAlert] = []

    conversion = float(kpi.get("conversion_rate") or 0.0)
    baseline = float(kpi.get("baseline_conversion") or 0.25)
    queue = int(kpi.get("queue_size") or 0)
    wait_sec = float(kpi.get("avg_wait_sec") or 0.0)

    if baseline > 0 and conversion < baseline * 0.7:
        alerts.append(
            WarRoomAlert(
                alert_id=new_id("alert"),
                kind="conversion_drop",
                severity="critical" if conversion < baseline * 0.5 else "high",
                title="Conversion drop",
                message=f"Conversion {conversion:.0%} vs baseline {baseline:.0%}",
                entity_type="floor",
                entity_id="global",
                evidence=[f"conversion={conversion}", f"baseline={baseline}"],
                recommended_action="Push best scripts and coach lowest converters now.",
            )
        )

    if wait_sec >= 45:
        alerts.append(
            WarRoomAlert(
                alert_id=new_id("alert"),
                kind="sla_breach",
                severity="critical" if wait_sec >= 90 else "high",
                title="Answer SLA breach",
                message=f"Average wait {wait_sec:.0f}s exceeds SLA",
                entity_type="queue",
                entity_id="inbound",
                evidence=[f"avg_wait_sec={wait_sec}", f"queue_size={queue}"],
                recommended_action="Reassign available agents to inbound queue.",
            )
        )

    if queue >= 20:
        alerts.append(
            WarRoomAlert(
                alert_id=new_id("alert"),
                kind="queue_overflow",
                severity="high" if queue < 40 else "critical",
                title="Queue overflow",
                message=f"Queue size {queue}",
                entity_type="queue",
                entity_id="inbound",
                evidence=[f"queue_size={queue}"],
                recommended_action="Open overflow skill group / callback mode.",
            )
        )

    objections = [c for c in calls if c.get("objection")]
    if len(calls) >= 5 and len(objections) / max(1, len(calls)) >= 0.45:
        alerts.append(
            WarRoomAlert(
                alert_id=new_id("alert"),
                kind="objection_spike",
                severity="medium",
                title="Objection spike",
                message=f"{len(objections)}/{len(calls)} live calls have objections",
                entity_type="floor",
                entity_id="global",
                evidence=[f"objection_ratio={len(objections) / max(1, len(calls)):.2f}"],
                recommended_action="Broadcast objection playbook to floor.",
            )
        )

    for call in calls:
        buy = float(call.get("buy_signal") or 0)
        if buy < 0.2 and call.get("status") == "live":
            if str(call.get("sentiment") or "") in {"frustrated", "resistant", "negative"}:
                alerts.append(
                    WarRoomAlert(
                        alert_id=new_id("alert"),
                        kind="churn_risk",
                        severity="high",
                        title="Live churn risk",
                        message=f"Call {call.get('call_id')} sentiment={call.get('sentiment')}",
                        entity_type="call",
                        entity_id=str(call.get("call_id") or ""),
                        evidence=[
                            f"buy_signal={buy}",
                            f"sentiment={call.get('sentiment')}",
                            f"agent={call.get('agent_id')}",
                        ],
                        recommended_action="Supervisor whisper: empathy + value reset.",
                    )
                )

    for agent in agents:
        active = int(agent.get("active_calls") or 0)
        idle = float(agent.get("idle_sec") or 0)
        if agent.get("status") == "available" and active == 0 and idle >= 300:
            alerts.append(
                WarRoomAlert(
                    alert_id=new_id("alert"),
                    kind="idle_agent",
                    severity="low",
                    title="Idle agent",
                    message=f"Agent {agent.get('name') or agent.get('agent_id')} idle",
                    entity_type="agent",
                    entity_id=str(agent.get("agent_id") or ""),
                    evidence=[f"idle_sec={idle}", f"status={agent.get('status')}"],
                    recommended_action="Assign next dial / assist queue.",
                )
            )

    seen: set[tuple[str, str]] = set()
    unique: list[WarRoomAlert] = []
    for alert in alerts:
        key = (alert.kind, alert.entity_id)
        if key in seen or alert.kind not in ALERT_KINDS:
            continue
        seen.add(key)
        unique.append(alert)
    return unique
