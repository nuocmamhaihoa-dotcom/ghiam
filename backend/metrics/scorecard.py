"""Model Scorecard — accuracy, precision, recall, F1, drift, latency per model."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import ModelMetrics, now_iso

DEFAULT_MODELS = (
    "Evidence AI",
    "Psychology AI",
    "Sales Expert AI",
    "Pragmatics Engine",
)


class ModelScorecard:
    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()
        card = self.store.get_scorecard()
        if not card:
            seed = {
                name: ModelMetrics(
                    model_name=name,
                    accuracy=0.0,
                    precision=0.0,
                    recall=0.0,
                    f1=0.0,
                    drift=0.0,
                    latency_ms=0.0,
                ).to_dict()
                for name in DEFAULT_MODELS
            }
            self.store.save_scorecard(seed)

    def update(
        self,
        model_name: str,
        metrics: Mapping[str, float],
        *,
        sample_count: int = 0,
    ) -> dict[str, Any]:
        card = self.store.get_scorecard()
        prev = dict(card.get(model_name) or {})
        row = ModelMetrics(
            model_name=model_name,
            accuracy=float(metrics.get("accuracy", prev.get("accuracy", 0.0)) or 0.0),
            precision=float(metrics.get("precision", prev.get("precision", 0.0)) or 0.0),
            recall=float(metrics.get("recall", prev.get("recall", 0.0)) or 0.0),
            f1=float(metrics.get("f1", prev.get("f1", 0.0)) or 0.0),
            drift=float(metrics.get("drift", prev.get("drift", 0.0)) or 0.0),
            latency_ms=float(metrics.get("latency_ms", prev.get("latency_ms", 0.0)) or 0.0),
            sample_count=int(sample_count or prev.get("sample_count") or 0),
            updated_at=now_iso(),
        ).to_dict()
        card[model_name] = row
        self.store.save_scorecard(card)
        return row

    def dashboard(self) -> dict[str, Any]:
        card = self.store.get_scorecard()
        models = list(card.values())
        avg_acc = sum(float(m.get("accuracy") or 0) for m in models) / len(models) if models else 0.0
        avg_drift = sum(float(m.get("drift") or 0) for m in models) / len(models) if models else 0.0
        return {
            "models": card,
            "summary": {
                "model_count": len(models),
                "avg_accuracy": round(avg_acc, 4),
                "avg_drift": round(avg_drift, 4),
                "updated_at": now_iso(),
            },
        }
