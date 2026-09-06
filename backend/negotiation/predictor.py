"""Next-step prediction (3–5 moves ahead)."""
from __future__ import annotations

from typing import Any

from negotiation.types import EMOTION_CUES, OBJECTION_CUES, NextStepPrediction


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def detect_objection_family(text: str) -> str:
    t = _norm(text)
    scores = {k: sum(1 for c in cues if c in t) for k, cues in OBJECTION_CUES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def detect_emotion(text: str) -> str:
    t = _norm(text)
    scores = {k: sum(1 for c in cues if c in t) for k, cues in EMOTION_CUES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"


def predict_next_steps(
    customer_utterance: str,
    *,
    history: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> NextStepPrediction:
    history = history or []
    context = context or {}
    family = detect_objection_family(customer_utterance)
    emotion = detect_emotion(customer_utterance)
    turns = len(history)

    buy = float(context.get("buy_signal", 0.35))
    if emotion == "positive":
        buy += 0.18
    if emotion in {"frustrated", "resistant"}:
        buy -= 0.15
    if family == "price":
        buy -= 0.05
    if family == "need":
        buy -= 0.12
    if turns >= 4:
        buy += 0.08
    buy_probability = round(_clamp(buy), 4)

    exit_r = 0.25
    if emotion in {"frustrated", "resistant"}:
        exit_r += 0.28
    if family in {"timing", "need"}:
        exit_r += 0.12
    if "thôi" in _norm(customer_utterance) or "cúp" in _norm(customer_utterance):
        exit_r += 0.2
    if buy_probability > 0.6:
        exit_r -= 0.15
    exit_risk = round(_clamp(exit_r), 4)

    questions = {
        "price": "Có gói nào rẻ hơn / có thể giảm được không?",
        "timing": "Có thể để mình suy nghĩ thêm rồi gọi lại không?",
        "trust": "Bảo hành / hoàn tiền thế nào, có giấy tờ không?",
        "competitor": "Bên kia đang offer gì khác biệt?",
        "need": "Tại sao mình phải dùng ngay bây giờ?",
        "general": "Em giải thích rõ hơn lợi ích chính được không?",
    }
    objections = {
        "price": "Vẫn thấy đắt so với ngân sách hiện tại.",
        "timing": "Tháng này chưa tiện, để tháng sau.",
        "trust": "Mình chưa chắc uy tín bên em.",
        "competitor": "Bên kia đang có ưu đãi tốt hơn.",
        "need": "Mình chưa thấy cần thiết.",
        "general": "Mình cần thêm thông tin trước khi quyết định.",
    }
    paths = {
        "anxious": ["anxious", "curious", "cautious", "positive", "committed"],
        "frustrated": ["frustrated", "resistant", "neutral", "curious", "positive"],
        "curious": ["curious", "curious", "positive", "positive", "committed"],
        "positive": ["positive", "positive", "committed", "committed", "closed"],
        "resistant": ["resistant", "frustrated", "neutral", "curious", "exit"],
        "neutral": ["neutral", "curious", "cautious", "positive", "committed"],
    }
    path = paths.get(emotion, paths["neutral"])

    horizon: list[dict[str, Any]] = []
    for i in range(3, 6):
        step_emotion = path[min(i - 1, len(path) - 1)]
        decay = 0.04 * (i - 3)
        horizon.append(
            {
                "step": i - 2,
                "predicted_question": questions[family] if i == 3 else "Khi nào mình bắt đầu được?",
                "predicted_objection": objections[family] if i <= 4 else "Còn phí ẩn nào không?",
                "predicted_emotion": step_emotion,
                "exit_risk": round(
                    _clamp(exit_risk + decay if step_emotion in {"resistant", "exit"} else exit_risk - decay * 0.5),
                    4,
                ),
                "buy_probability": round(
                    _clamp(
                        buy_probability - decay
                        if step_emotion in {"resistant", "exit"}
                        else buy_probability + decay * 0.6
                    ),
                    4,
                ),
            }
        )

    evidence = [
        f"objection_family={family}",
        f"emotion={emotion}",
        f"history_turns={turns}",
        f"utterance_len={len(customer_utterance or '')}",
    ]
    if context.get("product"):
        evidence.append(f"product={context.get('product')}")

    confidence = round(
        _clamp(
            0.55
            + (0.08 if family != "general" else 0.0)
            + (0.06 if emotion != "neutral" else 0.0)
            + min(0.12, turns * 0.03)
        ),
        4,
    )

    return NextStepPrediction(
        next_question=questions[family],
        next_objection=objections[family],
        next_emotion=path[min(1, len(path) - 1)],
        exit_risk=exit_risk,
        buy_probability=buy_probability,
        horizon=horizon,
        confidence=confidence,
        evidence=evidence,
    )


def prediction_accuracy_proxy(pred: NextStepPrediction, observed: dict[str, Any]) -> float:
    score = 0.0
    total = 0.0
    if "next_emotion" in observed:
        total += 1
        score += 1.0 if observed["next_emotion"] == pred.next_emotion else 0.35
    if "exit_risk" in observed:
        total += 1
        score += 1.0 - min(1.0, abs(float(observed["exit_risk"]) - pred.exit_risk))
    if "buy_probability" in observed:
        total += 1
        score += 1.0 - min(1.0, abs(float(observed["buy_probability"]) - pred.buy_probability))
    if "objection_family" in observed:
        total += 1
        fam = detect_objection_family(pred.next_objection)
        score += 1.0 if fam == observed["objection_family"] or observed["objection_family"] == "general" else 0.4
    if total <= 0:
        return pred.confidence
    return round(score / total, 4)
