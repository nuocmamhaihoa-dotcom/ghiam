"""Root cause analysis service."""

from __future__ import annotations

from typing import Any

from app.domain.entities import RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import ScoreItemResult


class RootCauseService:
    def analyze(
        self,
        items: list[ScoreItemResult],
        rules_by_code: dict[str, RuleEntity],
    ) -> dict[str, Any]:
        fails = [i for i in items if i.verdict == Verdict.FAIL]
        if not fails and all(
            i.verdict == Verdict.INSUFFICIENT_EVIDENCE for i in items if i.verdict != Verdict.NOT_APPLICABLE
        ):
            return {
                "verdict": "Insufficient Evidence",
                "primary_cause_code": None,
                "causes": [],
                "explanation": "Insufficient Evidence: cannot attribute root cause without scored failures.",
            }

        if not fails:
            return {
                "verdict": "none",
                "primary_cause_code": None,
                "causes": [],
                "explanation": "No failing criteria; no root cause identified.",
            }

        causes: list[dict[str, Any]] = []
        for item in sorted(fails, key=lambda x: x.weight, reverse=True):
            rule = rules_by_code.get(item.rule_code)
            code = (rule.cause_code_on_fail if rule else None) or f"RC-{item.rule_code}"
            causes.append(
                {
                    "cause_code": code,
                    "rule_code": item.rule_code,
                    "title": item.title,
                    "severity": item.severity,
                    "weight": item.weight,
                    "explanation": item.explanation,
                }
            )

        primary = causes[0]["cause_code"] if causes else None
        return {
            "verdict": "identified",
            "primary_cause_code": primary,
            "causes": causes[:10],
            "explanation": f"Primary root cause {primary} from {len(fails)} failing criteria.",
        }
