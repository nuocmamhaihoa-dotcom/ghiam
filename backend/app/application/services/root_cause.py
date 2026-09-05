"""Root cause analysis — evidence-bound, never invents causes without FAIL items."""

from __future__ import annotations

from typing import Any

from app.domain.entities import RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import ScoreItemResult

_CAUSE_LABELS: dict[str, str] = {
    "RC-OPEN-GREETING": "Thiếu chào hỏi / xác nhận danh xưng mở đầu",
    "RC-DISC-SHALLOW": "Discovery nông — chưa đào nhu cầu thật",
    "RC-PITCH-EARLY": "Pitch / báo giá quá sớm",
    "RC-OBJ-PRICE-UNHANDLED": "Objection giá chưa được xử lý",
    "RC-OBJ-TRUST-UNHANDLED": "Objection niềm tin chưa được xử lý",
    "RC-OBJ-TIME-UNHANDLED": "Objection trì hoãn chưa được đào sâu",
    "RC-CLOSE-NO-ASK": "Không hỏi chốt đơn khi có tín hiệu",
    "RC-COMPLIANCE": "Vi phạm tuân thủ / cam kết sai",
    "RC-SOFT-SKILL": "Kỹ năng mềm / kiểm soát cảm xúc yếu",
}


def _label_for(code: str, fallback_title: str) -> str:
    return _CAUSE_LABELS.get(code) or fallback_title


class RootCauseService:
    def analyze(
        self,
        items: list[ScoreItemResult],
        rules_by_code: dict[str, RuleEntity],
    ) -> dict[str, Any]:
        fails = [i for i in items if i.verdict == Verdict.FAIL]
        applicable = [i for i in items if i.verdict != Verdict.NOT_APPLICABLE]
        ie_only = applicable and all(
            i.verdict == Verdict.INSUFFICIENT_EVIDENCE for i in applicable
        )

        if not fails and ie_only:
            return self._insufficient(
                "Insufficient Evidence: cannot attribute root cause without scored failures."
            )

        if not fails:
            return {
                "verdict": "none",
                "status": "ok",
                "primary_code": None,
                "primary_cause_code": None,
                "label": None,
                "confidence": None,
                "contributing_factors": [],
                "evidence_refs": [],
                "causes": [],
                "children": [],
                "explanation": "No failing criteria; no root cause identified.",
                "reason": "No failing criteria; no root cause identified.",
            }

        causes: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        evidence_refs: list[str] = []
        total_weight = sum(max(i.weight, 0.01) for i in fails) or 1.0

        for item in sorted(fails, key=lambda x: x.weight, reverse=True):
            rule = rules_by_code.get(item.rule_code)
            code = (rule.cause_code_on_fail if rule else None) or f"RC-{item.rule_code}"
            weight_share = round(item.weight / total_weight, 4)
            span_ids = [
                str(span.id)
                for span in item.evidence_spans
                if span.id is not None
            ]
            evidence_refs.extend(span_ids)
            label = _label_for(code, item.title)
            cause_row = {
                "cause_code": code,
                "code": code,
                "rule_code": item.rule_code,
                "title": item.title,
                "label": label,
                "severity": item.severity,
                "weight": item.weight,
                "weight_share": weight_share,
                "explanation": item.explanation,
                "evidence_refs": span_ids,
            }
            causes.append(cause_row)
            children.append(
                {
                    "code": code,
                    "label": label,
                    "weight": weight_share,
                    "children": [
                        {
                            "code": item.rule_code,
                            "label": item.title,
                            "weight": weight_share,
                        }
                    ],
                }
            )

        primary = causes[0]
        primary_code = primary["cause_code"]
        top_weight = primary["weight"]
        confidence = round(min(0.95, 0.55 + (top_weight / total_weight) * 0.4), 3)
        contributing = [c["cause_code"] for c in causes[1:6]]
        explanation = (
            f"Primary root cause {primary_code} ({primary['label']}) "
            f"from {len(fails)} failing criteria."
        )

        return {
            "verdict": "identified",
            "status": "ok",
            "primary_code": primary_code,
            "primary_cause_code": primary_code,
            "label": primary["label"],
            "confidence": confidence,
            "contributing_factors": contributing,
            "evidence_refs": list(dict.fromkeys(evidence_refs))[:40],
            "causes": causes[:10],
            "children": children[:10],
            "explanation": explanation,
            "reason": explanation,
        }

    @staticmethod
    def _insufficient(explanation: str) -> dict[str, Any]:
        return {
            "verdict": "Insufficient Evidence",
            "status": "Insufficient Evidence",
            "primary_code": None,
            "primary_cause_code": None,
            "label": None,
            "confidence": None,
            "contributing_factors": [],
            "evidence_refs": [],
            "causes": [],
            "children": [],
            "explanation": explanation,
            "reason": explanation,
        }
