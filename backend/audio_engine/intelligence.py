
"""Call intelligence: stages, emotion, buying signals, objections, interrupts, silence, features."""
from __future__ import annotations

from typing import Any

from audio_engine.types import (
    AGENT_EMOTIONS,
    BUYING_SIGNAL_PATTERNS,
    CALL_STAGES,
    CUSTOMER_EMOTIONS,
    NEXT_STEPS,
    OBJECTION_CONTEXTS,
    EvidenceItem,
    new_id,
)


STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "opening": ("xin chào", "em chào", "alo", "em là"),
    "rapport": ("dạo này", "anh khỏe", "cảm ơn anh", "anh đang"),
    "discovery": ("anh đang dùng", "nhu cầu", "mục đích", "hiện tại"),
    "qualification": ("ngân sách", "quyết định", "khi nào cần", "ai dùng"),
    "presentation": ("sản phẩm", "tính năng", "ưu điểm", "giải pháp"),
    "pricing": ("giá", "báo giá", "chi phí", "ưu đãi"),
    "objection": ("nhưng", "đắt", "để suy nghĩ", "bên kia"),
    "closing": ("chốt", "đặt cọc", "ký", "giao dịch"),
    "follow_up": ("mai em gọi", "gửi thông tin", "theo dõi", "hẹn"),
}


def detect_call_stages(lines: list[dict[str, Any]]) -> dict[str, Any]:
    found: dict[str, dict[str, Any]] = {}
    for line in lines:
        text = str(line.get("normalized_text") or line.get("original_text") or line.get("text") or "").lower()
        for stage, kws in STAGE_KEYWORDS.items():
            if any(k in text for k in kws) and stage not in found:
                found[stage] = {
                    "stage": stage,
                    "timestamp": float(line.get("start_sec") or 0),
                    "speaker": line.get("speaker"),
                    "evidence": text[:120],
                }
    present = [s for s in CALL_STAGES if s in found]
    missing = [s for s in CALL_STAGES if s not in found]
    return {"ok": True, "stages": [found[s] for s in present], "present": present, "missing": missing}


def extract_evidence(lines: list[dict[str, Any]], *, rule_id: str = "AIE-GENERIC") -> list[dict[str, Any]]:
    items: list[EvidenceItem] = []
    for line in lines:
        text = str(line.get("normalized_text") or line.get("original_text") or "").strip()
        if not text:
            continue
        conf = float(line.get("confidence") or 0)
        if conf < 0.5:
            continue
        items.append(
            EvidenceItem(
                claim=f"utterance:{text[:80]}",
                timestamp_start=float(line.get("start_sec") or 0),
                timestamp_end=float(line.get("end_sec") or 0),
                speaker=str(line.get("speaker") or "unknown"),
                transcript=text,
                rule_id=rule_id,
                confidence=conf,
            )
        )
    return [i.to_dict() for i in items]


