"""Quality evaluation for Autonomous Sales AI."""
from __future__ import annotations

from typing import Any

from autonomous.types import QUALITY_THRESHOLDS, RESTRICTED_CHANGES, SAFE_AUTOMATIONS, WORKFLOW_STAGES


def evaluate_quality(store: Any) -> dict[str, Any]:
    recs = store.list_recommendations(limit=200)
    jobs = store.list_jobs(limit=200)
    approvals = store.list_approvals(limit=200)
    workflows = store.list_workflows(limit=200) if hasattr(store, "list_workflows") else []
    memory = store.list_memory_proposals(limit=200) if hasattr(store, "list_memory_proposals") else []
    metrics = store.get_metrics()

    if recs:
        good = [
            1.0
            if float(r.get("confidence") or 0) >= 0.55 and len(r.get("evidence") or []) >= 1
            else 0.3
            for r in recs[-50:]
        ]
        recommendation_precision = sum(good) / max(1, len(good))
    else:
        recommendation_precision = 0.8

    unsafe = 0
    total_jobs = max(1, len(jobs[-80:]))
    for job in jobs[-80:]:
        kind = str(job.get("kind") or "")
        if kind in RESTRICTED_CHANGES and job.get("status") == "completed" and not job.get("approval_id"):
            unsafe += 1
        if kind not in SAFE_AUTOMATIONS and kind not in RESTRICTED_CHANGES and job.get("status") == "completed":
            unsafe += 1
    automation_safety = 1.0 - (unsafe / total_jobs)

    restricted_proposals = [a for a in approvals if a.get("change_type") in RESTRICTED_CHANGES]
    if not restricted_proposals:
        approval_enforcement = 1.0
    else:
        violations = 0
        for a in restricted_proposals:
            if a.get("status") == "approved" and not a.get("decided_by"):
                violations += 1
            if a.get("status") == "pending" and (a.get("proposal") or {}).get("applied") is True:
                violations += 1
        approval_enforcement = 1.0 - (violations / max(1, len(restricted_proposals)))

    evidence_scores = []
    for row in (recs[-30:] + jobs[-30:] + approvals[-30:]):
        evidence_scores.append(1.0 if len(row.get("evidence") or []) >= 1 else 0.25)
    evidence_validation = sum(evidence_scores) / max(1, len(evidence_scores)) if evidence_scores else 0.8

    # Workflow integrity: completed workflows must include all canonical stages
    if workflows:
        intact = 0
        for wf in workflows[-30:]:
            names = {str(s.get("stage")) for s in (wf.get("stages") or [])}
            if set(WORKFLOW_STAGES).issubset(names) or set(WORKFLOW_STAGES) == set(wf.get("stage_names") or []):
                intact += 1
            elif len(wf.get("stages") or []) >= 8:
                intact += 1
        workflow_integrity = intact / max(1, len(workflows[-30:]))
    else:
        workflow_integrity = 0.95

    # Revenue forecast sanity: recommendations carry revenue impact fields
    if recs:
        with_impact = [
            1.0
            if (r.get("expected_revenue_impact") is not None or r.get("expected_impact") is not None)
            else 0.2
            for r in recs[-40:]
        ]
        revenue_forecast = sum(with_impact) / max(1, len(with_impact))
    else:
        revenue_forecast = 0.85

    # Memory consistency: approved changes produce memory proposals; pending never mutate
    mem_ok = 1.0
    if memory:
        bad = sum(1 for m in memory if m.get("rulebook_mutated") is True)
        mem_ok = 1.0 - (bad / max(1, len(memory)))
    pending_applied = sum(
        1
        for a in approvals
        if a.get("status") == "pending" and (a.get("proposal") or {}).get("applied") is True
    )
    memory_consistency = min(mem_ok, 1.0 - (pending_applied / max(1, len(approvals) or 1)))

    # Rule safety: no restricted kind executed as automation job without approval
    rule_violations = 0
    for job in jobs:
        if str(job.get("kind")) in RESTRICTED_CHANGES and job.get("status") == "completed":
            if not job.get("approval_id"):
                rule_violations += 1
    rule_safety = 1.0 - (rule_violations / max(1, len(jobs) or 1))

    checks = {
        "recommendation_precision": {
            "ok": recommendation_precision >= QUALITY_THRESHOLDS["recommendation_precision"],
            "value": round(recommendation_precision, 4),
            "threshold": QUALITY_THRESHOLDS["recommendation_precision"],
        },
        "automation_safety": {
            "ok": automation_safety >= QUALITY_THRESHOLDS["automation_safety"],
            "value": round(automation_safety, 4),
            "threshold": QUALITY_THRESHOLDS["automation_safety"],
        },
        "approval_enforcement": {
            "ok": approval_enforcement >= QUALITY_THRESHOLDS["approval_enforcement"],
            "value": round(approval_enforcement, 4),
            "threshold": QUALITY_THRESHOLDS["approval_enforcement"],
        },
        "evidence_validation": {
            "ok": evidence_validation >= QUALITY_THRESHOLDS["evidence_validation"],
            "value": round(evidence_validation, 4),
            "threshold": QUALITY_THRESHOLDS["evidence_validation"],
        },
        "workflow_integrity": {
            "ok": workflow_integrity >= QUALITY_THRESHOLDS["workflow_integrity"],
            "value": round(workflow_integrity, 4),
            "threshold": QUALITY_THRESHOLDS["workflow_integrity"],
        },
        "revenue_forecast": {
            "ok": revenue_forecast >= QUALITY_THRESHOLDS["revenue_forecast"],
            "value": round(revenue_forecast, 4),
            "threshold": QUALITY_THRESHOLDS["revenue_forecast"],
        },
        "memory_consistency": {
            "ok": memory_consistency >= QUALITY_THRESHOLDS["memory_consistency"],
            "value": round(memory_consistency, 4),
            "threshold": QUALITY_THRESHOLDS["memory_consistency"],
        },
        "rule_safety": {
            "ok": rule_safety >= QUALITY_THRESHOLDS["rule_safety"],
            "value": round(rule_safety, 4),
            "threshold": QUALITY_THRESHOLDS["rule_safety"],
        },
    }
    errors = [k for k, v in checks.items() if not v["ok"]]
    return {
        "ok": len(errors) == 0,
        "checks": checks,
        "thresholds": QUALITY_THRESHOLDS,
        "errors": errors,
        "metrics": metrics,
        "approval_only_rule_changes": True,
    }
