"""Drift Detection — data, language, industry, customer drift."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import new_id, now_iso

DRIFT_KINDS = ("data", "language", "industry", "customer")


class DriftDetector:
    def __init__(self, store: Optional[EvolutionStore] = None, *, threshold: float = 0.25) -> None:
        self.store = store or EvolutionStore()
        self.threshold = threshold

    def detect(
        self,
        signals: Mapping[str, float],
        *,
        context: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        scores: dict[str, float] = {}
        alerts: list[dict[str, Any]] = []
        for kind in DRIFT_KINDS:
            score = float(signals.get(kind, 0.0) or 0.0)
            scores[kind] = score
            if score >= self.threshold:
                alert = {
                    "alert_id": new_id("alrt"),
                    "kind": "drift",
                    "drift_type": kind,
                    "score": score,
                    "threshold": self.threshold,
                    "severity": "high" if score >= 0.5 else "medium",
                    "message": f"{kind} drift elevated: {score:.3f} >= {self.threshold}",
                    "created_at": now_iso(),
                    "resolved": False,
                    "context": dict(context or {}),
                }
                self.store.append_alert(alert)
                alerts.append(alert)
        return {
            "scores": scores,
            "threshold": self.threshold,
            "alert_count": len(alerts),
            "alerts": alerts,
            "elevated": bool(alerts),
            "created_at": now_iso(),
        }

    def open_alerts(self) -> list[dict[str, Any]]:
        return [a for a in self.store.list_alerts() if a.get("kind") == "drift" and not a.get("resolved")]

    def report(self) -> dict[str, Any]:
        alerts = [a for a in self.store.list_alerts() if a.get("kind") == "drift"]
        open_alerts = [a for a in alerts if not a.get("resolved")]
        by_type: dict[str, int] = {}
        for a in open_alerts:
            t = str(a.get("drift_type") or "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total_drift_alerts": len(alerts),
            "open_drift_alerts": len(open_alerts),
            "by_type": by_type,
            "threshold": self.threshold,
            "alerts": open_alerts[-50:],
        }
