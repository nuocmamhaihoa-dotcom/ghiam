"""Revenue leak estimation — only from FAIL items + known order value."""

from __future__ import annotations

from typing import Any

from app.domain.entities import CallEntity, RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import ScoreItemResult

_SEVERITY_FRACTION = {
    "critical": 0.35,
    "major": 0.15,
    "minor": 0.05,
    "info": 0.01,
}

_LEAK_CODE_BY_CAUSE: dict[str, str] = {
    "RC-OBJ-PRICE-UNHANDLED": "LEAK_PRICE_OBJECTION_MISS",
    "RC-CLOSE-NO-ASK": "LEAK_SOFT_CLOSE_MISSING",
    "RC-DISC-SHALLOW": "LEAK_SHALLOW_DISCOVERY",
    "RC-PITCH-EARLY": "LEAK_EARLY_PITCH",
    "RC-OBJ-TRUST-UNHANDLED": "LEAK_TRUST_GAP",
    "RC-OBJ-TIME-UNHANDLED": "LEAK_DELAY_UNHANDLED",
    "RC-COMPLIANCE": "LEAK_COMPLIANCE_BLOCK",
}


class RevenueLeakService:
    def estimate(
        self,
        *,
        call: CallEntity,
        items: list[ScoreItemResult],
        rules_by_code: dict[str, RuleEntity],
    ) -> dict[str, Any]:
        currency = call.currency or "VND"
        if call.crm_order_value is None:
            return self._insufficient(
                currency,
                "Insufficient Evidence: missing crm_order_value; cannot estimate revenue leak.",
            )

        base = float(call.crm_order_value)
        components: list[dict[str, Any]] = []
        leak_codes: list[str] = []
        evidence_refs: list[str] = []
        total_leak = 0.0
        probability_mass = 0.0

        for item in items:
            if item.verdict != Verdict.FAIL:
                continue
            rule = rules_by_code.get(item.rule_code)
            impact_code = rule.revenue_impact_code if rule else None
            cause = (rule.cause_code_on_fail if rule else None) or f"RC-{item.rule_code}"
            leak_code = (
                impact_code
                or _LEAK_CODE_BY_CAUSE.get(cause)
                or f"LEAK_{item.rule_code}"
            )
            fraction = _SEVERITY_FRACTION.get((item.severity or "major").lower(), 0.1)
            amount = round(base * fraction, 2)
            total_leak += amount
            probability_mass += fraction
            span_ids = [
                str(span.id) for span in item.evidence_spans if span.id is not None
            ]
            evidence_refs.extend(span_ids)
            leak_codes.append(leak_code)
            components.append(
                {
                    "rule_code": item.rule_code,
                    "revenue_impact_code": impact_code,
                    "leak_code": leak_code,
                    "cause_code": cause,
                    "amount": amount,
                    "fraction": fraction,
                    "severity": item.severity,
                    "evidence_refs": span_ids,
                }
            )

        if not components:
            return {
                "verdict": "none",
                "status": "ok",
                "estimated_amount": 0.0,
                "estimated_loss_vnd": 0.0,
                "currency": currency,
                "leak_codes": [],
                "probability": 0.0,
                "components": [],
                "evidence_refs": [],
                "explanation": "No failing revenue-impacting criteria.",
            }

        total_leak = min(total_leak, base)
        probability = round(min(0.95, 0.35 + probability_mass * 0.5), 3)
        codes = list(dict.fromkeys(leak_codes))
        explanation = (
            f"Estimated leak {round(total_leak, 2)} {currency} "
            f"from {len(components)} failing criteria against order {base}."
        )
        return {
            "verdict": "estimated",
            "status": "ok",
            "estimated_amount": round(total_leak, 2),
            "estimated_loss_vnd": round(total_leak, 2),
            "currency": currency,
            "leak_codes": codes,
            "probability": probability,
            "components": components,
            "evidence_refs": list(dict.fromkeys(evidence_refs))[:40],
            "explanation": explanation,
        }

    @staticmethod
    def _insufficient(currency: str, explanation: str) -> dict[str, Any]:
        return {
            "verdict": "Insufficient Evidence",
            "status": "Insufficient Evidence",
            "estimated_amount": None,
            "estimated_loss_vnd": None,
            "currency": currency,
            "leak_codes": [],
            "probability": None,
            "components": [],
            "evidence_refs": [],
            "explanation": explanation,
        }
