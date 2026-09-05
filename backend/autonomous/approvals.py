"""Approval workflow for restricted autonomous changes."""
from __future__ import annotations

from typing import Any

from autonomous.types import RESTRICTED_CHANGES, ApprovalRequest, new_id, now_iso


def propose_change(
    *,
    change_type: str,
    title: str,
    proposal: dict[str, Any],
    evidence: list[str] | None = None,
    requested_by: str = "autonomous_ai",
) -> dict[str, Any]:
    if change_type not in RESTRICTED_CHANGES:
        return {
            "ok": False,
            "error": "not_restricted_use_automation",
            "change_type": change_type,
        }
    req = ApprovalRequest(
        approval_id=new_id("apr"),
        change_type=change_type,
        title=title,
        proposal=proposal,
        evidence=evidence or [f"change_type={change_type}"],
        requested_by=requested_by,
    )
    return {"ok": True, "applied": False, "approval": req.to_dict()}


def decide_approval(
    approval: dict[str, Any],
    *,
    approve: bool,
    decided_by: str,
) -> dict[str, Any]:
    if approval.get("status") != "pending":
        return {"ok": False, "error": "not_pending", "approval": approval}
    approval = dict(approval)
    approval["status"] = "approved" if approve else "rejected"
    approval["decided_at"] = now_iso()
    approval["decided_by"] = decided_by
    applied = False
    if approve:
        applied = True
        proposal = dict(approval.get("proposal") or {})
        proposal["applied"] = True
        approval["proposal"] = proposal
    return {"ok": True, "applied": applied, "approval": approval}
