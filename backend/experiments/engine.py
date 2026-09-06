"""Experiment Engine for Evolution System.

Creates A/B tests, records observations, and concludes which arm wins.
Winners are NEVER auto-promoted to production.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import new_id, now_iso

KPI_KEYS = (
    "accuracy",
    "qa_agreement",
    "revenue_impact",
    "coaching_effectiveness",
    "conversion_improvement",
)


@dataclass
class ExperimentResult:
    experiment_id: str
    name: str
    arm_a: dict[str, Any]
    arm_b: dict[str, Any]
    metrics_a: dict[str, float]
    metrics_b: dict[str, float]
    samples_a: int
    samples_b: int
    winner: str
    conclusion: str
    auto_promoted: bool
    status: str
    created_at: str
    concluded_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class ExperimentEngine:
    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()

    def create(
        self,
        name: str,
        arm_a: Mapping[str, Any],
        arm_b: Mapping[str, Any],
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ExperimentResult:
        result = ExperimentResult(
            experiment_id=new_id("exp"),
            name=name,
            arm_a=dict(arm_a),
            arm_b=dict(arm_b),
            metrics_a={k: 0.0 for k in KPI_KEYS},
            metrics_b={k: 0.0 for k in KPI_KEYS},
            samples_a=0,
            samples_b=0,
            winner="insufficient_data",
            conclusion="Experiment created; awaiting samples.",
            auto_promoted=False,
            status="running",
            created_at=now_iso(),
            metadata=dict(metadata or {}),
        )
        self.store.append_experiment(result.to_dict())
        return result

    def record(
        self,
        experiment_id: str,
        arm: str,
        metrics: Mapping[str, float],
    ) -> dict[str, Any]:
        arm_key = arm.lower().strip()
        if arm_key not in {"a", "b"}:
            raise ValueError("arm must be 'a' or 'b'")

        rows = self.store.list_experiments()
        row = next((r for r in rows if r.get("experiment_id") == experiment_id), None)
        if row is None:
            raise ValueError(f"Experiment not found: {experiment_id}")
        if row.get("status") != "running":
            raise ValueError(f"Experiment is not running: {experiment_id}")

        history = list(row.get("history") or [])
        history.append({"arm": arm_key, "metrics": dict(metrics), "at": now_iso()})

        samples_key = f"samples_{arm_key}"
        metrics_key = f"metrics_{arm_key}"
        n = int(row.get(samples_key) or 0) + 1
        prev = dict(row.get(metrics_key) or {})
        merged: dict[str, float] = {}
        for k in KPI_KEYS:
            old = float(prev.get(k) or 0.0)
            new = float(metrics.get(k) or 0.0)
            merged[k] = ((old * (n - 1)) + new) / n

        patch = {
            samples_key: n,
            metrics_key: merged,
            "history": history[-500:],
            "auto_promoted": False,
        }
        return self.store.update_experiment(experiment_id, patch)

    def conclude(self, experiment_id: str, *, min_samples: int = 10) -> ExperimentResult:
        row = next(
            (r for r in self.store.list_experiments() if r.get("experiment_id") == experiment_id),
            None,
        )
        if row is None:
            raise ValueError(f"Experiment not found: {experiment_id}")

        sa = int(row.get("samples_a") or 0)
        sb = int(row.get("samples_b") or 0)
        ma = {k: float((row.get("metrics_a") or {}).get(k) or 0.0) for k in KPI_KEYS}
        mb = {k: float((row.get("metrics_b") or {}).get(k) or 0.0) for k in KPI_KEYS}

        if sa < min_samples or sb < min_samples:
            winner = "insufficient_data"
            conclusion = (
                f"Need at least {min_samples} samples per arm "
                f"(have a={sa}, b={sb}). Not concluding."
            )
            status = "running"
            concluded_at = None
        else:
            score_a = _avg([ma[k] for k in KPI_KEYS])
            score_b = _avg([mb[k] for k in KPI_KEYS])
            if abs(score_a - score_b) < 0.02:
                winner = "tie"
                conclusion = f"Tie (a={score_a:.4f}, b={score_b:.4f}). No auto-promote."
            elif score_a > score_b:
                winner = "a"
                conclusion = (
                    f"Arm A wins (a={score_a:.4f} > b={score_b:.4f}). "
                    "Requires QA approval before production."
                )
            else:
                winner = "b"
                conclusion = (
                    f"Arm B wins (b={score_b:.4f} > a={score_a:.4f}). "
                    "Requires QA approval before production."
                )
            status = "concluded"
            concluded_at = now_iso()

        patch = {
            "winner": winner,
            "conclusion": conclusion,
            "status": status,
            "concluded_at": concluded_at,
            "auto_promoted": False,
        }
        updated = self.store.update_experiment(experiment_id, patch)
        return ExperimentResult(
            experiment_id=updated["experiment_id"],
            name=updated.get("name", ""),
            arm_a=dict(updated.get("arm_a") or {}),
            arm_b=dict(updated.get("arm_b") or {}),
            metrics_a=ma,
            metrics_b=mb,
            samples_a=sa,
            samples_b=sb,
            winner=winner,
            conclusion=conclusion,
            auto_promoted=False,
            status=status,
            created_at=str(updated.get("created_at") or ""),
            concluded_at=concluded_at,
            metadata=dict(updated.get("metadata") or {}),
        )

    def list_experiments(self) -> list[dict[str, Any]]:
        return self.store.list_experiments()
