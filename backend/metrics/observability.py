"""Observability Center — error rate, latency, queues, speeds, alerts."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import new_id, now_iso

DEFAULT_THRESHOLDS = {
    "error_rate": 0.05,
    "ai_latency_ms": 3000.0,
    "queue_depth": 200.0,
    "upload_speed_mbps": 1.0,
    "transcript_speed_x": 0.5,
    "dashboard_p95_ms": 2000.0,
}


class ObservabilityCenter:
    def __init__(
        self,
        store: Optional[EvolutionStore] = None,
        thresholds: Optional[Mapping[str, float]] = None,
    ) -> None:
        self.store = store or EvolutionStore()
        self.thresholds = {**DEFAULT_THRESHOLDS, **(dict(thresholds) if thresholds else {})}

    def record(self, metrics: Mapping[str, float]) -> dict[str, Any]:
        current = self.store.get_observability()
        updated = {**current, **{k: float(v) for k, v in metrics.items()}, "updated_at": now_iso()}
        self.store.save_observability(updated)
        alerts = self._evaluate(updated)
        return {"observability": updated, "alerts": alerts}

    def _evaluate(self, metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []

        def fire(metric: str, value: float, thr: float, cmp: str) -> None:
            alert = {
                "alert_id": new_id("alrt"),
                "kind": "observability",
                "metric": metric,
                "value": value,
                "threshold": thr,
                "severity": "high" if metric in {"error_rate", "ai_latency_ms"} else "medium",
                "message": f"{metric}={value} {cmp} threshold {thr}",
                "created_at": now_iso(),
                "resolved": False,
            }
            self.store.append_alert(alert)
            alerts.append(alert)

        er = float(metrics.get("error_rate") or 0)
        if er > self.thresholds["error_rate"]:
            fire("error_rate", er, self.thresholds["error_rate"], ">")

        lat = float(metrics.get("ai_latency_ms") or 0)
        if lat > self.thresholds["ai_latency_ms"]:
            fire("ai_latency_ms", lat, self.thresholds["ai_latency_ms"], ">")

        qd = float(metrics.get("queue_depth") or 0)
        if qd > self.thresholds["queue_depth"]:
            fire("queue_depth", qd, self.thresholds["queue_depth"], ">")

        up = float(metrics.get("upload_speed_mbps") if "upload_speed_mbps" in metrics else 999)
        if "upload_speed_mbps" in metrics and up < self.thresholds["upload_speed_mbps"]:
            fire("upload_speed_mbps", up, self.thresholds["upload_speed_mbps"], "<")

        ts = float(metrics.get("transcript_speed_x") if "transcript_speed_x" in metrics else 999)
        if "transcript_speed_x" in metrics and ts < self.thresholds["transcript_speed_x"]:
            fire("transcript_speed_x", ts, self.thresholds["transcript_speed_x"], "<")

        dash = float(metrics.get("dashboard_p95_ms") or 0)
        if dash > self.thresholds["dashboard_p95_ms"]:
            fire("dashboard_p95_ms", dash, self.thresholds["dashboard_p95_ms"], ">")
        return alerts

    def snapshot(self) -> dict[str, Any]:
        return {
            "observability": self.store.get_observability(),
            "thresholds": self.thresholds,
            "open_alerts": [
                a
                for a in self.store.list_alerts()
                if a.get("kind") == "observability" and not a.get("resolved")
            ][-50:],
        }
