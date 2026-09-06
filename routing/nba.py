"""Next Best Action engine after each call."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ActionType = Literal[
    "callback",
    "zalo_message",
    "email",
    "escalate_leader",
    "reassign_sale",
    "close_lead",
]


@dataclass(slots=True)
class NextBestAction:
    action: ActionType
    confidence: float
    evidence: list[dict[str, Any]]
    expected_impact: str
    schedule_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NextBestActionEngine:
    """Decide post-call action with confidence, evidence, expected impact."""

    ACTIONS: tuple[ActionType, ...] = (
        "callback",
        "zalo_message",
        "email",
        "escalate_leader",
        "reassign_sale",
        "close_lead",
    )

    def decide(self, call: dict[str, Any]) -> NextBestAction:
        outcome = str(call.get("outcome") or call.get("status") or "").lower()
        sentiment = float(call.get("sentiment") or 0.5)
        objections = list(call.get("objections") or [])
        buy_signals = int(call.get("buy_signals") or 0)
        attempts = int(call.get("attempts") or 1)
        score = float(call.get("score") or call.get("call_score") or 50)
        hours_since = float(call.get("hours_since_last") or 0)
        preferred_channel = str(call.get("preferred_channel") or "phone").lower()

        evidence: list[dict[str, Any]] = [
            {"field": "outcome", "value": outcome},
            {"field": "sentiment", "value": sentiment},
            {"field": "objections", "count": len(objections)},
            {"field": "buy_signals", "value": buy_signals},
            {"field": "attempts", "value": attempts},
            {"field": "score", "value": score},
        ]

        # Close if explicit reject / exhausted
        if outcome in {"rejected", "do_not_call", "closed_lost"} or (attempts >= 6 and sentiment < 0.35):
            return NextBestAction(
                action="close_lead",
                confidence=round(min(0.95, 0.7 + attempts * 0.04), 3),
                evidence=evidence + [{"rule": "exhausted_or_rejected"}],
                expected_impact="Stop revenue leak from unproductive dials; free agent capacity.",
            )

        # Escalate when high value + stuck
        value = float(call.get("lead_value") or 0)
        if value >= 50_000_000 and (outcome in {"stalled", "need_manager"} or len(objections) >= 3):
            return NextBestAction(
                action="escalate_leader",
                confidence=0.82,
                evidence=evidence + [{"rule": "high_value_stalled", "lead_value": value}],
                expected_impact="Leader intervention lifts close probability on high-value deal.",
            )

        # Reassign on DNA mismatch / low performance
        if call.get("dna_mismatch") or (score < 40 and attempts >= 3):
            return NextBestAction(
                action="reassign_sale",
                confidence=0.78,
                evidence=evidence + [{"rule": "poor_fit_or_low_score"}],
                expected_impact="Fresh agent with better DNA/skill fit increases conversion.",
            )

        # Strong buy signals → callback soon
        if buy_signals >= 2 or outcome in {"interested", "callback_requested"}:
            delay_h = 2 if buy_signals >= 3 else 4
            return NextBestAction(
                action="callback",
                confidence=round(min(0.93, 0.7 + buy_signals * 0.05 + sentiment * 0.1), 3),
                evidence=evidence + [{"rule": "buy_signal_callback", "delay_hours": delay_h}],
                expected_impact=f"Timely callback within {delay_h}h captures warm intent.",
                schedule_at=f"+{delay_h}h",
                metadata={"channel": "phone"},
            )

        # Soft follow-up channels
        if preferred_channel in {"zalo", "zalo_oa"} or "price" in " ".join(str(o) for o in objections).lower():
            return NextBestAction(
                action="zalo_message",
                confidence=0.74,
                evidence=evidence + [{"rule": "async_nurture_zalo"}],
                expected_impact="Zalo follow-up nurtures without interrupt; shares collateral.",
                schedule_at="+1h",
            )

        if preferred_channel == "email" or hours_since >= 24:
            return NextBestAction(
                action="email",
                confidence=0.71,
                evidence=evidence + [{"rule": "email_nurture"}],
                expected_impact="Email delivers proposal/SOP summary and books next slot.",
                schedule_at="+3h",
            )

        # Default: schedule callback in peak window
        return NextBestAction(
            action="callback",
            confidence=0.66,
            evidence=evidence + [{"rule": "default_callback"}],
            expected_impact="Maintain pipeline cadence; avoid stale leads.",
            schedule_at="+24h",
        )


__all__ = ["NextBestAction", "NextBestActionEngine"]
