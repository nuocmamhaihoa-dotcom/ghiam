"""New intent discovery."""
from __future__ import annotations

from research.proposals_util import make_proposal
from self_learning.types import Cluster, PatternHit, Proposal, ProposalKind


class IntentDiscovery:
    def discover(self, patterns: list[PatternHit], clusters: list[Cluster]) -> list[Proposal]:
        out: list[Proposal] = []
        for c in clusters:
            if c.size < 2 and c.confidence < 0.7:
                continue
            seed = (c.name or "").lower()
            intent_name = c.name.replace(" ", "_").upper()
            trigger = ", ".join(c.variants[:3]) or c.name
            counter = "Không ép chốt — xác nhận timeline và lợi ích cụ thể."
            coaching = f"Khi gặp cụm «{c.name}»: {c.hidden_meaning}"
            out.append(make_proposal(
                kind=ProposalKind.INTENT,
                title=f"New Intent: {c.name}",
                summary=f"Intent mới từ cluster — {c.hidden_meaning}",
                suggested_rule=f"INTENT::{intent_name}::trigger[{trigger}]",
                suggested_coaching=coaching,
                suggested_sop_update=f"Thêm intent {c.name} vào playbook sau QA. Counter: {counter}",
                evidence=list(c.evidence)[:5],
                confidence=c.confidence,
                novelty=0.65,
                expected_impact="high" if "Pay" in c.name or "Ghost" in c.name else "medium",
                metadata={"cluster_id": c.cluster_id, "trigger": trigger, "counter_example": counter, "pending_qa": True},
            ))
        # novel buying/objection patterns without cluster also become intent candidates
        for p in patterns:
            if p.known or p.novelty < 0.6:
                continue
            if not p.kind.startswith("buying") and p.kind != "objection":
                continue
            out.append(make_proposal(
                kind=ProposalKind.INTENT,
                title=f"Emerging Intent from pattern: {p.text[:50]}",
                summary=f"Pattern {p.kind} có novelty cao — có thể là intent mới.",
                suggested_rule=f"INTENT::EMERGING::{p.normalized[:40]}",
                suggested_coaching=f"Thử nghiệm phản xạ với «{p.text}».",
                suggested_sop_update="Gắn tag intent tạm, chờ QA đặt tên chính thức.",
                evidence=list(p.evidence),
                confidence=p.confidence,
                novelty=p.novelty,
                expected_impact="medium",
                metadata={"pattern_id": p.pattern_id, "pending_qa": True},
            ))
        return out
