"""Roleplay — twin replies, similarity, coaching (no verbatim cloning)."""
from __future__ import annotations

import re
from typing import Any

from digital_twin.types import RoleplaySession, RoleplayTurn, new_id, now_iso

TOKEN_RE = re.compile(r"[a-zA-ZÀ-ỹ0-9]+", re.I)
STYLE_CUES = {
    "discovery": re.compile(r"nhu cầu|đang dùng|ngân sách|mục tiêu|ưu tiên|khó khăn|quan tâm", re.I),
    "rapport": re.compile(r"dạ|vâng|cảm ơn|xin phép|tiện không", re.I),
    "empathy": re.compile(r"em hiểu|cảm thông|yên tâm|lo lắng|cân nhắc", re.I),
    "value": re.compile(r"lợi ích|tiết kiệm|bảo hành|giá trị|ưu đãi|phù hợp", re.I),
    "closing": re.compile(r"chốt|xác nhận|chuyển khoản|đặt cọc|đồng ý|làm luôn", re.I),
    "question": re.compile(r"[?？]|không ạ|được không|thế nào", re.I),
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(text or "") if len(t) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def style_vector(text: str) -> dict[str, float]:
    return {k: (1.0 if rx.search(text or "") else 0.0) for k, rx in STYLE_CUES.items()}


def twin_expected_reply(profile: dict[str, Any], customer_text: str, step: int = 0) -> str:
    dna = profile.get("conversation_dna") or {}
    style = profile.get("conversation_style") or dna.get("conversation_style") or "warm_professional"
    discovery = profile.get("discovery_style") or dna.get("discovery_style") or "consultative"
    closing = profile.get("closing_style") or dna.get("closing_style") or "soft_ask"
    objection = profile.get("objection_strategy") or dna.get("objection_strategy") or "acknowledge_reframe_offer"
    qseq = dna.get("question_sequence") or []
    cseq = dna.get("closing_sequence") or []
    eseq = dna.get("empathy_pattern") or []
    vseq = dna.get("value_building_pattern") or []
    low = (customer_text or "").lower()
    if any(x in low for x in ("đắt", "giá cao", "cân nhắc", "để em coi", "suy nghĩ", "không cần")):
        emp = eseq[step % len(eseq)] if eseq else "Đồng cảm ngắn"
        val = vseq[step % len(vseq)] if vseq else "Nêu lợi ích chính"
        return (
            f"[{style}] {emp}. Em hiểu anh/chị muốn chắc chắn. "
            f"{val}. Theo hướng {objection}, mình khoanh 1 tiêu chí quan trọng nhất rồi quyết định nhẹ nhàng nhé?"
        )
    if any(x in low for x in ("chuyển khoản", "lấy", "chốt", "đồng ý", "gửi stk", "ok")):
        close = cseq[step % len(cseq)] if cseq else "Xác nhận bước tiếp theo"
        return f"[{style}/{closing}] Tuyệt vời ạ. {close}. Em gửi thông tin thanh toán và xác nhận đơn ngay."
    q = qseq[step % len(qseq)] if qseq else "Hỏi nhu cầu hiện tại"
    return (
        f"[{style}/{discovery}] Cảm ơn anh/chị đã chia sẻ. {q}. "
        f"Em hỏi ngắn để chọn gói phù hợp nhất, không làm mất thời gian ạ."
    )


def similarity_score(trainee_text: str, twin_text: str, profile: dict[str, Any] | None = None) -> float:
    lex = jaccard(trainee_text, twin_text)
    if lex >= 0.92 and len(_tokens(trainee_text)) > 6:
        lex = 0.55
    tv, tw = style_vector(trainee_text), style_vector(twin_text)
    keys = list(STYLE_CUES)
    style_sim = sum(1 for k in keys if tv[k] == tw[k]) / len(keys)
    cue_bonus = 0.0
    if profile:
        dna = profile.get("conversation_dna") or {}
        if (dna.get("discovery_style") or profile.get("discovery_style")) and tv["discovery"]:
            cue_bonus += 0.05
        if (profile.get("closing_style") or dna.get("closing_style")) and tv["closing"]:
            cue_bonus += 0.05
        if tv["empathy"]:
            cue_bonus += 0.05
    return round(max(0.0, min(1.0, 0.35 * min(lex, 0.75) + 0.55 * style_sim + cue_bonus)), 4)


def skill_gap(trainee_texts: list[str], profile: dict[str, Any]) -> dict[str, float]:
    skills = profile.get("skill_profile") or {}
    vec = style_vector(" ".join(trainee_texts))
    mapping = {
        "discovery": "discovery",
        "rapport": "rapport",
        "empathy": "empathy",
        "value_building": "value",
        "closing": "closing",
        "question_quality": "question",
    }
    return {
        skill: round(max(0.0, float(skills.get(skill) or 0.5) - float(vec.get(cue) or 0.0)), 4)
        for skill, cue in mapping.items()
    }


def coaching_notes(gaps: dict[str, float], similarity: float, profile: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if similarity < 0.55:
        style = profile.get("conversation_style") or "warm_professional"
        notes.append(
            f"Similarity thấp — bám nhịp Twin ({style}): đồng cảm ngắn → hỏi nhu cầu → giá trị → mời bước tiếp."
        )
    tips = {
        "discovery": "Thêm 1 câu hỏi mở về nhu cầu/điều kiện trước khi pitch.",
        "rapport": "Mở đầu ấm hơn: chào + xin phép thời gian + gọi tên nhẹ.",
        "empathy": "Phản ánh cảm xúc khách trước khi phản biện giá/điều kiện.",
        "value_building": "Nối lợi ích với tiêu chí khách vừa nêu, tránh liệt kê tính năng.",
        "closing": "Kết bằng lời mời bước nhỏ (xác nhận / gửi thông tin), không ép cứng.",
        "question_quality": "Ưu tiên câu hỏi một tiêu chí/một lần, dễ trả lời.",
    }
    for skill, gap in sorted(gaps.items(), key=lambda x: x[1], reverse=True):
        if gap >= 0.25 and skill in tips:
            notes.append(tips[skill])
        if len(notes) >= 4:
            break
    return notes or ["Đang bám sát Twin — giữ nhịp hỏi–đồng cảm–giá trị–chốt mềm."]


def improvement_score(similarity: float, gaps: dict[str, float], prev_similarity: float | None = None) -> float:
    gap_penalty = sum(gaps.values()) / max(1, len(gaps))
    base = max(0.0, similarity - 0.35 * gap_penalty)
    if prev_similarity is not None:
        base = 0.7 * base + 0.3 * max(0.0, similarity - prev_similarity + 0.5)
    return round(max(0.0, min(1.0, base)), 4)


def top_differences(trainee_texts: list[str], twin_replies: list[str], gaps: dict[str, float]) -> list[str]:
    diffs = [
        f"{skill}: gap={gap:.2f}"
        for skill, gap in sorted(gaps.items(), key=lambda x: x[1], reverse=True)[:3]
        if gap >= 0.2
    ]
    if trainee_texts and twin_replies and jaccard(trainee_texts[-1], twin_replies[-1]) < 0.25:
        diffs.append("Lời thoại lệch phong cách Twin ở lượt gần nhất")
    return diffs or ["Khác biệt nhỏ — duy trì consistency"]


def run_roleplay(
    profile: dict[str, Any],
    *,
    trainee_id: str,
    scenario: str,
    trainee_turns: list[str],
    customer_turns: list[str] | None = None,
    prev_similarity: float | None = None,
) -> RoleplaySession:
    customers = customer_turns or ["Em muốn tìm hiểu thêm.", "Giá hơi cao.", "Để em suy nghĩ đã."]
    twin_replies: list[str] = []
    turns: list[RoleplayTurn] = []
    for i, trainee in enumerate(trainee_turns):
        customer = customers[i % len(customers)]
        twin_reply = twin_expected_reply(profile, customer, step=i)
        twin_replies.append(twin_reply)
        turns.append(RoleplayTurn(speaker="customer", text=customer))
        turns.append(RoleplayTurn(speaker="trainee", text=trainee))
        turns.append(RoleplayTurn(speaker="twin", text=twin_reply))
    sims = [similarity_score(trainee_turns[i], twin_replies[i], profile) for i in range(len(trainee_turns))]
    sim = round(sum(sims) / max(1, len(sims)), 4)
    gaps = skill_gap(trainee_turns, profile)
    return RoleplaySession(
        session_id=new_id("rp"),
        twin_id=str(profile.get("twin_id") or ""),
        trainee_id=trainee_id,
        scenario=scenario,
        turns=turns,
        twin_replies=twin_replies,
        similarity_score=sim,
        improvement_score=improvement_score(sim, gaps, prev_similarity),
        coaching=coaching_notes(gaps, sim, profile),
        top_differences=top_differences(trainee_turns, twin_replies, gaps),
        skill_gap=gaps,
        created_at=now_iso(),
        metadata={"verbatim_cloning": False, "scenario": scenario},
    )
