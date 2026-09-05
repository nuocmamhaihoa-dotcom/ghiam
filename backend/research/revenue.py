"""Revenue leak discovery."""
from __future__ import annotations

from typing import Any

from research.proposals_util import make_proposal
from self_learning.types import Evidence, Proposal, ProposalKind


class RevenueLeakDiscovery:
    def propose(self, calls: list[dict[str, Any]], *, week_id: str = "current") -> list[Proposal]:
        lost = 0.0
        leaks: list[dict[str, Any]] = []
        for call in calls:
            closed = bool(call.get("closed") or call.get("outcome") in {"won", "closed", "chot"})
            value = float(call.get("deal_value") or call.get("amount") or call.get("revenue") or 0)
            if closed or value <= 0:
                continue
            reason = str(call.get("loss_reason") or call.get("root_cause") or "unspecified")
            lost += value
            leaks.append({
                "call_id": call.get("call_id") or call.get("id"),
                "value": value,
                "reason": reason,
            })
        if not leaks:
            # still emit a lightweight weekly scan proposal when corpus empty of explicit leaks
            return []
        top_reasons: dict[str, float] = {}
        for row in leaks:
            top_reasons[row["reason"]] = top_reasons.get(row["reason"], 0.0) + float(row["value"])
        ranked = sorted(top_reasons.items(), key=lambda x: x[1], reverse=True)
        evidence = [
            Evidence(call_id=str(r["call_id"] or "unknown"), quote=f"lost={r['value']} reason={r['reason']}")
            for r in leaks[:8]
        ]
        summary = (
            f"Tuần {week_id}: phát hiện {len(leaks)} revenue leak, tổng mất ~{lost:,.0f}. "
            f"Top nguyên nhân: {', '.join(f'{k}({v:,.0f})' for k, v in ranked[:3])}."
        )
        return [make_proposal(
            kind=ProposalKind.REVENUE_LEAK,
            title=f"Revenue Leak Report — {week_id}",
            summary=summary,
            suggested_rule=f"REVENUE_LEAK::{week_id}::top={ranked[0][0] if ranked else 'n/a'}",
            suggested_coaching="Ưu tiên coaching theo root cause doanh thu mất lớn nhất trong tuần.",
            suggested_sop_update="Cập nhật weekly revenue war-room checklist sau QA.",
            evidence=evidence,
            confidence=min(0.95, 0.7 + 0.02 * len(leaks)),
            novelty=0.7,
            expected_impact="high",
            metadata={"week_id": week_id, "report": {"lost_revenue": lost, "leak_count": len(leaks), "top_reasons": ranked}, "pending_qa": True},
        )]
