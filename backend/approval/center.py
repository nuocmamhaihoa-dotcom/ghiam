"""QA Approval Center — production knowledge requires explicit QA approval."""
from __future__ import annotations

from typing import Any, Callable

from self_learning.store import LearningStore
from self_learning.types import (
    KnowledgeLayer,
    KnowledgeRecord,
    ProposalStatus,
    VersionRecord,
    new_id,
    now_iso,
)

QUALITY_THRESHOLD = 0.55
LAYER_KEY = {
    KnowledgeLayer.RAW: "layer_1_raw_calls",
    KnowledgeLayer.VERIFIED: "layer_2_verified_knowledge",
    KnowledgeLayer.APPROVED: "layer_3_approved_rules",
    KnowledgeLayer.PRODUCTION: "layer_4_production_knowledge",
}


class QualityGate:
    def check(self, proposal: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        evidence = proposal.get("evidence") or []
        evidence_count = int(proposal.get("evidence_count") or len(evidence))
        confidence = float(proposal.get("confidence") or 0)
        novelty = float(proposal.get("novelty") or 0)
        status = proposal.get("status")
        if evidence_count < 1 or not evidence:
            errors.append("missing_evidence")
        if confidence < 0.5:
            errors.append("confidence_below_threshold")
        if status not in {ProposalStatus.APPROVED.value, "approved"}:
            errors.append("qa_not_approved")
        if not proposal.get("version"):
            errors.append("missing_version")
        if not proposal.get("proposal_id"):
            errors.append("missing_proposal_id")
        content_key = f"{proposal.get('suggested_rule') or ''}|{proposal.get('title') or ''}".lower()
        proposal_id = proposal.get("proposal_id")
        for rec in knowledge.get("layer_4_production_knowledge") or []:
            if proposal_id and rec.get("proposal_id") == proposal_id:
                errors.append("already_in_production")
                break
            content = rec.get("content") or {}
            existing = f"{content.get('suggested_rule') or ''}|{content.get('title') or ''}".lower()
            if content_key and content_key == existing:
                errors.append("duplicate_knowledge")
                break
        quality = round(
            0.35 * confidence
            + 0.25 * novelty
            + 0.25 * min(1.0, evidence_count / 10.0)
            + 0.15 * float(proposal.get("qa_agreement_prediction") or 0.5),
            4,
        )
        if quality < QUALITY_THRESHOLD:
            errors.append("quality_score_below_threshold")
        if not proposal.get("diff"):
            warnings.append("empty_diff")
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "quality_score": quality,
            "can_promote": not errors,
        }


