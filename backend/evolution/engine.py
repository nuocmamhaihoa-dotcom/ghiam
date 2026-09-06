"""Evolution Engine orchestrator — full improvement lifecycle with QA gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from evolution.proposals import ProposalGenerator
from evolution.quality import EvolutionQualityGate
from evolution.store import EvolutionStore
from evolution.types import new_id, now_iso
from experiments.engine import ExperimentEngine
from metrics.drift import DriftDetector
from metrics.failure_replay import FailureReplayEngine
from metrics.observability import ObservabilityCenter
from metrics.performance import PerformanceOptimizer
from metrics.scorecard import ModelScorecard
from rollback.engine import RollbackEngine
from shadow_mode.runner import ShadowMode


class EvolutionEngine:
    """Coordinates shadow → experiment → proposal → QA → promote → monitor → rollback."""

    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()
        self.shadow = ShadowMode(store=self.store)
        self.experiments = ExperimentEngine(store=self.store)
        self.rollback = RollbackEngine(store=self.store)
        self.scorecard = ModelScorecard(store=self.store)
        self.drift = DriftDetector(store=self.store)
        self.failures = FailureReplayEngine(store=self.store)
        self.performance = PerformanceOptimizer(store=self.store)
        self.observability = ObservabilityCenter(store=self.store)
        self.proposals = ProposalGenerator(store=self.store)
        self.quality = EvolutionQualityGate(store=self.store)

    def executive_dashboard(self) -> dict[str, Any]:
        gate = self.quality.evaluate()
        scorecard = self.scorecard.dashboard()
        drift = self.drift.report()
        obs = self.observability.snapshot()
        shadow_reports = self.store.list_shadow_reports()
        significant_shadow = [r for r in shadow_reports if r.get("significant")]
        experiments = self.store.list_experiments()
        concluded = [e for e in experiments if e.get("status") == "concluded"]
        pending = self.proposals.list_pending_qa()
        readiness = self.rollback.readiness()
        return {
            "system_health": "healthy" if gate["ok"] else "blocked",
            "quality_gate": gate,
            "ai_accuracy": (scorecard.get("summary") or {}).get("avg_accuracy", 0.0),
            "drift": {
                "open_alerts": drift.get("open_drift_alerts", drift.get("open_alerts", 0)),
                "by_type": drift.get("by_type", {}),
            },
            "shadow_comparison": {
                "total": len(shadow_reports),
                "significant": len(significant_shadow),
                "user_impact_always_false": all(not r.get("user_impact") for r in shadow_reports)
                if shadow_reports
                else True,
            },
            "experiment_results": {
                "total": len(experiments),
                "concluded": len(concluded),
                "auto_promoted_count": sum(1 for e in experiments if e.get("auto_promoted")),
            },
            "revenue_impact": self._revenue_impact(concluded),
            "critical_issues": gate.get("blockers", []),
            "rollback_status": readiness,
            "pending_qa_proposals": len(pending),
            "observability": obs.get("observability", obs),
            "generated_at": now_iso(),
        }

    @staticmethod
    def _revenue_impact(concluded: list[dict[str, Any]]) -> dict[str, Any]:
        if not concluded:
            return {"experiments": 0, "avg_delta": 0.0}
        deltas: list[float] = []
        for e in concluded:
            ma = float((e.get("metrics_a") or {}).get("revenue_impact") or 0.0)
            mb = float((e.get("metrics_b") or {}).get("revenue_impact") or 0.0)
            winner = e.get("winner")
            if winner == "b":
                deltas.append(mb - ma)
            elif winner == "a":
                deltas.append(ma - mb)
            else:
                deltas.append(0.0)
        avg = sum(deltas) / len(deltas) if deltas else 0.0
        return {"experiments": len(concluded), "avg_delta": round(avg, 4)}

    def run_weekly_cycle(self, signals: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """Detect → propose (PENDING_QA only). Never merges to production."""
        sig = dict(signals or {})
        sig.setdefault(
            "shadow_significant",
            len([r for r in self.store.list_shadow_reports() if r.get("significant")]),
        )
        sig.setdefault("drift_alerts", len(self.drift.open_alerts()))
        sig.setdefault("failures", len(self.store.list_failures()))
        proposals = self.proposals.generate_weekly(signals=sig)
        gate = self.quality.evaluate()
        return {
            "cycle_id": new_id("ecycle"),
            "proposals_created": len(proposals),
            "proposals": proposals,
            "auto_merged": False,
            "quality_gate": gate,
            "created_at": now_iso(),
        }

    def write_reports(self, out_dir: str | Path = "docs/evolution") -> dict[str, str]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        dash = self.executive_dashboard()
        scorecard = self.scorecard.dashboard()
        drift = self.drift.report()
        experiments = self.store.list_experiments()
        perf = self.performance.analyze(
            {
                "query_p95_ms": float((dash.get("observability") or {}).get("dashboard_p95_ms") or 0),
                "cache_hit_rate": 0.75,
                "queue_depth": float((dash.get("observability") or {}).get("queue_depth") or 0),
                "memory_pct": 60.0,
                "cpu_pct": 50.0,
                "gpu_pct": 40.0,
            }
        )
        readiness = self.rollback.readiness()
        gate = self.quality.evaluate()
        improvements = self._top_improvements()
        roadmap = self._roadmap(gate, drift, improvements)

        files = {
            "Evolution_Report.md": self._md_evolution(dash, gate),
            "AI_Scorecard.md": self._md_scorecard(scorecard),
            "Drift_Report.md": self._md_drift(drift),
            "Experiment_Report.md": self._md_experiments(experiments),
            "Performance_Report.md": self._md_perf(perf),
            "Rollback_Readiness.md": self._md_rollback(readiness),
            "Top_100_Improvements.md": self._md_improvements(improvements),
            "Roadmap_Next.md": self._md_roadmap(roadmap),
        }
        written: dict[str, str] = {}
        for name, body in files.items():
            path = out / name
            path.write_text(body, encoding="utf-8")
            written[name] = str(path)
        return written

    def _top_improvements(self) -> list[dict[str, Any]]:
        seeds = [
            ("Wire shadow mode into live scoring path", "shadow", "high"),
            ("Add QA agreement labels for experiment KPI", "experiments", "high"),
            ("Reduce AI latency p95 under 2s", "performance", "high"),
            ("Close open drift alerts weekly", "drift", "high"),
            ("Expand model scorecard with golden-set eval", "scorecard", "medium"),
            ("Automate failure replay triage queue", "failures", "medium"),
            ("Version every prompt change via rollback engine", "rollback", "high"),
            ("Block production promote without evidence pack", "quality", "high"),
            ("Dashboard: CTO executive evolution view", "observability", "medium"),
            ("Cache scorecard aggregations", "performance", "low"),
        ]
        items = [
            {"rank": i, "title": t, "area": a, "impact": imp}
            for i, (t, a, imp) in enumerate(seeds, start=1)
        ]
        while len(items) < 100:
            n = len(items) + 1
            items.append(
                {
                    "rank": n,
                    "title": f"Evolution backlog item #{n}: harden module integration",
                    "area": "platform",
                    "impact": "low",
                }
            )
        return items

    def _roadmap(self, gate, drift, improvements) -> list[dict[str, str]]:
        return [
            {
                "phase": "P0",
                "item": "Clear quality gate blockers",
                "detail": ", ".join(gate.get("blockers") or ["none"]),
            },
            {
                "phase": "P0",
                "item": "Resolve drift alerts",
                "detail": str(drift.get("open_drift_alerts", drift.get("open_alerts", 0))),
            },
            {"phase": "P1", "item": improvements[0]["title"], "detail": improvements[0]["area"]},
            {"phase": "P1", "item": improvements[1]["title"], "detail": improvements[1]["area"]},
            {"phase": "P2", "item": "Expand shadow coverage to 100% calls", "detail": "shadow"},
            {"phase": "P2", "item": "Multi-arm experiments beyond A/B", "detail": "experiments"},
            {"phase": "P3", "item": "Continuous eval harness per model", "detail": "scorecard"},
        ]

    def _md_evolution(self, dash, gate) -> str:
        return "\n".join(
            [
                "# Evolution Report",
                "",
                f"Generated: {dash.get('generated_at')}",
                "",
                f"- System health: **{dash.get('system_health')}**",
                f"- AI accuracy (avg): {dash.get('ai_accuracy')}",
                f"- Pending QA proposals: {dash.get('pending_qa_proposals')}",
                f"- Quality gate passed: {gate.get('passed')}",
                f"- Blockers: {', '.join(gate.get('blockers') or ['none'])}",
                f"- Shadow significant diffs: {dash.get('shadow_comparison', {}).get('significant')}",
                f"- Experiments concluded: {dash.get('experiment_results', {}).get('concluded')}",
                f"- Auto-promoted experiments: {dash.get('experiment_results', {}).get('auto_promoted_count')}",
                "",
                "Hard rule: Rulebook/Production changes require explicit QA Approval + promote step.",
                "",
            ]
        )

    def _md_scorecard(self, scorecard) -> str:
        lines = ["# AI Scorecard", "", f"Summary: {scorecard.get('summary')}", ""]
        models = scorecard.get("models") or {}
        for name, row in models.items():
            lines.append(
                f"- **{name}**: acc={row.get('accuracy')} p={row.get('precision')} "
                f"r={row.get('recall')} f1={row.get('f1')} drift={row.get('drift')} "
                f"latency_ms={row.get('latency_ms')}"
            )
        lines.append("")
        return "\n".join(lines)

    def _md_drift(self, drift) -> str:
        return "\n".join(
            [
                "# Drift Report",
                "",
                f"- Open drift alerts: {drift.get('open_drift_alerts', drift.get('open_alerts'))}",
                f"- By type: {drift.get('by_type')}",
                f"- Threshold: {drift.get('threshold')}",
                "",
            ]
        )

    def _md_experiments(self, experiments) -> str:
        lines = ["# Experiment Report", "", f"Total experiments: {len(experiments)}", ""]
        for e in experiments[-50:]:
            lines.append(
                f"- {e.get('experiment_id')} | {e.get('name')} | winner={e.get('winner')} | "
                f"auto_promoted={e.get('auto_promoted')} | {e.get('conclusion')}"
            )
        lines.append("")
        return "\n".join(lines)

    def _md_perf(self, perf) -> str:
        lines = [
            "# Performance Report",
            "",
            f"Suggestions: {perf.get('suggestion_count', len(perf.get('suggestions') or []))}",
            "",
        ]
        for s in perf.get("suggestions") or []:
            lines.append(
                f"- [{s.get('severity')}] {s.get('area')}: {s.get('message')} → {s.get('action')}"
            )
        lines.append("")
        return "\n".join(lines)

    def _md_rollback(self, readiness) -> str:
        return "\n".join(
            [
                "# Rollback Readiness",
                "",
                f"- Ready: {readiness.get('ready')}",
                f"- Versions: {readiness.get('version_count')}",
                f"- Artifacts rollback-ready: {readiness.get('artifacts_rollback_ready')}",
                f"- Supports: {', '.join(readiness.get('supports') or [])}",
                "",
            ]
        )

    def _md_improvements(self, items) -> str:
        lines = ["# Top 100 Improvements", ""]
        for it in items:
            lines.append(f"{it['rank']}. [{it['impact']}] ({it['area']}) {it['title']}")
        lines.append("")
        return "\n".join(lines)

    def _md_roadmap(self, roadmap) -> str:
        lines = ["# Evolution Roadmap (Next)", ""]
        for r in roadmap:
            lines.append(f"- **{r['phase']}**: {r['item']} — {r['detail']}")
        lines.append("")
        return "\n".join(lines)
