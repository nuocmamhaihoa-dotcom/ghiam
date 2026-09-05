"""Autonomous Sales AI engine — full sales operating workflow."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomous.approvals import decide_approval, propose_change
from autonomous.automation import execute_automation
from autonomous.quality import evaluate_quality
from autonomous.recommender import map_action_to_automation, recommend_nba
from autonomous.store import AutonomousStore
from autonomous.types import WORKFLOW_STAGES, new_id, now_iso


class AutonomousEngine:
    def __init__(self, store: AutonomousStore | None = None) -> None:
        self.store = store or AutonomousStore()

    # ----- NBA -----
    def recommend(self, context: dict[str, Any]) -> dict[str, Any]:
        rec = recommend_nba(context)
        payload = rec.to_dict()
        self.store.append_recommendation(payload)
        metrics = self.store.get_metrics()
        metrics["recommendations"] = int(metrics.get("recommendations") or 0) + 1
        metrics["revenue_impact_total"] = float(metrics.get("revenue_impact_total") or 0) + float(
            payload.get("expected_revenue_impact") or 0
        )
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

    # ----- Assignment / Follow-up -----
    def assign_lead(self, context: dict[str, Any]) -> dict[str, Any]:
        score = float(context.get("lead_score") or context.get("buy_signal") or 0)
        cltv = float(context.get("cltv_score") or 0)
        if score >= 0.8 or cltv >= 0.75:
            assignee = context.get("top_closer") or "sale_elite_01"
            reason = "high_score_or_cltv"
        elif score >= 0.45:
            assignee = context.get("preferred_sale") or "sale_core_01"
            reason = "mid_score_routing"
        else:
            assignee = context.get("nurture_pool") or "sale_nurture_01"
            reason = "nurture_pool"
        evidence = [f"lead_score={score}", f"cltv={cltv}", f"reason={reason}"]
        job = execute_automation(
            "lead_assignment",
            {
                "lead_id": context.get("lead_id"),
                "assignee": assignee,
                "reason": reason,
            },
        )
        if job.get("ok") and job.get("job"):
            self.store.append_job(job["job"])
            metrics = self.store.get_metrics()
            metrics["automations_run"] = int(metrics.get("automations_run") or 0) + 1
            self.store.save_metrics(metrics)
        return {
            "ok": True,
            "assignment": {
                "lead_id": context.get("lead_id"),
                "assignee": assignee,
                "reason": reason,
                "evidence": evidence,
                "confidence": round(min(0.95, 0.55 + score * 0.4), 4),
            },
            "automation": job.get("job"),
        }

    def schedule_follow_up(self, context: dict[str, Any]) -> dict[str, Any]:
        missed = int(context.get("missed_followups") or 0)
        channel = "zalo" if missed >= 2 else "callback"
        hours = 4 if float(context.get("buy_signal") or 0) >= 0.7 else 24
        job = execute_automation(
            "follow_up_schedule",
            {
                "lead_id": context.get("lead_id"),
                "channel": channel,
                "in_hours": hours,
            },
        )
        if job.get("ok") and job.get("job"):
            self.store.append_job(job["job"])
            metrics = self.store.get_metrics()
            metrics["automations_run"] = int(metrics.get("automations_run") or 0) + 1
            self.store.save_metrics(metrics)
        return {
            "ok": True,
            "follow_up": {
                "lead_id": context.get("lead_id"),
                "channel": channel,
                "in_hours": hours,
                "evidence": [f"missed={missed}", f"channel={channel}", f"in_hours={hours}"],
            },
            "automation": job.get("job"),
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

    # ----- Approvals / learning (proposal only) -----
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
                # Knowledge update only after human approval — never silent rulebook rewrite
                mem = {
                    "memory_id": new_id("mem"),
                    "source_approval": approval_id,
                    "change_type": found.get("change_type"),
                    "proposal": result["approval"].get("proposal"),
                    "status": "queued_for_knowledge_update",
                    "created_at": now_iso(),
                    "evidence": list(found.get("evidence") or []) + ["human_approved=true"],
                }
                self.store.append_memory_proposal(mem)
                metrics["knowledge_proposals"] = int(metrics.get("knowledge_proposals") or 0) + 1
            self.store.save_metrics(metrics)
            result["memory_update"] = approve
        return result

    def propose_learning(self, context: dict[str, Any]) -> dict[str, Any]:
        """Self-learning lab output: proposal only, never auto-writes rulebook."""
        signal = float(context.get("pattern_strength") or context.get("buy_signal") or 0)
        change_type = str(context.get("change_type") or "rule_change")
        title = str(context.get("title") or f"learned_pattern_{new_id('pat')}")
        proposal = {
            "pattern": context.get("pattern") or "follow_up_window",
            "suggested_value": context.get("suggested_value") or {"hours": 4 if signal >= 0.7 else 24},
            "source": "self_learning_lab",
            "memory_graph_refs": list(context.get("memory_refs") or []),
        }
        evidence = list(context.get("evidence") or [f"pattern_strength={signal}", "source=self_learning_lab"])
        return self.propose_rule_change(
            change_type=change_type if change_type in {
                "rule_change", "sop_change", "pricing_change", "routing_policy_change"
            } else "rule_change",
            title=title,
            proposal=proposal,
            evidence=evidence,
            requested_by="self_learning_lab",
        )

    # ----- Full workflow -----
    def run_workflow(self, context: dict[str, Any]) -> dict[str, Any]:
        lead_id = str(context.get("lead_id") or new_id("lead"))
        workflow_id = new_id("wf")
        stages: list[dict[str, Any]] = []
        ctx = dict(context)
        ctx["lead_id"] = lead_id

        # 1) Lead intake + score
        score = float(ctx.get("lead_score") or ctx.get("buy_signal") or 0)
        stages.append({"stage": "lead_intake", "ok": True, "lead_id": lead_id})
        stages.append({"stage": "lead_score", "ok": True, "score": score})

        # 2) Assignment
        assignment = self.assign_lead(ctx)
        stages.append({"stage": "sales_assignment", "ok": True, "result": assignment["assignment"]})

        # 3) Call strategy via NBA
        nba = self.run_nba_pipeline(ctx, auto_execute=True)
        stages.append({"stage": "call_strategy", "ok": True, "recommendation": nba["recommendation"]})

        # 4) Live coaching hint
        coaching = {
            "nudge": "ask_for_commitment" if score >= 0.7 else "discover_pain",
            "evidence": [f"score={score}"],
        }
        if nba["recommendation"]["action"] == "coaching_nudge":
            self.trigger_automation("coaching_nudge", {"lead_id": lead_id, "nudge": coaching["nudge"]})
        stages.append({"stage": "live_coaching", "ok": True, "coaching": coaching})

        # 5) Follow-up
        follow = self.schedule_follow_up(ctx)
        stages.append({"stage": "follow_up", "ok": True, "result": follow["follow_up"]})

        # 6) Revenue analysis
        revenue = {
            "expected_impact": nba["recommendation"].get("expected_revenue_impact")
            or nba["recommendation"].get("expected_impact"),
            "leak_risk": float(ctx.get("churn_risk") or 0),
            "evidence": list(nba["recommendation"].get("evidence") or []),
        }
        stages.append({"stage": "revenue_analysis", "ok": True, "result": revenue})

        # 7) Self learning -> proposal only
        learning = self.propose_learning(
            {
                **ctx,
                "pattern_strength": score,
                "pattern": "workflow_outcome",
                "evidence": [f"workflow={workflow_id}", f"action={nba['recommendation']['action']}"],
            }
        )
        stages.append({"stage": "self_learning", "ok": True, "proposal": learning.get("approval")})

        # 8) QA approval gate (human) — surface pending approval, do not auto-apply
        stages.append(
            {
                "stage": "qa_approval",
                "ok": True,
                "pending_approval_id": (learning.get("approval") or {}).get("approval_id"),
                "auto_applied": False,
            }
        )

        # 9) Knowledge update only marked pending until approval
        stages.append(
            {
                "stage": "knowledge_update",
                "ok": True,
                "status": "awaiting_approval",
                "rulebook_mutated": False,
            }
        )

        completed = {
            "workflow_id": workflow_id,
            "lead_id": lead_id,
            "stages": stages,
            "status": "completed",
            "created_at": now_iso(),
            "completed_at": now_iso(),
            "stage_names": list(WORKFLOW_STAGES),
        }
        self.store.append_workflow(completed)
        metrics = self.store.get_metrics()
        metrics["workflows_completed"] = int(metrics.get("workflows_completed") or 0) + 1
        self.store.save_metrics(metrics)
        return {"ok": True, "workflow": completed, "nba": nba, "assignment": assignment, "follow_up": follow}

    # ----- Dashboard / quality -----
    def dashboard(self) -> dict[str, Any]:
        recs = self.store.list_recommendations(limit=200)
        jobs = self.store.list_jobs(limit=200)
        approvals = self.store.list_approvals(limit=200)
        workflows = self.store.list_workflows(limit=200)
        memory = self.store.list_memory_proposals(limit=200)
        latest: dict[str, dict[str, Any]] = {}
        for a in approvals:
            latest[str(a.get("approval_id"))] = a
        pending = [a for a in latest.values() if a.get("status") == "pending"]
        action_counts: dict[str, int] = {}
        for r in recs:
            action_counts[str(r.get("action"))] = action_counts.get(str(r.get("action")), 0) + 1
        metrics = self.store.get_metrics()
        revenue_impact = float(metrics.get("revenue_impact_total") or 0)
        avg_conf = round(
            (sum(float(r.get("confidence") or 0) for r in recs) / max(1, len(recs))) if recs else 0.0,
            4,
        )
        forecast = round(revenue_impact * max(0.5, avg_conf), 2)
        risk = round(
            min(
                1.0,
                (int(metrics.get("automations_blocked") or 0) + len(pending) * 0.1) / max(1, len(jobs) + 1),
            ),
            4,
        )
        return {
            "ok": True,
            "widgets": {
                "recommendation_count": len(recs),
                "automations_run": int(metrics.get("automations_run") or 0),
                "automations_blocked": int(metrics.get("automations_blocked") or 0),
                "approvals_pending": len(pending),
                "approvals_decided": int(metrics.get("approvals_decided") or 0),
                "rule_changes_applied": int(metrics.get("rule_changes_applied") or 0),
                "avg_confidence": avg_conf,
                "nba_mix": action_counts,
                # CEO dashboard extensions
                "autonomous_actions": int(metrics.get("automations_run") or 0) + len(recs),
                "revenue_impact": revenue_impact,
                "ai_accuracy": avg_conf,
                "approval_queue": len(pending),
                "knowledge_growth": len(memory),
                "forecast": forecast,
                "risk": risk,
                "workflows_completed": len(workflows),
            },
            "recent_recommendations": recs[-20:],
            "recent_jobs": jobs[-20:],
            "pending_approvals": pending[-20:],
            "recent_workflows": workflows[-10:],
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
