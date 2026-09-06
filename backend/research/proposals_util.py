"""Shared Proposal builder."""
from __future__ import annotations

from typing import Any

from self_learning.types import (
    Evidence, KnowledgeLayer, Proposal, ProposalKind, ProposalStatus, new_id, now_iso,
)


def make_proposal(
    *,
    kind: ProposalKind,
    title: str,
    summary: str,
    suggested_rule: str,
    suggested_coaching: str,
    suggested_sop_update: str,
    evidence: list[Evidence] | None = None,
    confidence: float = 0.75,
    novelty: float = 0.5,
    expected_impact: str = "medium",
    metadata: dict[str, Any] | None = None,
) -> Proposal:
    evidence = evidence or [Evidence(call_id="corpus", quote=title)]
    return Proposal(
        proposal_id=new_id("prop"),
        kind=kind,
        title=title,
        summary=summary,
        suggested_rule=suggested_rule,
        suggested_coaching=suggested_coaching,
        suggested_sop_update=suggested_sop_update,
        evidence=evidence,
        confidence=round(confidence, 4),
        novelty=round(novelty, 4),
        evidence_count=len(evidence),
        qa_agreement_prediction=round(min(0.95, 0.5 + novelty * 0.3 + confidence * 0.2), 4),
        expected_impact=expected_impact,
        status=ProposalStatus.PENDING,
        layer=KnowledgeLayer.VERIFIED,
        created_at=now_iso(),
        metadata=metadata or {"pending_qa": True},
    )
