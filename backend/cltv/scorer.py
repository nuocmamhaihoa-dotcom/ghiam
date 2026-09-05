"""CLTV scoring from call/CRM/conversation signals."""
from __future__ import annotations

from typing import Any

from cltv.types import CLTVScores


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _f(d: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                continue
    return default


def _s(d: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        if k in d and d[k] is not None:
            return str(d[k])
    return default


def score_cltv(
    *,
    lead_id: str = "",
    call_history: list[dict[str, Any]] | None = None,
    conversation_dna: dict[str, Any] | None = None,
    intent: str | dict[str, Any] | None = None,
    emotion: str | dict[str, Any] | None = None,
    buying_signal: float | dict[str, Any] | None = None,
    crm: dict[str, Any] | None = None,
    follow_up: dict[str, Any] | None = None,
) -> CLTVScores:
    _ = lead_id
    call_history = call_history or []
    conversation_dna = conversation_dna or {}
    crm = crm or {}
    follow_up = follow_up or {}

    if isinstance(intent, dict):
        intent_s = _s(intent, "label", "intent", default="unknown").lower()
        intent_conf = _f(intent, "confidence", default=0.5)
    else:
        intent_s = str(intent or "unknown").lower()
        intent_conf = 0.55

    if isinstance(emotion, dict):
        emotion_s = _s(emotion, "label", "emotion", default="neutral").lower()
        emotion_conf = _f(emotion, "confidence", default=0.5)
    else:
        emotion_s = str(emotion or "neutral").lower()
        emotion_conf = 0.5

    if isinstance(buying_signal, dict):
        buy = _f(buying_signal, "score", "value", default=0.4)
    else:
        buy = float(buying_signal if buying_signal is not None else 0.4)

    calls_n = len(call_history)
    connected = sum(
        1
        for c in call_history
        if c.get("connected") or c.get("answered") or float(c.get("duration") or 0) > 30
    )
    conversions = sum(
        1
        for c in call_history
        if c.get("converted") or c.get("outcome") in {"won", "closed", "purchased"}
    )
    objections = sum(int(c.get("objection_count") or 0) for c in call_history)
    avg_qa = 0.0
    if call_history:
        avg_qa = sum(_f(c, "qa_score", "score", default=70) for c in call_history) / calls_n / 100.0

    dna_rapport = _f(conversation_dna, "rapport", "rapport_score", default=0.5)
    dna_value = _f(conversation_dna, "value_building", "value_score", default=0.5)
    dna_close = _f(conversation_dna, "closing", "close_score", default=0.5)

    past_revenue = _f(crm, "past_revenue", "ltv_so_far", "revenue", default=0.0)
    tickets = _f(crm, "support_tickets", "tickets", default=0.0)
    tenure_days = _f(crm, "tenure_days", "days_as_customer", default=0.0)
    segment = _s(crm, "segment", "tier", default="standard").lower()
    products_owned = int(_f(crm, "products_owned", "product_count", default=1))
    aov = _f(crm, "aov", "avg_order_value", default=500_000) or 500_000

    followups_done = int(_f(follow_up, "completed", "done", default=0))
    followups_missed = int(_f(follow_up, "missed", "overdue", default=0))
    next_follow_hours = _f(follow_up, "next_in_hours", "eta_hours", default=72)

    engagement = _clamp(0.2 + 0.08 * connected + 0.05 * conversions + 0.15 * avg_qa)
    intent_boost = 0.2 if any(x in intent_s for x in ("buy", "purchase", "mua", "chốt")) else 0.0
    intent_boost -= 0.15 if any(x in intent_s for x in ("reject", "từ chối", "no")) else 0.0
    emotion_boost = 0.12 if emotion_s in {"positive", "curious", "interested"} else 0.0
    emotion_boost -= 0.12 if emotion_s in {"frustrated", "resistant", "angry"} else 0.0

    retention = _clamp(
        0.35
        + 0.25 * engagement
        + 0.15 * dna_rapport
        + 0.1 * min(1.0, tenure_days / 365)
        + 0.08 * followups_done
        - 0.12 * followups_missed
        - 0.05 * min(1.0, tickets / 5)
        + emotion_boost * 0.5
    )
    churn = _clamp(
        0.55
        - 0.35 * retention
        + 0.08 * min(1.0, objections / max(1, calls_n))
        + 0.1 * followups_missed
        + (0.12 if emotion_s in {"frustrated", "resistant"} else 0.0)
        + (0.08 if next_follow_hours > 168 else 0.0)
    )
    repeat_p = _clamp(retention * 0.7 + buy * 0.2 + conversions * 0.05 + intent_boost)
    upsell = _clamp(
        0.25
        + 0.3 * buy
        + 0.2 * dna_value
        + 0.15 * dna_close
        + 0.1 * (1 if segment in {"vip", "premium", "gold"} else 0)
        + intent_boost
    )
    cross_sell = _clamp(
        0.2
        + 0.25 * buy
        + 0.15 * products_owned * 0.1
        + 0.2 * dna_value
        + 0.1 * engagement
        + (0.1 if segment in {"vip", "premium"} else 0)
    )
    referral_p = _clamp(
        0.15
        + 0.35 * retention
        + 0.2 * (1 - churn)
        + 0.15 * dna_rapport
        + 0.1 * conversions
        + emotion_boost
    )

    expected_orders = 1 + repeat_p * 4 + upsell * 1.5 + cross_sell * 1.2
    lifetime_value = round(max(0.0, (past_revenue * 0.4) + aov * expected_orders * (1 - churn * 0.5)), 2)

    cltv_score = _clamp(
        0.25 * retention
        + 0.2 * (1 - churn)
        + 0.2 * min(1.0, lifetime_value / (aov * 6))
        + 0.15 * upsell
        + 0.1 * cross_sell
        + 0.1 * referral_p
    )

    if cltv_score >= 0.72 or lifetime_value >= aov * 4:
        priority = "high_value"
    elif cltv_score >= 0.45:
        priority = "medium"
    else:
        priority = "low"

    confidence = _clamp(
        0.45
        + 0.08 * min(5, calls_n)
        + 0.1 * intent_conf
        + 0.08 * emotion_conf
        + (0.1 if crm else 0.0)
        + (0.05 if conversation_dna else 0.0)
    )

    evidence = [
        f"calls={calls_n}",
        f"connected={connected}",
        f"conversions={conversions}",
        f"intent={intent_s}",
        f"emotion={emotion_s}",
        f"buy_signal={round(buy, 3)}",
        f"segment={segment}",
        f"past_revenue={past_revenue}",
        f"followups_done={followups_done}",
        f"followups_missed={followups_missed}",
    ]
    explanations = [
        f"Retention {retention:.2f} driven by engagement/rapport and follow-up discipline.",
        f"Churn risk {churn:.2f} rises with missed follow-ups, objections, and negative emotion.",
        f"LTV ~{lifetime_value:,.0f} from AOV×expected orders adjusted by churn.",
        f"Priority={priority} from composite CLTV score {cltv_score:.2f}.",
        f"Upsell/cross-sell reflect buying signal ({buy:.2f}) and value DNA.",
    ]
    return CLTVScores(
        cltv_score=round(cltv_score, 4),
        retention_score=round(retention, 4),
        upsell_score=round(upsell, 4),
        cross_sell_score=round(cross_sell, 4),
        referral_score=round(referral_p, 4),
        lifetime_value=lifetime_value,
        repeat_purchase_probability=round(repeat_p, 4),
        churn_risk=round(churn, 4),
        referral_probability=round(referral_p, 4),
        priority=priority,
        confidence=round(confidence, 4),
        evidence=evidence,
        explanations=explanations,
    )


def prioritize_leads(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"high_value": 0, "medium": 1, "low": 2}

    def key(p: dict[str, Any]) -> tuple:
        scores = p.get("scores") or p
        return (
            order.get(str(scores.get("priority") or "low"), 9),
            -float(scores.get("cltv_score") or 0),
            -float(scores.get("lifetime_value") or 0),
        )

    return sorted(predictions, key=key)
