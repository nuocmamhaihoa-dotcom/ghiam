"""Coaching engine — tips and drills bound to failed rules and evidence."""

from __future__ import annotations

from typing import Any

from app.domain.entities import RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import ScoreItemResult

_SEVERITY_PRIORITY = {
    "critical": "high",
    "major": "high",
    "minor": "medium",
    "info": "low",
}

_DRILL_BY_PREFIX: list[tuple[str, str]] = [
    ("OPEN", "drill.opening.greeting"),
    ("DISC", "drill.discovery.need_probe"),
    ("PITCH", "drill.pitch.value_stack"),
    ("OBJ", "drill.objection.price_reframe"),
    ("CLOSE", "drill.close.ask_commit"),
    ("COMP", "drill.compliance.claim_check"),
]


def _drill_for(rule_code: str, template: str | None) -> str:
    if template:
        return f"drill.{template.lower()}"
    upper = rule_code.upper()
    for prefix, drill in _DRILL_BY_PREFIX:
        if prefix in upper:
            return drill
    return "drill.general.replay_miss"


def _script_for(item: ScoreItemResult, cause: str) -> str:
    snippets = {
        "RC-OBJ-PRICE-UNHANDLED": (
            "Dạ em hiểu chị đang cân nhắc về giá. Cho em hỏi thêm: phần chị thấy "
            "chưa tương xứng nhất là chi phí hay hiệu quả sử dụng ạ? Em so với "
            "chi phí mỗi lần dùng để chị dễ quyết định."
        ),
        "RC-CLOSE-NO-ASK": (
            "Vậy để chốt đúng nhu cầu của chị, em giao trong hôm nay hay để "
            "cuối tuần ạ? Chị nhận bao nhiêu sản phẩm cho tiện nhất?"
        ),
        "RC-DISC-SHALLOW": (
            "Để em tư vấn sát hơn: hiện chị đang dùng gì, và điều chị muốn cải "
            "thiện nhất sau 2–4 tuần là gì ạ?"
        ),
        "RC-OPEN-GREETING": (
            "Dạ em chào chị, em [Tên] bên [Công ty]. Chị đang nghe máy thuận "
            "tiện không ạ? Em xin phép trao đổi trong 2 phút."
        ),
    }
    if cause in snippets:
        return snippets[cause]
    return (
        f"Ôn lại tiêu chí **{item.rule_code}** ({item.title}). "
        f"Khi gặp tình huống tương tự, nhắc lại bằng chứng vừa thiếu: {item.explanation}"
    )


class CoachingService:
    def generate_call_tips(
        self,
        items: list[ScoreItemResult],
        rules_by_code: dict[str, RuleEntity],
        root_cause: dict[str, Any],
    ) -> dict[str, Any]:
        if root_cause.get("verdict") == "Insufficient Evidence" or root_cause.get(
            "status"
        ) == "Insufficient Evidence":
            return {
                "plan_id": None,
                "priority": "low",
                "tips": [],
                "call_tips": [],
                "drill_ids": [],
                "explanation": "Insufficient Evidence: coaching deferred until evidence exists.",
            }

        tips: list[dict[str, Any]] = []
        call_tips: list[dict[str, Any]] = []
        drill_ids: list[str] = []
        worst_severity = "info"

        for item in items:
            if item.verdict not in {Verdict.FAIL, Verdict.INSUFFICIENT_EVIDENCE}:
                continue
            rule = rules_by_code.get(item.rule_code)
            template = rule.coaching_template_code if rule else None
            cause = (rule.cause_code_on_fail if rule else None) or f"RC-{item.rule_code}"
            severity = (item.severity or "major").lower()
            if _SEVERITY_PRIORITY.get(severity, "medium") == "high":
                worst_severity = "critical" if severity == "critical" else (
                    "critical" if worst_severity == "critical" else "major"
                )
            elif worst_severity == "info":
                worst_severity = severity

            span_ids = [
                str(span.id) for span in item.evidence_spans if span.id is not None
            ]
            tip_id = f"tip.{item.rule_code.lower()}"
            script = _script_for(item, cause)
            title = f"Improve: {item.title}"
            drill = _drill_for(item.rule_code, template)
            drill_ids.append(drill)

            tips.append(
                {
                    "tip_id": tip_id,
                    "title": title,
                    "script_suggestion": script,
                    "linked_rule_ids": [item.rule_code],
                    "evidence_refs": span_ids,
                }
            )
            call_tips.append(
                {
                    "cause_code": cause,
                    "rule_code": item.rule_code,
                    "title": title,
                    "action_markdown": (
                        f"Review criterion **{item.rule_code}** ({item.title}). "
                        f"Focus: {item.explanation}"
                    ),
                    "template_code": template,
                    "priority": severity,
                    "script_suggestion": script,
                    "evidence_refs": span_ids,
                }
            )

        tips = tips[:8]
        call_tips = call_tips[:8]
        drill_ids = list(dict.fromkeys(drill_ids))[:8]
        priority = _SEVERITY_PRIORITY.get(worst_severity, "medium")
        if not tips:
            priority = "low"

        explanation = (
            f"Generated {len(tips)} coaching tips from scored misses."
            if tips
            else "No coaching tips; call met applicable criteria."
        )
        return {
            "plan_id": None,
            "priority": priority,
            "tips": tips,
            "call_tips": call_tips,
            "drill_ids": drill_ids,
            "explanation": explanation,
        }
