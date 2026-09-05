"""Revenue leak estimation service."""

from __future__ import annotations

from typing import Any

from app.domain.entities import CallEntity, RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import ScoreItemResult


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
            return {
                "verdict": "Insufficient Evidence",
                "estimated_amount": None,
                "currency": currency,
                "components": [],
                "explanation": (
                    "Insufficient Evidence: missing crm_order_value; "
                    "cannot estimate revenue leak."
                ),
            }

        base = float(call.crm_order_value)
        components: list[dict[str, Any]] = []
        total_leak = 0.0

        for item in items:
            if item.verdict != Verdict.FAIL:
                continue
            rule = rules_by_code.get(item.rule_code)
            impact_code = rule.revenue_impact_code if rule else None
            # Deterministic fractions by severity — never invent without a fail
            fraction = {
                "critical": 0.35,
                "major": 0.15,
                "minor": 0.05,
                "info": 0.01,
            }.get(item.severity or "major", 0.1)
            amount = round(base * fraction, 2)
            total_leak += amount
            components.append(
                {
                    "rule_code": item.rule_code,
                    "revenue_impact_code": impact_code,
                    "amount": amount,
                    "fraction": fraction,
                    "severity": item.severity,
                }
            )

        if not components:
            return {
                "verdict": "none",
                "estimated_amount": 0.0,
                "currency": currency,
                "components": [],
                "explanation": "No failing revenue-impacting criteria.",
            }

        # Cap at order value
        total_leak = min(total_leak, base)
        return {
            "verdict": "estimated",
            "estimated_amount": round(total_leak, 2),
            "currency": currency,
            "components": components,
            "explanation": (
                f"Estimated leak {round(total_leak, 2)} {currency} "
                f"from {len(components)} failing criteria against order {base}."
            ),
        }
