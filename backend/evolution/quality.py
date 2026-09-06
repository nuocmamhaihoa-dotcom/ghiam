"""Evolution Quality Gate — block incomplete or unsafe evolution cycles."""

from __future__ import annotations

from typing import Any, Optional

from evolution.store import EvolutionStore


class EvolutionQualityGate:
    """Do not finish an evolution cycle if critical issues remain."""

    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()

    def evaluate(self) -> dict[str, Any]:
        blockers: list[str] = []
        warnings: list[str] = []

        alerts = self.store.list_alerts()
        open_alerts = [a for a in alerts if not a.get("resolved")]
        critical = [
            a
            for a in open_alerts
            if str(a.get("severity", "")).lower() in {"critical", "high"}
            and a.get("kind") in {"observability", "drift", "performance", "pipeline"}
        ]
        if critical:
            blockers.append(f"critical_or_high_alerts:{len(critical)}")

        drift_open = [a for a in open_alerts if a.get("kind") == "drift"]
        if drift_open:
            blockers.append(f"unresolved_drift:{len(drift_open)}")

        failures = self.store.list_failures()
        unreviewed = [f for f in failures if f.get("replayable") and not f.get("qa_reviewed")]
        if len(unreviewed) > 50:
            warnings.append(f"large_failure_backlog:{len(unreviewed)}")

        proposals = self.store.list_proposals()
        auto = [p for p in proposals if (p.get("metadata") or {}).get("auto_merge") is True]
        if auto:
            blockers.append(f"auto_merge_proposals_forbidden:{len(auto)}")

        experiments = self.store.list_experiments()
        auto_promoted = [e for e in experiments if e.get("auto_promoted") is True]
        if auto_promoted:
            blockers.append(f"experiment_auto_promoted:{len(auto_promoted)}")

        scorecard = self.store.get_scorecard()
        if not scorecard:
            warnings.append("missing_model_scorecard")

        obs = self.store.get_observability()
        if float(obs.get("error_rate") or 0) > 0.1:
            blockers.append("broken_pipeline_error_rate")

        ok = not blockers
        return {
            "ok": ok,
            "passed": ok,
            "blockers": blockers,
            "warnings": warnings,
            "open_alert_count": len(open_alerts),
            "critical_high_count": len(critical),
            "can_close_cycle": ok,
        }
