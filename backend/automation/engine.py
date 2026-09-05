"""Automation: callback reminders, follow-up, CRM update, tasks, coaching."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

JobType = Literal[
    "callback_reminder",
    "follow_up",
    "crm_update",
    "create_task",
    "create_coaching",
]


@dataclass(slots=True)
class AutomationJob:
    job_id: str
    job_type: JobType
    payload: dict[str, Any]
    status: str = "pending"
    created_at: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AutomationEngine:
    def __init__(self) -> None:
        self._jobs: list[AutomationJob] = []
        self._audit: list[dict[str, Any]] = []

    def trigger(self, job_type: JobType, payload: dict[str, Any] | None = None) -> AutomationJob:
        job = AutomationJob(
            job_id=f"job_{uuid4().hex[:12]}",
            job_type=job_type,
            payload=payload or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        handlers = {
            "callback_reminder": lambda p: {
                "ok": True,
                "action": "callback_reminder",
                "lead_id": p.get("lead_id"),
                "schedule_at": p.get("schedule_at", "+24h"),
            },
            "follow_up": lambda p: {
                "ok": True,
                "action": "follow_up",
                "channel": p.get("channel", "zalo"),
                "lead_id": p.get("lead_id"),
            },
            "crm_update": lambda p: {
                "ok": True,
                "action": "crm_update",
                "connector": p.get("connector", "hubspot"),
                "fields_updated": list((p.get("fields") or {"status": 1}).keys()),
            },
            "create_task": lambda p: {
                "ok": True,
                "action": "create_task",
                "title": p.get("title", "Follow up"),
                "assignee": p.get("assignee"),
            },
            "create_coaching": lambda p: {
                "ok": True,
                "action": "create_coaching",
                "agent_id": p.get("agent_id"),
                "focus": p.get("focus", ["objection_handling"]),
            },
        }
        if job_type not in handlers:
            job.result = {"ok": False, "error": f"unknown job_type {job_type}"}
            job.status = "failed"
        else:
            job.result = handlers[job_type](job.payload)
            job.status = "completed" if job.result.get("ok") else "failed"
        self._jobs.append(job)
        self._audit.append(
            {
                "event": "automation_trigger",
                "job_id": job.job_id,
                "job_type": job_type,
                "status": job.status,
            }
        )
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        return [j.to_dict() for j in self._jobs]

    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit)

    def run_nba_pipeline(
        self,
        nba: dict[str, Any],
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[AutomationJob]:
        ctx = context or kwargs.get("context") or {}
        action = str(nba.get("action") or "")
        jobs: list[AutomationJob] = []

        if action == "callback":
            jobs.append(self.trigger("callback_reminder", {"lead_id": ctx.get("lead_id"), "schedule_at": nba.get("schedule_at")}))
            jobs.append(self.trigger("create_task", {"title": "Callback", "assignee": ctx.get("agent_id"), "due": nba.get("schedule_at")}))
        elif action == "zalo_message":
            jobs.append(self.trigger("follow_up", {"lead_id": ctx.get("lead_id"), "channel": "zalo"}))
        elif action == "email":
            jobs.append(self.trigger("follow_up", {"lead_id": ctx.get("lead_id"), "channel": "email"}))
        elif action in {"escalate_leader", "reassign_sale"}:
            jobs.append(self.trigger("crm_update", {"lead_id": ctx.get("lead_id"), "fields": {"routing": action}}))
            jobs.append(self.trigger("create_task", {"title": f"Handle {action}", "assignee": ctx.get("leader_id") or ctx.get("agent_id")}))
        elif action == "close_lead":
            jobs.append(self.trigger("crm_update", {"lead_id": ctx.get("lead_id"), "fields": {"status": "closed_lost"}}))

        jobs.append(self.trigger("crm_update", {"lead_id": ctx.get("lead_id"), "fields": {"last_touch": "now"}}))
        if float(ctx.get("score") or 100) < 60:
            jobs.append(self.trigger("create_coaching", {"agent_id": ctx.get("agent_id"), "call_id": ctx.get("call_id")}))
        return jobs


# Back-compat aliases for Sales OS façade naming
AutomationEngine.list_jobs = AutomationEngine.list_jobs  # type: ignore[attr-defined]
AutomationEngine.run_nba_pipeline = AutomationEngine.run_nba_pipeline  # type: ignore[attr-defined]


__all__ = ["AutomationEngine", "AutomationJob"]
