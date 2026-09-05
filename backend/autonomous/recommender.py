"""Next Best Action recommender."""
from __future__ import annotations

from typing import Any

from autonomous.types import NBA_ACTIONS, Recommendation, new_id


def recommend_nba(context: dict[str, Any]) -> Recommendation:
    buy = float(context.get("buy_signal") or context.get("buying_signal") or 0)
    sentiment = str(context.get("sentiment") or context.get("emotion") or "neutral").lower()
    objection = context.get("objection")
    intent = str(context.get("intent") or "").lower()
    missed = int((context.get("follow_up") or {}).get("missed") or context.get("missed_followups") or 0)
    qa = float(context.get("qa_score") or 0)
    churn = float(context.get("churn_risk") or 0)
    cltv = float(context.get("cltv_score") or 0)

    evidence: list[str] = []
    action = "callback"
    confidence = 0.55
    impact = 0.4
    rationale = "Default callback to keep the opportunity warm."

    if buy >= 0.75 or intent in {"buy", "purchase", "ready"}:
        action = "send_proposal" if buy >= 0.85 else "close_lead"
        confidence = min(0.97, 0.7 + buy * 0.25)
        impact = 0.85
        rationale = "Strong buying signal — move to close / proposal."
        evidence.append(f"buy_signal={buy}")
    elif objection and sentiment in {"frustrated", "resistant", "negative"}:
        action = "escalate_leader" if churn >= 0.6 or qa < 50 else "callback"
        confidence = 0.72
        impact = 0.55
        rationale = "Hard objection with negative emotion — escalate or careful callback."
        evidence.extend([f"objection={objection}", f"sentiment={sentiment}"])
    elif missed >= 2:
        action = "zalo_message" if missed < 4 else "email"
        confidence = 0.68
        impact = 0.5
        rationale = "Multiple missed follow-ups — switch channel."
        evidence.append(f"missed_followups={missed}")
    elif cltv >= 0.75 and buy >= 0.45:
        action = "reassign_sale"
        confidence = 0.7
        impact = 0.65
        rationale = "High CLTV opportunity — assign strongest closer."
        evidence.append(f"cltv_score={cltv}")
    elif 0 < qa < 60:
        action = "coaching_nudge"
        confidence = 0.66
        impact = 0.45
        rationale = "Low QA — coach before next contact."
        evidence.append(f"qa_score={qa}")
    elif buy >= 0.45:
        action = "callback"
        confidence = 0.64
        impact = 0.5
        rationale = "Moderate interest — schedule callback."
        evidence.append(f"buy_signal={buy}")
    else:
        action = "email"
        confidence = 0.58
        impact = 0.35
        rationale = "Low urgency — nurture via email."
        evidence.append(f"buy_signal={buy}")

    if action not in NBA_ACTIONS:
        action = "callback"
    if not evidence:
        evidence.append("context_default")

    return Recommendation(
        recommendation_id=new_id("nba"),
        action=action,
        confidence=round(confidence, 4),
        rationale=rationale,
        evidence=evidence,
        expected_impact=round(impact, 4),
        requires_approval=False,
    )


def map_action_to_automation(action: str) -> str | None:
    mapping = {
        "callback": "callback_reminder",
        "zalo_message": "follow_up_message",
        "email": "follow_up_message",
        "coaching_nudge": "coaching_nudge",
        "close_lead": "crm_note",
        "send_proposal": "create_task",
        "reassign_sale": "create_task",
        "escalate_leader": "create_task",
    }
    return mapping.get(action)
