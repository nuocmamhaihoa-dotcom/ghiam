"""Coaching plan generation service."""

from __future__ import annotations

from typing import Any

from app.domain.entities import RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import ScoreItemResult


class CoachingService:
    def generate_call_tips(
        self,
        items: list[ScoreItemResult],
        rules_by_code: dict[str, RuleEntity],
        root_cause: dict[str, Any],
    ) -> dict[str, Any]:
        if root_cause.get("verdict") == "Insufficient Evidence":
            return {
                "plan_id": None,
                "call_tips": [],
                "explanation": "Insufficient Evidence: coaching deferred until evidence exists.",
            }

        tips: list[dict[str, Any]] = []
        for item in items:
            if item.verdict not in {Verdict.FAIL, Verdict.INSUFFICIENT_EVIDENCE}:
                continue
            rule = rules_by_code.get(item.rule_code)
            template = rule.coaching_template_code if rule else None
            cause = (rule.cause_code_on_fail if rule else None) or f"RC-{item.rule_code}"
            tips.append(
                {
                    "cause_code": cause,
                    "rule_code": item.rule_code,
                    "title": f"Improve: {item.title}",
                    "action_markdown": (
                        f"Review criterion **{item.rule_code}** ({item.title}). "
                        f"Focus: {item.explanation}"
                    ),
                    "template_code": template,
                    "priority": item.severity or "major",
                }
            )

        tips = tips[:8]
        return {
            "plan_id": None,
            "call_tips": tips,
            "explanation": (
                f"Generated {len(tips)} coaching tips from scored misses."
                if tips
                else "No coaching tips; call met applicable criteria."
            ),
        }
