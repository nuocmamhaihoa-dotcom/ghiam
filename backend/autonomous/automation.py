"""Safe automation executor — never applies restricted policy changes."""
from __future__ import annotations

from typing import Any

from autonomous.types import RESTRICTED_CHANGES, SAFE_AUTOMATIONS, AutomationJob, new_id, now_iso


def is_safe_automation(kind: str) -> bool:
    return kind in SAFE_AUTOMATIONS


def is_restricted_change(kind: str) -> bool:
    return kind in RESTRICTED_CHANGES


def execute_automation(kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    if is_restricted_change(kind):
        return {
            "ok": False,
            "blocked": True,
            "reason": "restricted_change_requires_approval",
            "kind": kind,
            "job": None,
        }
    if not is_safe_automation(kind):
        return {
            "ok": False,
            "blocked": True,
            "reason": "unknown_or_unsafe_automation",
            "kind": kind,
            "job": None,
        }

    job = AutomationJob(
        job_id=new_id("job"),
        kind=kind,
        status="completed",
        payload=payload,
        evidence=[f"kind={kind}", f"lead={payload.get('lead_id')}", "auto_executed=true"],
        completed_at=now_iso(),
    )
    return {"ok": True, "blocked": False, "kind": kind, "job": job.to_dict()}
