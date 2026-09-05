"""Autonomous Sales AI engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomous.approvals import decide_approval, propose_change
from autonomous.automation import execute_automation
from autonomous.quality import evaluate_quality
from autonomous.recommender import map_action_to_automation, recommend_nba
from autonomous.store import AutonomousStore


class AutonomousEngine:
    def __init__(self, store: AutonomousStore | None = None) -> None:
        self.store = store or AutonomousStore()

    def recommend(self, context: dict[str, Any]) -> dict[str, Any]:
        rec = recommend_nba(context)
        payload = rec.to_dict()
        self.store.append_recommendation(payload)
        metrics = self.store.get_metrics()
        metrics["recommendations"] = int(metrics.get("recommendations") or 0) + 1
        self.store.save_metrics(metrics)
        return {"ok": True, "recommendation": payload}

    def run_nba_pipeline(self, context: dict[str, Any], *, auto_execute: bool = True) -> dict[str, Any]:
        rec_out = self.recommend(context)
        rec = rec_out["recommendation"]
        automation = None
        blocked = False
        if auto_execute:
            kind = map_action_to_automation(str(rec["action"]))
            if kind:
                result = execute_automation(
                    kind,
                    {
                        "lead_id": context.get("lead_id"),
                        "call_id": context.get("call_id"),
                        "action": rec["action"],
                    },
                )
                if result.get("ok") and result.get("job"):
                    self.store.append_job(result["job"])
                    metrics = self.store.get_metrics()
                    metrics["automations_run"] = int(metrics.get("automations_run") or 0) + 1
                    self.store.save_metrics(metrics)
                    automation = result["job"]
                else:
                    blocked = bool(result.get("blocked"))
                    metrics = self.store.get_metrics()
                    metrics["automations_blocked"] = int(metrics.get("automations_blocked") or 0) + 1
                    self.store.save_metrics(metrics)
                    automation = result
        return {
            "ok": True,
            "recommendation": rec,
            "automation": automation,
            "auto_execute": auto_execute,
            "blocked": blocked,
        }

    def trigger_automation(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = execute_automation(kind, payload)
        if result.get("ok") and result.get("job"):
            self.store.append_job(result["job"])
            metrics = self.store.get_metrics()
            metrics["automations_run"] = int(metrics.get("automations_run") or 0) + 1
            self.store.save_metrics(metrics)
        elif result.get("blocked"):
            metrics = self.store.get_metrics()
            metrics["automations_blocked"] = int(metrics.get("automations_blocked") or 0) + 1
            self.store.save_metrics(metrics)
        return result

    def propose_rule_change(
        self,
        *,
        change_type: str,
        title: str,
        proposal: dict[str, Any],
        evidence: list[str] | None = None,
        requested_by: str = "autonomous_ai",
    ) -> dict[str, Any]:
        result = propose_change(
            change_type=change_type,
            title=title,
            proposal=proposal,
            evidence=evidence,
            requested_by=requested_by,
        )
        if result.get("ok") and result.get("approval"):
            self.store.append_approval(result["approval"])
            metrics = self.store.get_metrics()
            metrics["approvals_pending"] = int(metrics.get("approvals_pending") or 0) + 1
            self.store.save_metrics(metrics)
            result["applied"] = False
        return result

    def decide_rule_change(
        self,
        approval_id: str,
        *,
        approve: bool,
        decided_by: str,
    ) -> dict[str, Any]:
        rows = self.store.list_approvals(limit=1000)
        found = None
        for row in rows:
            if row.get("approval_id") == approval_id:
                found = row
                break
        if not found:
            return {"ok": False, "error": "approval_not_found"}
        result = decide_approval(found, approve=approve, decided_by=decided_by)
        if result.get("ok"):
            self.store.append_approval(result["approval"])
            metrics = self.store.get_metrics()
            metrics["approvals_pending"] = max(0, int(metrics.get("approvals_pending") or 0) - 1)
            metrics["approvals_decided"] = int(metrics.get("approvals_decided") or 0) + 1
            if approve:
                metrics["rule_changes_applied"] = int(metrics.get("rule_changes_applied") or 0) + 1
            self.store.save_metrics(metrics)
        return result

    def dashboard(self) -> dict[str, Any]:
        recs = self.store.list_recommendations(limit=200)
        jobs = self.store.list_jobs(limit=200)
        approvals = self.store.list_approvals(limit=200)
        latest: dict[str, dict[str, Any]] = {}
        for a in approvals:
            latest[str(a.get("approval_id"))] = a
        pending = [a for a in latest.values() if a.get("status") == "pending"]
        action_counts: dict[str, int] = {}
        for r in recs:
            action_counts[str(r.get("action"))] = action_counts.get(str(r.get("action")), 0) + 1
        metrics = self.store.get_metrics()
        return {
            "ok": True,
            "widgets": {
                "recommendation_count": len(recs),
                "automations_run": int(metrics.get("automations_run") or 0),
                "automations_blocked": int(metrics.get("automations_blocked") or 0),
                "approvals_pending": len(pending),
                "approvals_decided": int(metrics.get("approvals_decided") or 0),
                "rule_changes_applied": int(metrics.get("rule_changes_applied") or 0),
                "avg_confidence": round(
                    (sum(float(r.get("confidence") or 0) for r in recs) / max(1, len(recs))) if recs else 0.0,
                    4,
                ),
                "nba_mix": action_counts,
            },
            "recent_recommendations": recs[-20:],
            "recent_jobs": jobs[-20:],
            "pending_approvals": pending[-20:],
            "approval_only_rule_changes": True,
        }

    def quality_snapshot(self) -> dict[str, Any]:
        return evaluate_quality(self.store)


_engine: AutonomousEngine | None = None


def get_autonomous_engine(root: Path | None = None) -> AutonomousEngine:
    global _engine
    if root is not None:
        return AutonomousEngine(store=AutonomousStore(root=root))
    if _engine is None:
        _engine = AutonomousEngine()
    return _engine