class ApprovalCenter:
    def __init__(self, store: LearningStore | None = None) -> None:
        self.store = store or LearningStore()
        self.gate = QualityGate()

    def _quality(self, row: dict[str, Any]) -> float:
        return round(
            0.35 * float(row.get("confidence") or 0)
            + 0.25 * float(row.get("novelty") or 0)
            + 0.25 * min(1.0, float(row.get("evidence_count") or 0) / 10.0)
            + 0.15 * float(row.get("qa_agreement_prediction") or 0.5),
            4,
        )

    def list_queue(self, status: str | None = "pending_qa") -> list[dict[str, Any]]:
        rows = self.store.list_proposals()
        if status:
            rows = [r for r in rows if r.get("status") == status]
        visible: list[dict[str, Any]] = []
        for r in rows:
            qs = float(r.get("quality_score") or self._quality(r))
            if status == "pending_qa" and qs < QUALITY_THRESHOLD:
                continue
            item = dict(r)
            item["quality_score"] = qs
            visible.append(item)
        return visible

    def _update(self, proposal_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        rows = self.store.list_proposals()
        found = None
        for i, row in enumerate(rows):
            if row.get("proposal_id") == proposal_id:
                rows[i] = mutator(dict(row))
                found = rows[i]
                break
        if found is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        self.store.rewrite_proposals(rows)
        return found

    @staticmethod
    def _bump(version: str) -> str:
        parts = str(version or "0.1.0").split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            return ".".join(parts)
        except ValueError:
            return f"{version}.1"

    def approve(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        def mutate(row: dict[str, Any]) -> dict[str, Any]:
            before = row.get("status")
            row["status"] = ProposalStatus.APPROVED.value
            row["reviewer"] = reviewer
            row["reviewed_at"] = now_iso()
            row["layer"] = KnowledgeLayer.APPROVED.value
            row["version"] = self._bump(row.get("version") or "0.1.0")
            row["diff"] = {"before_status": before, "after_status": "approved"}
            return row

        row = self._update(proposal_id, mutate)
        knowledge = self.store.get_knowledge()
        gate = self.gate.check(row, knowledge)
        record = KnowledgeRecord(
            record_id=new_id("kn"),
            layer=KnowledgeLayer.APPROVED,
            kind=str(row.get("kind")),
            content=row,
            version=str(row.get("version")),
            proposal_id=proposal_id,
            reviewer=reviewer,
        )
        knowledge[LAYER_KEY[KnowledgeLayer.APPROVED]].append(record.to_dict())
        self.store.save_knowledge(knowledge)
        self.store.append_version(
            VersionRecord(
                version_id=new_id("ver"),
                entity_id=proposal_id,
                entity_type="proposal",
                version=str(row.get("version")),
                reviewer=reviewer,
                timestamp=now_iso(),
                diff=row.get("diff") or {},
                snapshot=row,
            ).to_dict()
        )
        return {"proposal": row, "gate": gate, "promoted_to_production": False}

    def reject(self, proposal_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        def mutate(row: dict[str, Any]) -> dict[str, Any]:
            row["status"] = ProposalStatus.REJECTED.value
            row["reviewer"] = reviewer
            row["reviewed_at"] = now_iso()
            meta = dict(row.get("metadata") or {})
            meta["reject_reason"] = reason
            row["metadata"] = meta
            row["diff"] = {"action": "reject", "reason": reason}
            return row

        row = self._update(proposal_id, mutate)
        self.store.append_version(
            VersionRecord(
                version_id=new_id("ver"),
                entity_id=proposal_id,
                entity_type="proposal",
                version=str(row.get("version") or "0.1.0"),
                reviewer=reviewer,
                timestamp=now_iso(),
                diff=row.get("diff") or {},
                snapshot=row,
            ).to_dict()
        )
        return {"proposal": row}

    def merge(self, proposal_id: str, into_proposal_id: str, reviewer: str) -> dict[str, Any]:
        rows = {r["proposal_id"]: r for r in self.store.list_proposals() if "proposal_id" in r}
        if proposal_id not in rows or into_proposal_id not in rows:
            raise ValueError("Both proposals required for merge")

        def mutate_source(row: dict[str, Any]) -> dict[str, Any]:
            row["status"] = ProposalStatus.MERGED.value
            row["reviewer"] = reviewer
            row["reviewed_at"] = now_iso()
            meta = dict(row.get("metadata") or {})
            meta["merged_into"] = into_proposal_id
            row["metadata"] = meta
            return row

        def mutate_target(row: dict[str, Any]) -> dict[str, Any]:
            src = rows[proposal_id]
            row["evidence"] = list(row.get("evidence") or []) + list(src.get("evidence") or [])
            row["evidence_count"] = len(row["evidence"])
            row["confidence"] = max(float(row.get("confidence") or 0), float(src.get("confidence") or 0))
            row["reviewer"] = reviewer
            row["reviewed_at"] = now_iso()
            row["status"] = ProposalStatus.EDITED.value
            row["diff"] = {"merged_from": proposal_id}
            return row

        return {
            "source": self._update(proposal_id, mutate_source),
            "target": self._update(into_proposal_id, mutate_target),
        }

    def edit(self, proposal_id: str, reviewer: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "title", "summary", "suggested_rule", "suggested_coaching",
            "suggested_sop_update", "expected_impact", "metadata",
        }

        def mutate(row: dict[str, Any]) -> dict[str, Any]:
            before = {k: row.get(k) for k in allowed}
            for k, v in patch.items():
                if k in allowed:
                    row[k] = v
            row["status"] = ProposalStatus.EDITED.value
            row["reviewer"] = reviewer
            row["reviewed_at"] = now_iso()
            row["diff"] = {"before": before, "patch": {k: patch[k] for k in patch if k in allowed}}
            return row

        row = self._update(proposal_id, mutate)
        self.store.append_version(
            VersionRecord(
                version_id=new_id("ver"),
                entity_id=proposal_id,
                entity_type="proposal",
                version=str(row.get("version") or "0.1.0"),
                reviewer=reviewer,
                timestamp=now_iso(),
                diff=row.get("diff") or {},
                snapshot=row,
            ).to_dict()
        )
        return {"proposal": row}

    def promote_to_production(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        row = next((r for r in self.store.list_proposals() if r.get("proposal_id") == proposal_id), None)
        if not row:
            raise ValueError(f"Proposal not found: {proposal_id}")
        if row.get("status") != ProposalStatus.APPROVED.value:
            raise ValueError("Only approved proposals can enter production")
        knowledge = self.store.get_knowledge()
        gate = self.gate.check(row, knowledge)
        if not gate["ok"]:
            return {"ok": False, "gate": gate, "promoted": False}
        record = KnowledgeRecord(
            record_id=new_id("kn"),
            layer=KnowledgeLayer.PRODUCTION,
            kind=str(row.get("kind")),
            content=row,
            version=str(row.get("version")),
            proposal_id=proposal_id,
            reviewer=reviewer,
        ).to_dict()
        knowledge[LAYER_KEY[KnowledgeLayer.PRODUCTION]].append(record)
        self.store.save_knowledge(knowledge)
        self.store.append_version(
            VersionRecord(
                version_id=new_id("ver"),
                entity_id=proposal_id,
                entity_type="production_knowledge",
                version=str(row.get("version")),
                reviewer=reviewer,
                timestamp=now_iso(),
                diff={"action": "promote_production"},
                snapshot=record,
            ).to_dict()
        )
        return {"ok": True, "gate": gate, "promoted": True, "record": record}

    def rollback(self, version_id: str, reviewer: str) -> dict[str, Any]:
        target = next((v for v in self.store.list_versions() if v.get("version_id") == version_id), None)
        if not target:
            raise ValueError(f"Version not found: {version_id}")
        snapshot = target.get("snapshot") or {}
        entity_id = str(target.get("entity_id") or "")
        if target.get("entity_type") == "proposal" and entity_id:
            self._update(entity_id, lambda _row: dict(snapshot))
        elif target.get("entity_type") == "production_knowledge":
            knowledge = self.store.get_knowledge()
            knowledge[LAYER_KEY[KnowledgeLayer.PRODUCTION]] = [
                r for r in knowledge[LAYER_KEY[KnowledgeLayer.PRODUCTION]]
                if r.get("proposal_id") != entity_id
            ]
            self.store.save_knowledge(knowledge)
        rb = VersionRecord(
            version_id=new_id("ver"),
            entity_id=entity_id,
            entity_type=str(target.get("entity_type")),
            version=str(target.get("version")),
            reviewer=reviewer,
            timestamp=now_iso(),
            diff={"action": "rollback", "from": version_id},
            snapshot=snapshot,
            rollback_of=version_id,
        )
        self.store.append_version(rb.to_dict())
        return {"ok": True, "rolled_back": version_id, "version": rb.to_dict()}

    def start_ab(self, rule_a: dict[str, Any], rule_b: dict[str, Any]) -> dict[str, Any]:
        row = {
            "ab_id": new_id("ab"),
            "rule_a": rule_a,
            "rule_b": rule_b,
            "metrics": {
                "a": {"accuracy": 0.0, "revenue_impact": 0.0, "qa_agreement": 0.0, "n": 0},
                "b": {"accuracy": 0.0, "revenue_impact": 0.0, "qa_agreement": 0.0, "n": 0},
            },
            "created_at": now_iso(),
            "status": "running",
        }
        self.store.append_ab(row)
        return row

    def record_ab_outcome(
        self,
        ab_id: str,
        arm: str,
        *,
        accuracy: float,
        revenue_impact: float,
        qa_agreement: float,
    ) -> dict[str, Any]:
        rows = self.store.list_ab()
        updated = None
        for i, row in enumerate(rows):
            if row.get("ab_id") != ab_id:
                continue
            metrics = dict(row.get("metrics") or {})
            arm_m = dict(metrics.get(arm) or {"accuracy": 0.0, "revenue_impact": 0.0, "qa_agreement": 0.0, "n": 0})
            n = int(arm_m.get("n") or 0) + 1
            arm_m["accuracy"] = round(((arm_m["accuracy"] * (n - 1)) + accuracy) / n, 4)
            arm_m["revenue_impact"] = round(((arm_m["revenue_impact"] * (n - 1)) + revenue_impact) / n, 4)
            arm_m["qa_agreement"] = round(((arm_m["qa_agreement"] * (n - 1)) + qa_agreement) / n, 4)
            arm_m["n"] = n
            metrics[arm] = arm_m
            row["metrics"] = metrics
            rows[i] = row
            updated = row
            break
        if updated is None:
            raise ValueError(f"A/B test not found: {ab_id}")
        self.store.rewrite_ab(rows)
        return updated
