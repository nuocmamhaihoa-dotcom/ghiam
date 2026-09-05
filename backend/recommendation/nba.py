"""Next Best Action engine with confidence, evidence, and revenue impact."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

NBA_DECISIONS = (
    "call_now",
    "callback",
    "zalo_message",
    "email",
    "reassign_sale",
    "escalate_leader",
    "close_lead",
    "send_proposal",
    "coaching_nudge",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass
class NextBestAction:
    action: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    expected_revenue_impact: float = 0.0
    rationale: str = ""
    recommendation_id: str = field(default_factory=lambda: _id("nba"))
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expected_impact"] = self.expected_revenue_impact
        return payload


class NextBestActionEngine:
    def recommend(self, context: dict[str, Any]) -> NextBestAction:
        return recommend_next_best_action(context)


def recommend_next_best_action(context: dict[str, Any]) -> NextBestAction:
    buy = float(context.get("buy_signal") or context.get("buying_signal") or 0)
    sentiment = str(context.get("sentiment") or context.get("emotion") or "neutral").lower()
    objection = context.get("objection")
    intent = str(context.get("intent") or "").lower()
    missed = int(
        (context.get("follow_up") or {}).get("missed")
        or context.get("missed_followups")
        or 0
    )
    qa = float(context.get("qa_score") or 0)
    churn = float(context.get("churn_risk") or 0)
    cltv = float(context.get("cltv_score") or 0)
    deal = float(context.get("deal_value") or context.get("expected_value") or 5_000_000)
    hot = bool(context.get("hot_lead") or context.get("is_hot"))

    evidence: list[str] = []
    action = "callback"
    confidence = 0.55
    impact_ratio = 0.4
    rationale = "Default callback to keep the opportunity warm."

    if hot or buy >= 0.9 or intent in {"buy", "purchase", "ready"}:
        if hot or buy >= 0.92 or intent == "ready":
            action = "call_now"
            confidence = min(0.98, 0.75 + buy * 0.22)
            impact_ratio = 0.9
            rationale = "Hot buying signal — call immediately."
        else:
            action = "send_proposal" if buy >= 0.85 else "close_lead"
            confidence = min(0.97, 0.7 + buy * 0.25)
            impact_ratio = 0.85
            rationale = "Strong buying signal — move to close / proposal."
        evidence.append(f"buy_signal={buy}")
        if hot:
            evidence.append("hot_lead=true")
    elif objection and sentiment in {"frustrated", "resistant", "negative"}:
        action = "escalate_leader" if churn >= 0.6 or qa < 50 else "callback"
        confidence = 0.72
        impact_ratio = 0.55
        rationale = "Hard objection with negative emotion — escalate or careful callback."
        evidence.extend([f"objection={objection}", f"sentiment={sentiment}"])
    elif missed >= 2:
        action = "zalo_message" if missed < 4 else "email"
        confidence = 0.68
        impact_ratio = 0.5
        rationale = "Multiple missed follow-ups — switch channel."
        evidence.append(f"missed_followups={missed}")
    elif cltv >= 0.75 and buy >= 0.45:
        action = "reassign_sale"
        confidence = 0.7
        impact_ratio = 0.65
        rationale = "High CLTV opportunity — assign strongest closer."
        evidence.append(f"cltv_score={cltv}")
    elif 0 < qa < 60:
        action = "coaching_nudge"
        confidence = 0.66
        impact_ratio = 0.45
        rationale = "Low QA — coach before next contact."
        evidence.append(f"qa_score={qa}")
    elif buy >= 0.45:
        action = "callback"
        confidence = 0.64
        impact_ratio = 0.5
        rationale = "Moderate interest — schedule callback."
        evidence.append(f"buy_signal={buy}")
    else:
        action = "email"
        confidence = 0.58
        impact_ratio = 0.35
        rationale = "Low urgency — nurture via email."
        evidence.append(f"buy_signal={buy}")

    if action not in NBA_DECISIONS:
        action = "callback"
    if not evidence:
        evidence.append("context_default")

    expected_revenue_impact = round(deal * impact_ratio * confidence, 2)
    return NextBestAction(
        action=action,
        confidence=round(confidence, 4),
        evidence=evidence,
        expected_revenue_impact=expected_revenue_impact,
        rationale=rationale,
    )