def detect_buying_signals(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for line in lines:
        text = str(line.get("normalized_text") or line.get("original_text") or "").lower()
        sp = str(line.get("speaker") or "").lower()
        if sp not in {"customer", "client", "c"}:
            continue
        for kind, patterns in BUYING_SIGNAL_PATTERNS:
            if any(p in text for p in patterns):
                hits.append(
                    {
                        "kind": kind,
                        "text": text,
                        "timestamp": float(line.get("start_sec") or 0),
                        "confidence": min(0.99, float(line.get("confidence") or 0.7) + 0.1),
                        "suggested_next_step": NEXT_STEPS.get(kind, "Clarify and advance to close"),
                        "speaker": sp,
                    }
                )
                break
    return hits


def detect_objections(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Context-based objection detection (not raw keyword scoring)."""
    hits: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        text = str(line.get("normalized_text") or line.get("original_text") or "").lower()
        sp = str(line.get("speaker") or "").lower()
        if sp not in {"customer", "client", "c"}:
            continue
        prev = ""
        if i > 0:
            prev = str(lines[i - 1].get("normalized_text") or lines[i - 1].get("original_text") or "").lower()
        context = f"{prev} | {text}"
        for kind, patterns in OBJECTION_CONTEXTS:
            matched = [p for p in patterns if p in text]
            if not matched:
                continue
            # Require conversational context: prior agent pitch/price mention or hesitation phrasing
            contextual = any(x in context for x in ("giá", "sản phẩm", "bên", "quyết", "suy", "nhưng", "tuy"))
            if not contextual and kind not in {"delay", "authority"}:
                continue
            hits.append(
                {
                    "kind": kind,
                    "text": text,
                    "timestamp": float(line.get("start_sec") or 0),
                    "confidence": 0.72 if contextual else 0.6,
                    "context": context[:200],
                    "matched_cues": matched,
                    "speaker": sp,
                }
            )
            break
    return hits


def emotion_timeline(lines: list[dict[str, Any]], duration_sec: float) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for line in lines:
        text = str(line.get("normalized_text") or line.get("original_text") or "").lower()
        sp = str(line.get("speaker") or "").lower()
        t = float(line.get("start_sec") or 0)
        if sp in {"customer", "client", "c"}:
            emotion = "neutral"
            if any(x in text for x in ("hay", "ok", "được", "quan tâm")):
                emotion = "interested"
            if any(x in text for x in ("tin", "uy tín", "ok em")):
                emotion = "trust"
            if any(x in text for x in ("suy nghĩ", "để xem", "chưa chắc")):
                emotion = "hesitation"
            if any(x in text for x in ("phiền", "mệt", "đừng gọi", "thôi")):
                emotion = "frustration"
            if any(x in text for x in ("cúp", "không cần", "thôi anh")):
                emotion = "exit_risk"
            if emotion not in CUSTOMER_EMOTIONS:
                emotion = "neutral"
            points.append({"t_sec": t, "role": "customer", "emotion": emotion, "confidence": 0.7})
        elif sp in {"agent", "a", "sale", "seller"}:
            emotion = "calm"
            if any(x in text for x in ("cam kết", "chắc chắn", "yên tâm")):
                emotion = "confidence"
            if any(x in text for x in ("nhanh", "chỉ còn", "hôm nay")):
                emotion = "rush"
            if any(x in text for x in ("xin lỗi", "à", "ờ")):
                emotion = "stress"
            if emotion not in AGENT_EMOTIONS:
                emotion = "neutral"
            points.append({"t_sec": t, "role": "agent", "emotion": emotion, "confidence": 0.68})
    # fill second-level sparse timeline
    seconds = max(1, int(float(duration_sec or 1)))
    by_sec: dict[int, list[dict[str, Any]]] = {i: [] for i in range(seconds)}
    for p in points:
        idx = min(seconds - 1, max(0, int(p["t_sec"])))
        by_sec[idx].append(p)
    return {"ok": True, "points": points, "by_second": {str(k): v for k, v in by_sec.items() if v}, "duration_sec": seconds}


def classify_interrupts(lines: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for i in range(1, len(lines)):
        prev, cur = lines[i - 1], lines[i]
        if float(cur.get("start_sec") or 0) >= float(prev.get("end_sec") or 0):
            continue
        prev_sp = str(prev.get("speaker") or "").lower()
        cur_sp = str(cur.get("speaker") or "").lower()
        cur_text = str(cur.get("normalized_text") or cur.get("original_text") or "").lower()
        kind = "cooperative_overlap"
        if any(x in cur_text for x in ("đúng", "vâng", "ừ", "ok")):
            kind = "helpful_interruption"
        elif prev_sp != cur_sp and len(cur_text.split()) >= 4:
            kind = "harmful_interruption"
        if float(prev.get("end_sec") or 0) - float(cur.get("start_sec") or 0) > 0.8 and prev_sp != cur_sp:
            kind = "cutting_off"
        events.append(
            {
                "kind": kind,
                "timestamp": float(cur.get("start_sec") or 0),
                "interrupter": cur_sp,
                "interrupted": prev_sp,
                "score_impact": {"helpful_interruption": 1, "cooperative_overlap": 0, "harmful_interruption": -2, "cutting_off": -3}.get(kind, 0),
            }
        )
    return {
        "ok": True,
        "events": events,
        "counts": {
            "helpful": sum(1 for e in events if e["kind"] == "helpful_interruption"),
            "harmful": sum(1 for e in events if e["kind"] == "harmful_interruption"),
            "cooperative": sum(1 for e in events if e["kind"] == "cooperative_overlap"),
            "cutting_off": sum(1 for e in events if e["kind"] == "cutting_off"),
        },
        "interrupt_score": sum(e["score_impact"] for e in events),
    }


def classify_silence(lines: list[dict[str, Any]], duration_sec: float) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    sorted_lines = sorted(lines, key=lambda x: float(x.get("start_sec") or 0))
    for i in range(1, len(sorted_lines)):
        gap = float(sorted_lines[i].get("start_sec") or 0) - float(sorted_lines[i - 1].get("end_sec") or 0)
        if gap < 1.2:
            continue
        prev_text = str(sorted_lines[i - 1].get("normalized_text") or "").lower()
        cur_text = str(sorted_lines[i].get("normalized_text") or "").lower()
        kind = "thinking"
        if any(x in prev_text for x in ("giá", "báo", "tính")):
            kind = "searching"
        if any(x in cur_text for x in ("alo", "nghe", "mất")):
            kind = "connection_issue"
        if any(x in prev_text for x in ("không hiểu", "ý em", "sao")) or any(x in cur_text for x in ("không hiểu", "lại")):
            kind = "confusion"
        gaps.append({"start": float(sorted_lines[i - 1].get("end_sec") or 0), "duration": round(gap, 2), "kind": kind})
    # trailing silence
    if sorted_lines:
        trail = float(duration_sec or 0) - float(sorted_lines[-1].get("end_sec") or 0)
        if trail >= 1.5:
            gaps.append({"start": float(sorted_lines[-1].get("end_sec") or 0), "duration": round(trail, 2), "kind": "thinking"})
    return {"ok": True, "gaps": gaps, "total_silence_sec": round(sum(g["duration"] for g in gaps), 2)}


def audio_features(lines: list[dict[str, Any]], duration_sec: float, separation: dict[str, Any] | None = None) -> dict[str, Any]:
    words = 0
    for line in lines:
        words += len(str(line.get("normalized_text") or line.get("original_text") or "").split())
    minutes = max(0.01, float(duration_sec or 1) / 60.0)
    wpm = words / minutes
    pauses = classify_silence(lines, duration_sec)
    return {
        "ok": True,
        "pitch": {"mean_proxy": 0.5, "variance_proxy": 0.12},
        "volume": {"mean_proxy": 0.62, "low_ratio": 0.1},
        "tempo": {"wpm": round(wpm, 1)},
        "pause": pauses,
        "stress_proxy": min(1.0, pauses["total_silence_sec"] / max(1.0, float(duration_sec or 1)) + (0.2 if wpm > 180 else 0)),
        "confidence_proxy": 0.7,
        "speaking_ratio": (separation or {}).get("speaking_ratio") or {},
        "timeline": [{"t": float(l.get("start_sec") or 0), "speaker": l.get("speaker"), "wpm_local": len(str(l.get("normalized_text") or "").split()) * 60 / max(0.5, float(l.get("end_sec") or 0) - float(l.get("start_sec") or 0))} for l in lines],
    }
