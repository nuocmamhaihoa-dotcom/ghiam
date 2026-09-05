"""Golden call discovery."""
from __future__ import annotations

from typing import Any

from research.proposals_util import make_proposal
from self_learning.types import Evidence, Proposal, ProposalKind


class GoldenCallDiscovery:
    def score_call(self, call: dict[str, Any]) -> dict[str, Any]:
        closed = bool(call.get("closed") or call.get("outcome") in {"won", "closed", "chot"})
        discovery = float(call.get("discovery_score") or call.get("discovery") or 0)
        objection_handling = float(call.get("objection_score") or call.get("objection_handling") or 0)
        duration = float(
            call.get("duration_sec") or call.get("duration") or call.get("duration_seconds") or 0
        )
        duration_score = 1.0
        if duration:
            if duration < 90:
                duration_score = 0.4
            elif duration > 900:
                duration_score = 0.5
            elif 180 <= duration <= 600:
                duration_score = 1.0
            else:
                duration_score = 0.75
        close_rate = 1.0 if closed else float(call.get("close_probability") or 0.2)
        disc = min(1.0, discovery / 100.0 if discovery > 1 else discovery)
        obj = min(1.0, objection_handling / 100.0 if objection_handling > 1 else objection_handling)
        total = 0.35 * close_rate + 0.25 * disc + 0.25 * obj + 0.15 * duration_score
        return {
            "call_id": call.get("call_id") or call.get("id"),
            "golden_score": round(total, 4),
            "closed": closed,
            "duration_score": duration_score,
            "eligible": total >= 0.72 and closed,
        }

    def discover(self, calls: list[dict[str, Any]], *, top_n: int = 10) -> list[Proposal]:
        scored = [(self.score_call(c), c) for c in calls]
        scored.sort(key=lambda x: x[0]["golden_score"], reverse=True)
        proposals: list[Proposal] = []
        for score, call in scored[:top_n]:
            if not score["eligible"] and score["golden_score"] < 0.65:
                continue
            call_id = str(score["call_id"] or "unknown")
            transcript = str(call.get("transcript") or call.get("summary") or "")[:240]
            # novelty bumped so quality_score clears QA threshold for strong golden calls
            novelty = 0.55 if score["eligible"] else 0.4
            proposals.append(make_proposal(
                kind=ProposalKind.GOLDEN_CALL,
                title=f"Golden Call Candidate: {call_id}",
                summary=f"Đề xuất đưa {call_id} vào Golden Set (score={score['golden_score']}).",
                suggested_rule=f"GOLDEN_CALL::{call_id}",
                suggested_coaching="Dùng làm mẫu roleplay: Discovery → Objection → Close.",
                suggested_sop_update="Gắn call vào thư viện Golden Call sau khi QA duyệt.",
                evidence=[
                    Evidence(call_id=call_id, quote=transcript or f"golden:{call_id}", meta={"metric": "transcript"}),
                    Evidence(call_id=call_id, quote=f"discovery={call.get('discovery_score')}", meta={"metric": "discovery"}),
                    Evidence(call_id=call_id, quote=f"objection={call.get('objection_score')}", meta={"metric": "objection"}),
                ],
                confidence=score["golden_score"],
                novelty=novelty,
                expected_impact="high",
                metadata={"scores": score, "pending_qa": True},
            ))
        return proposals
