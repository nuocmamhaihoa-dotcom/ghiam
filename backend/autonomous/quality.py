"""Quality evaluation for Autonomous Sales AI."""
from __future__ import annotations

from typing import Any

from autonomous.types import QUALITY_THRESHOLDS, RESTRICTED_CHANGES, SAFE_AUTOMATIONS


def evaluate_quality(store: Any) -> dict[str, Any]:
    recs = store.list_recommendations(limit=200)
    jobs = store.list_jobs(limit=200)
    approvals = store.list_approvals(limit=200)
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
