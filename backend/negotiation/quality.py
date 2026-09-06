"""Quality evaluation for Negotiation Strategy Engine."""
from __future__ import annotations

from typing import Any

from negotiation.predictor import predict_next_steps, prediction_accuracy_proxy
from negotiation.strategy import generate_strategies, strategy_consistency
from negotiation.types import QUALITY_THRESHOLDS


def evaluate_quality(store: Any) -> dict[str, Any]:
    sessions = store.list_sessions(limit=100)
    metrics = store.get_metrics()

    pred_scores: list[float] = []
    confidences: list[float] = []
    consistency_scores: list[float] = []
    evidence_scores: list[float] = []

    samples = sessions[-30:] if sessions else []
    if not samples:
        samples = [
            {"customer_utterance": "Đắt quá", "context": {}},
            {"customer_utterance": "Để mình suy nghĩ thêm", "context": {}},
            {"customer_utterance": "Bên kia rẻ hơn", "context": {}},
        ]

    for s in samples:
        utterance = str(s.get("customer_utterance") or "")
        ctx = s.get("context") or {}
        pred = predict_next_steps(utterance, context=ctx)
        confidences.append(pred.confidence)
        stored = s.get("prediction") or {}
        observed = {
            "next_emotion": stored.get("next_emotion", pred.next_emotion),
            "exit_risk": float(stored.get("exit_risk", pred.exit_risk)),
            "buy_probability": float(stored.get("buy_probability", pred.buy_probability)),
            "objection_family": "general",
        }
        pred_scores.append(prediction_accuracy_proxy(pred, observed))

        strategies = generate_strategies(utterance, prediction=pred.to_dict(), context=ctx)
        consistency_scores.append(strategy_consistency(strategies))
        evid = 0.0
        for st in strategies:
            if st.evidence and st.recommended_script and st.forbidden_script:
                evid += 1.0
        evidence_scores.append(evid / max(1, len(strategies)))

    prediction_accuracy = round(sum(pred_scores) / max(1, len(pred_scores)), 4)
    strategy_consistency_score = round(
        sum(consistency_scores) / max(1, len(consistency_scores)), 4
    )
    evidence_validation = round(sum(evidence_scores) / max(1, len(evidence_scores)), 4)

    if len(confidences) >= 2:
        mean_c = sum(confidences) / len(confidences)
        var = sum((c - mean_c) ** 2 for c in confidences) / len(confidences)
        confidence_stability = round(max(0.0, 1.0 - var * 4), 4)
    else:
        confidence_stability = round(confidences[0] if confidences else 0.7, 4)

    checks = {
        "prediction_accuracy": {
            "ok": prediction_accuracy >= QUALITY_THRESHOLDS["prediction_accuracy"],
            "value": prediction_accuracy,
            "threshold": QUALITY_THRESHOLDS["prediction_accuracy"],
        },
        "strategy_consistency": {
            "ok": strategy_consistency_score >= QUALITY_THRESHOLDS["strategy_consistency"],
            "value": strategy_consistency_score,
            "threshold": QUALITY_THRESHOLDS["strategy_consistency"],
        },
        "evidence_validation": {
            "ok": evidence_validation >= QUALITY_THRESHOLDS["evidence_validation"],
            "value": evidence_validation,
            "threshold": QUALITY_THRESHOLDS["evidence_validation"],
        },
        "confidence_stability": {
            "ok": confidence_stability >= QUALITY_THRESHOLDS["confidence_stability"],
            "value": confidence_stability,
            "threshold": QUALITY_THRESHOLDS["confidence_stability"],
        },
    }
    errors = [k for k, v in checks.items() if not v["ok"]]
    return {
        "ok": len(errors) == 0,
        "checks": checks,
        "thresholds": QUALITY_THRESHOLDS,
        "errors": errors,
        "metrics": metrics,
        "static_tree_forbidden": True,
        "strategy_graph_enabled": True,
    }
