"""Weekly auto-improvement proposals — never auto-merged to production."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import EvolutionProposal, ProposalKind, ProposalStatus, new_id, now_iso


class ProposalGenerator:
    """Generate Rule / Coaching / SOP / Memory proposals for QA review only."""

    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()

    def generate_weekly(
        self,
        *,
        signals: Optional[Mapping[str, Any]] = None,
        week_id: str | None = None,
    ) -> list[dict[str, Any]]:
        signals = dict(signals or {})
        week = week_id or now_iso()[:10]
        templates: list[tuple[ProposalKind, str, str, str]] = [
            (
                ProposalKind.RULE,
                "Tighten price-objection evidence gate",
                "Shadow deltas show root_cause churn on price objections.",
                "medium",
            ),
            (
                ProposalKind.COACHING,
                "Add reassurance micro-script for anxious buyers",
                "Emotion=anxious correlates with abandoned closes.",
                "high",
            ),
            (
                ProposalKind.SOP,
                "Update handoff SOP after buying_signal > 0.7",
                "Conversion improves when handoff happens within 2 turns of strong buying signal.",
                "medium",
            ),
            (
                ProposalKind.MEMORY,
                "Persist industry lexicon drift clusters",
                "Language drift alerts suggest stale memory graph nodes.",
                "low",
            ),
        ]

        created: list[dict[str, Any]] = []
        for kind, title, summary, impact in templates:
            conf = float(signals.get(f"confidence_{kind.value}", 0.72))
            prop = EvolutionProposal(
                proposal_id=new_id("eprop"),
                kind=kind,
                title=title,
                summary=summary,
                payload={
                    "week_id": week,
                    "kind": kind.value,
                    "suggested_change": title,
                    "auto_merge": False,
                },
                evidence=[
                    {"source": "shadow_reports", "count": int(signals.get("shadow_significant", 0))},
                    {"source": "drift_alerts", "count": int(signals.get("drift_alerts", 0))},
                    {"source": "failures", "count": int(signals.get("failures", 0))},
                ],
                confidence=conf,
                expected_impact=impact,
                status=ProposalStatus.PENDING_QA,
                metadata={"generator": "weekly", "auto_merge": False, "requires_qa": True},
            )
            row = prop.to_dict()
            self.store.append_proposal(row)
            created.append(row)
        return created

    def list_pending_qa(self) -> list[dict[str, Any]]:
        return [
            p
            for p in self.store.list_proposals()
            if p.get("status") == ProposalStatus.PENDING_QA.value
        ]

    def approve(self, proposal_id: str, reviewer: str) -> dict[str, Any]:
        """Approve proposal — does NOT promote to production."""
        return self.store.update_proposal(
            proposal_id,
            {
                "status": ProposalStatus.APPROVED.value,
                "reviewed_by": reviewer,
                "reviewed_at": now_iso(),
                "promoted_to_production": False,
            },
        )

    def reject(self, proposal_id: str, reviewer: str, reason: str = "") -> dict[str, Any]:
        return self.store.update_proposal(
            proposal_id,
            {
                "status": ProposalStatus.REJECTED.value,
                "reviewed_by": reviewer,
                "reviewed_at": now_iso(),
                "reject_reason": reason,
                "promoted_to_production": False,
            },
        )

    def promote(self, proposal_id: str, actor: str) -> dict[str, Any]:
        """Explicit second step after approval — records version, never silent."""
        rows = self.store.list_proposals()
        row = next((r for r in rows if r.get("proposal_id") == proposal_id), None)
        if row is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        if row.get("status") != ProposalStatus.APPROVED.value:
            raise ValueError("Proposal must be APPROVED before promotion to production")
        updated = self.store.update_proposal(
            proposal_id,
            {
                "status": ProposalStatus.PRODUCTION.value,
                "promoted_to_production": True,
                "promoted_by": actor,
                "promoted_at": now_iso(),
            },
        )
        self.store.append_version(
            {
                "version_id": new_id("ver"),
                "artifact_id": proposal_id,
                "artifact_type": str(row.get("kind") or "rule"),
                "version": f"prod-{now_iso()[:10]}",
                "payload": dict(row.get("payload") or {}),
                "created_at": now_iso(),
                "created_by": actor,
                "active": True,
                "metadata": {"from_proposal": proposal_id},
            }
        )
        return updated
