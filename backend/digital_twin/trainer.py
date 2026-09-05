"""Train Digital Twins from golden / QA-approved / high-conversion calls only."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from digital_twin.types import (
    ALLOWED_TRAINING_LABELS,
    CallEligibility,
    ConversationDNA,
    SkillProfile,
    VoicePattern,
)

RE_QUESTION = re.compile(r"[?？]|không ạ|được không|thế nào|bao nhiêu|khi nào|vì sao|tại sao", re.I)
RE_EMPATHY = re.compile(r"em hiểu|cảm thông|đúng rồi|em nắm|lo lắng|yên tâm|cân nhắc", re.I)
RE_VALUE = re.compile(r"lợi ích|tiết kiệm|bảo hành|giá trị|ưu đãi|quyền lợi|giải pháp|phù hợp", re.I)
RE_CLOSE = re.compile(r"chốt|xác nhận|chuyển khoản|đặt cọc|gửi hợp đồng|đồng ý|làm luôn", re.I)
RE_RAPPORT = re.compile(r"dạ|vâng|cảm ơn|chúc|hôm nay|tiện không|xin phép", re.I)
RE_DISCOVERY = re.compile(r"nhu cầu|quan tâm|đang dùng|ngân sách|mục tiêu|khó khăn|ưu tiên", re.I)


def classify_call(call: dict[str, Any]) -> str:
    label = str(call.get("label") or call.get("eligibility") or call.get("tier") or "").lower().strip()
    aliases = {
        "golden": CallEligibility.GOLDEN.value,
        "qa_approved": CallEligibility.QA_APPROVED.value,
        "qa-approved": CallEligibility.QA_APPROVED.value,
        "high_conversion": CallEligibility.HIGH_CONVERSION.value,
        "high-conversion": CallEligibility.HIGH_CONVERSION.value,
    }
    if label in aliases:
        return aliases[label]
    if label in ALLOWED_TRAINING_LABELS:
        return label
    if call.get("is_golden") or call.get("golden"):
        return CallEligibility.GOLDEN.value
    if call.get("qa_approved") or call.get("approved_by_qa"):
        return CallEligibility.QA_APPROVED.value

    outcome = str(call.get("outcome") or call.get("result") or "").lower()
    conversion = float(call.get("conversion_rate") or call.get("close_rate") or 0)
    score = float(call.get("qa_score") or call.get("score") or 0)
    failed = bool(
        call.get("failed")
        or call.get("is_failure")
        or outcome in {"lost", "fail", "failed", "no_sale", "rejected"}
        or label in {"rejected", "failed", "bad", "error"}
    )
    if failed or score < 60:
        return CallEligibility.REJECTED.value
    if outcome in {"won", "closed", "converted"} and (conversion >= 0.55 or score >= 80):
        return CallEligibility.HIGH_CONVERSION.value
    if conversion >= 0.7 or score >= 85:
        return CallEligibility.HIGH_CONVERSION.value
    return CallEligibility.REJECTED.value


def filter_training_calls(calls: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for call in calls:
        label = classify_call(call)
        enriched = dict(call)
        enriched["_eligibility"] = label
        if label in ALLOWED_TRAINING_LABELS:
            accepted.append(enriched)
        else:
            rejected.append(enriched)
    return accepted, rejected


def _agent_turns(call: dict[str, Any]) -> list[str]:
    turns = call.get("turns") or call.get("transcript") or []
    texts: list[str] = []
    if isinstance(turns, str):
        return [turns]
    for t in turns:
        if isinstance(t, str):
            texts.append(t)
            continue
        speaker = str(t.get("speaker") or t.get("role") or "").lower()
        text = str(t.get("text") or t.get("content") or "").strip()
        if not text:
            continue
        if speaker in {"agent", "seller", "sales", "nhân viên", "tvv", "advisor"}:
            texts.append(text)
        elif not speaker and t.get("is_agent"):
            texts.append(text)
    if not texts:
        for t in turns:
            if isinstance(t, dict):
                text = str(t.get("text") or "").strip()
                if text:
                    texts.append(text)
    return texts


def _abstract_template(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\b0\d{8,11}\b", "[SỐ]", t)
    t = re.sub(r"\b[\w.+-]+@[\w.-]+\.\w+\b", "[EMAIL]", t)
    t = re.sub(r"\b\d{1,3}(?:[.,]\d{3})+(?:\s*đ|k|tr)?\b", "[SỐ_TIỀN]", t, flags=re.I)
    t = re.sub(r"\b(anh|chị|em)\s+[A-ZÀ-Ỵ][\wÀ-ỹ]*", r"\1 [TÊN]", t)
    if len(t) > 140:
        t = t[:137] + "..."
    return t


def extract_style_signals(call: dict[str, Any]) -> dict[str, Any]:
    turns = _agent_turns(call)
    blob = " ".join(turns)
    n = max(1, len(turns))
    words = max(1, len(blob.split()))
    questions = [t for t in turns if RE_QUESTION.search(t)]
    empathy = [t for t in turns if RE_EMPATHY.search(t)]
    value = [t for t in turns if RE_VALUE.search(t)]
    closes = [t for t in turns if RE_CLOSE.search(t)]
    discovery = [t for t in turns if RE_DISCOVERY.search(t)]
    rapport = sum(1 for t in turns if RE_RAPPORT.search(t))
    return {
        "question_templates": [_abstract_template(t) for t in questions[:6]],
        "empathy_templates": [_abstract_template(t) for t in empathy[:6]],
        "value_templates": [_abstract_template(t) for t in value[:6]],
        "closing_templates": [_abstract_template(t) for t in closes[:6]],
        "discovery_templates": [_abstract_template(t) for t in discovery[:6]],
        "question_rate": len(questions) / n,
        "empathy_rate": len(empathy) / n,
        "value_rate": len(value) / n,
        "close_rate": len(closes) / n,
        "discovery_rate": len(discovery) / n,
        "rapport_rate": rapport / n,
        "avg_turn_length": words / n,
        "pause_ratio": min(0.4, max(0.05, 1.0 - min(1.0, words / (n * 18)))),
        "conversation_style": "warm_professional" if rapport / n >= 0.3 else "direct_professional",
        "discovery_style": "deep_discovery" if len(discovery) / n >= 0.25 else "consultative",
        "closing_style": "soft_ask" if closes else "trial_close",
        "objection_strategy": "acknowledge_reframe_offer",
    }


def _clamp(x: float) -> float:
    return round(max(0.05, min(1.0, x)), 4)


def build_skill_profile(signals: list[dict[str, Any]]) -> SkillProfile:
    n = max(1, len(signals))
    return SkillProfile(
        discovery=_clamp(sum(s["discovery_rate"] for s in signals) / n * 2.2),
        rapport=_clamp(sum(s["rapport_rate"] for s in signals) / n * 1.8),
        objection_handling=_clamp(
            (sum(s["empathy_rate"] for s in signals) / n + sum(s["value_rate"] for s in signals) / n) / 1.2
        ),
        closing=_clamp(sum(s["close_rate"] for s in signals) / n * 2.5 + 0.35),
        value_building=_clamp(sum(s["value_rate"] for s in signals) / n * 2.2 + 0.25),
        empathy=_clamp(sum(s["empathy_rate"] for s in signals) / n * 2.4 + 0.2),
        voice_control=_clamp(0.55 + (0.3 - abs(0.2 - sum(s["pause_ratio"] for s in signals) / n))),
        question_quality=_clamp(sum(s["question_rate"] for s in signals) / n * 2.0 + 0.25),
    )


def build_voice_pattern(signals: list[dict[str, Any]]) -> VoicePattern:
    n = max(1, len(signals))
    avg_len = sum(s["avg_turn_length"] for s in signals) / n
    pause = sum(s["pause_ratio"] for s in signals) / n
    qrate = sum(s["question_rate"] for s in signals) / n
    emp = sum(s["empathy_rate"] for s in signals) / n * 4
    tempo = "measured" if pause >= 0.22 else ("brisk" if avg_len < 10 else "balanced")
    return VoicePattern(
        avg_turn_length=round(avg_len, 2),
        pause_ratio=round(pause, 3),
        question_rate=round(qrate, 3),
        empathy_markers_per_call=round(emp, 2),
        tempo=tempo,
        rhythm_notes=f"tempo={tempo}; pause={pause:.2f}; Q-rate={qrate:.2f}",
    )


def _pick(counter: Counter[str], default: str) -> str:
    return counter.most_common(1)[0][0] if counter else default


def _uniq(items: list[str], limit: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def build_conversation_dna(signals: list[dict[str, Any]]) -> ConversationDNA:
    questions: list[str] = []
    closings: list[str] = []
    empathy: list[str] = []
    value: list[str] = []
    styles: Counter[str] = Counter()
    discovery: Counter[str] = Counter()
    closing: Counter[str] = Counter()
    objection: Counter[str] = Counter()
    for s in signals:
        questions.extend(s.get("question_templates") or [])
        closings.extend(s.get("closing_templates") or [])
        empathy.extend(s.get("empathy_templates") or [])
        value.extend(s.get("value_templates") or [])
        styles[s.get("conversation_style") or "warm_professional"] += 1
        discovery[s.get("discovery_style") or "consultative"] += 1
        closing[s.get("closing_style") or "soft_ask"] += 1
        objection[s.get("objection_strategy") or "acknowledge_reframe_offer"] += 1
    qseq = _uniq(questions) or ["Anh/chị đang quan tâm điều gì nhất hiện tại ạ?"]
    cseq = _uniq(closings) or ["Nếu ổn, mình xác nhận bước tiếp theo luôn nhé ạ."]
    eseq = _uniq(empathy) or ["Em hiểu anh/chị đang cân nhắc."]
    vseq = _uniq(value) or ["Lợi ích chính là giải pháp phù hợp và tiết kiệm dài hạn."]
    return ConversationDNA(
        question_sequence=qseq,
        closing_sequence=cseq,
        empathy_pattern=eseq,
        value_building_pattern=vseq,
        discovery_style=_pick(discovery, "consultative"),
        closing_style=_pick(closing, "soft_ask"),
        objection_strategy=_pick(objection, "acknowledge_reframe_offer"),
        conversation_style=_pick(styles, "warm_professional"),
        style_templates={"questions": qseq, "closings": cseq, "empathy": eseq, "value": vseq},
    )
