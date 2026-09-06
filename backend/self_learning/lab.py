"""Self-Learning Lab façade — learn from calls without auto-mutating production rules."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from approval.center import ApprovalCenter, QUALITY_THRESHOLD
from research import (
    ClusterEngine,
    CoachingGenerator,
    FailurePatternDiscovery,
    GoldenCallDiscovery,
    IntentDiscovery,
    ObjectionDiscovery,
    PatternDetector,
    RevenueLeakDiscovery,
)
from self_learning.store import LearningStore
from self_learning.types import Proposal, ProposalKind, new_id, now_iso


class SelfLearningLab:
    """Internal AI Research Lab. Never writes production rules without QA approval."""

    def __init__(self, store: LearningStore | None = None) -> None:
        self.store = store or LearningStore()
        self.patterns = PatternDetector()
        self.clusters = ClusterEngine()
        self.intents = IntentDiscovery()
        self.objections = ObjectionDiscovery()
        self.golden = GoldenCallDiscovery()
        self.failures = FailurePatternDiscovery()
        self.revenue = RevenueLeakDiscovery()
        self.coaching = CoachingGenerator()
        self.approval = ApprovalCenter(self.store)

    def ingest_call(self, call: dict[str, Any]) -> dict[str, Any]:
        call_id = str(call.get("call_id") or call.get("id") or new_id("call"))
        call = dict(call)
        call["call_id"] = call_id
        call["ingested_at"] = now_iso()
        self.store.append_raw_call(call)

        knowledge = self.store.get_knowledge()
        knowledge["layer_1_raw_calls"].append({"call_id": call_id, "ingested_at": call["ingested_at"]})
        self.store.save_knowledge(knowledge)

        pattern_hits = self.patterns.detect(call)
        for p in pattern_hits:
            self.store.append_pattern(p.to_dict())

        clusters = self.clusters.cluster(pattern_hits)
        for c in clusters:
            self.store.append_cluster(c.to_dict())

        proposals: list[Proposal] = []
        for p in pattern_hits:
            if p.known or p.novelty < 0.45:
                continue
            proposals.append(Proposal(
                proposal_id=new_id("prop"),
                kind=ProposalKind.PATTERN,
                title=f"Novel Pattern: {p.text[:60]}",
                summary=f"Phát hiện pattern mới ({p.kind}).",
                suggested_rule=f"PATTERN::{p.kind}::{p.normalized}",
                suggested_coaching=f"Luyện phản xạ với câu «{p.text}».",
                suggested_sop_update="Xem xét bổ sung pattern vào lexicon sau QA.",
                evidence=list(p.evidence),
                confidence=p.confidence,
                novelty=p.novelty,
                evidence_count=len(p.evidence),
                qa_agreement_prediction=round(min(0.95, 0.5 + p.novelty * 0.3 + p.confidence * 0.2), 4),
                expected_impact="medium",
                metadata={"pattern_id": p.pattern_id, "pending_qa": True},
            ))
        for c in clusters:
            proposals.append(Proposal(
                proposal_id=new_id("prop"),
                kind=ProposalKind.CLUSTER,
                title=f"Cluster: {c.name}",
                summary=c.hidden_meaning,
                suggested_rule=f"CLUSTER::{c.name}",
                suggested_coaching=f"Huấn luyện xử lý cụm «{c.name}»: {c.hidden_meaning}",
                suggested_sop_update=f"Gắn cluster `{c.name}` vào playbook sau QA.",
                evidence=list(c.evidence),
                confidence=c.confidence,
                novelty=0.55,
                evidence_count=max(1, c.size),
                qa_agreement_prediction=round(min(0.95, 0.55 + c.confidence * 0.3), 4),
                expected_impact="medium",
                metadata={"cluster_id": c.cluster_id, "variants": c.variants, "pending_qa": True},
            ))

        proposals.extend(self.intents.discover(pattern_hits, clusters))
        proposals.extend(self.objections.discover(pattern_hits))

        knowledge = self.store.get_knowledge()
        kept: list[Proposal] = []
        for prop in proposals:
            if prop.quality_score() < QUALITY_THRESHOLD:
                continue
            self.store.append_proposal(prop.to_dict())
            knowledge["layer_2_verified_knowledge"].append({
                "proposal_id": prop.proposal_id,
                "kind": prop.kind.value,
                "title": prop.title,
                "created_at": prop.created_at,
            })
            kept.append(prop)
        self.store.save_knowledge(knowledge)
        return {
            "call_id": call_id,
            "patterns": [p.to_dict() for p in pattern_hits],
            "clusters": [c.to_dict() for c in clusters],
            "proposals": [p.to_dict() for p in kept],
            "pending_qa": True,
            "auto_applied_to_production": False,
        }

    def discover_golden(self, calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        calls = calls or self.store.list_raw_calls()
        out = []
        for p in self.golden.discover(calls):
            if p.quality_score() < QUALITY_THRESHOLD:
                continue
            self.store.append_proposal(p.to_dict())
            out.append(p.to_dict())
        return out

    def discover_failures(self, calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        calls = calls or self.store.list_raw_calls()
        out = []
        for p in self.failures.discover(calls):
            if p.quality_score() < QUALITY_THRESHOLD:
                continue
            self.store.append_proposal(p.to_dict())
            out.append(p.to_dict())
        return out

    def discover_revenue_leaks(
        self, calls: list[dict[str, Any]] | None = None, week_id: str = "current"
    ) -> list[dict[str, Any]]:
        calls = calls or self.store.list_raw_calls()
        out = []
        for p in self.revenue.propose(calls, week_id=week_id):
            self.store.append_proposal(p.to_dict())
            out.append(p.to_dict())
        return out

    def generate_coaching(self, proposal_id: str | None = None) -> list[dict[str, Any]]:
        proposals = self.store.list_proposals()
        if proposal_id:
            proposals = [p for p in proposals if p.get("proposal_id") == proposal_id]
        out = []
        for p in self.coaching.from_many(proposals[:20]):
            self.store.append_proposal(p.to_dict())
            out.append(p.to_dict())
        return out

    def qa_queue(self) -> list[dict[str, Any]]:
        return self.approval.list_queue("pending_qa")

    def approve(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        return self.approval.approve(proposal_id, reviewer)

    def reject(self, proposal_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        return self.approval.reject(proposal_id, reviewer, reason)

    def merge(self, proposal_id: str, into_proposal_id: str, reviewer: str) -> dict[str, Any]:
        return self.approval.merge(proposal_id, into_proposal_id, reviewer)

    def edit(self, proposal_id: str, reviewer: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self.approval.edit(proposal_id, reviewer, patch)

    def promote(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        result = self.approval.promote_to_production(proposal_id, reviewer)
        if result.get("promoted"):
            self._link_memory_graph(result.get("record") or {})
        return result

    def rollback(self, version_id: str, reviewer: str) -> dict[str, Any]:
        return self.approval.rollback(version_id, reviewer)

    def _link_memory_graph(self, record: dict[str, Any]) -> None:
        marker = Path(__file__).resolve().parents[2] / "datasets" / "self_learning" / "memory_graph_links.jsonl"
        marker.parent.mkdir(parents=True, exist_ok=True)
        import json
        with marker.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"linked_at": now_iso(), "record": record, "note": "approved_only"}, ensure_ascii=False) + "\n")

    def self_evaluate(self, metrics_update: dict[str, float] | None = None) -> dict[str, Any]:
        metrics = self.store.get_metrics()
        if metrics_update:
            for k, v in metrics_update.items():
                if k in metrics and isinstance(v, (int, float)):
                    metrics[k] = float(v)
        alerts = []
        for key in (
            "pattern_accuracy", "intent_accuracy", "emotion_accuracy",
            "root_cause_accuracy", "revenue_leak_accuracy",
        ):
            if float(metrics.get(key) or 1) < 0.75:
                alerts.append({"type": "accuracy_drop", "metric": key, "value": metrics.get(key)})
        metrics["alerts"] = alerts
        self.store.save_metrics(metrics)
        self.store.append_eval({"at": now_iso(), "metrics": metrics, "alerts": alerts})
        return {"metrics": metrics, "alerts": alerts, "healthy": not alerts}

    def dashboard(self) -> dict[str, Any]:
        proposals = self.store.list_proposals()
        pending = [p for p in proposals if p.get("status") == "pending_qa"]
        approved = [p for p in proposals if p.get("status") == "approved"]
        rejected = [p for p in proposals if p.get("status") == "rejected"]
        patterns = self.store.list_patterns()
        knowledge = self.store.get_knowledge()
        metrics = self.store.get_metrics()
        new_intents = [p for p in proposals if p.get("kind") == "intent"]
        new_objections = [p for p in proposals if p.get("kind") == "objection"]
        revenue_props = [p for p in proposals if p.get("kind") == "revenue_leak"]
        revenue_impact = 0.0
        for p in revenue_props:
            report = (p.get("metadata") or {}).get("report") or {}
            revenue_impact += float(report.get("lost_revenue") or 0)
        growth = (
            len(knowledge.get("layer_2_verified_knowledge") or [])
            + len(knowledge.get("layer_3_approved_rules") or [])
            + len(knowledge.get("layer_4_production_knowledge") or [])
        )
        metrics["learning_velocity"] = round(len(patterns) / max(1, len(self.store.list_raw_calls()) or 1), 4)
        metrics["knowledge_growth"] = growth
        self.store.save_metrics(metrics)
        return {
            "widgets": {
                "new_patterns": len(patterns),
                "new_intents": len(new_intents),
                "new_objections": len(new_objections),
                "qa_queue": len(pending),
                "approved_rules": len(approved),
                "rejected_rules": len(rejected),
                "learning_velocity": metrics["learning_velocity"],
                "revenue_impact": revenue_impact,
                "confidence_trend": metrics.get("pattern_accuracy"),
                "knowledge_growth": growth,
            },
            "layers": {k: len(v or []) for k, v in knowledge.items()},
            "metrics": metrics,
            "auto_apply_blocked": True,
        }

    def dataset_growth(self) -> dict[str, Any]:
        proposals = self.store.list_proposals()
        return {
            "period": "month",
            "new_patterns": len(self.store.list_patterns()),
            "new_intents": sum(1 for p in proposals if p.get("kind") == "intent"),
            "new_objections": sum(1 for p in proposals if p.get("kind") == "objection"),
            "new_golden_calls": sum(1 for p in proposals if p.get("kind") == "golden_call"),
            "new_rules": sum(1 for p in proposals if p.get("status") == "approved"),
            "raw_calls": len(self.store.list_raw_calls()),
        }

    def quality_snapshot(self) -> dict[str, Any]:
        knowledge = self.store.get_knowledge()
        prod = knowledge.get("layer_4_production_knowledge") or []
        pending_in_prod = [r for r in prod if (r.get("content") or {}).get("status") == "pending_qa"]
        return {
            "ok": len(pending_in_prod) == 0,
            "production_count": len(prod),
            "pending_leaked_into_production": len(pending_in_prod),
            "quality_threshold": QUALITY_THRESHOLD,
            "requires_qa": True,
        }


_lab: SelfLearningLab | None = None


def get_self_learning_lab() -> SelfLearningLab:
    global _lab
    if _lab is None:
        _lab = SelfLearningLab()
    return _lab
