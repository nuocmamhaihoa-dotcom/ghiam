"""Persistence for Autonomous Sales AI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AutonomousStore:
    def __init__(self, root: Path | None = None) -> None:
        repo = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else (repo / "datasets" / "autonomous")
        self.root.mkdir(parents=True, exist_ok=True)
        self.recommendations_path = self.root / "recommendations.jsonl"
        self.jobs_path = self.root / "jobs.jsonl"
        self.approvals_path = self.root / "approvals.jsonl"
        self.metrics_path = self.root / "metrics.json"
        if not self.metrics_path.exists():
            self.save_metrics(
                {
                    "recommendations": 0,
                    "automations_run": 0,
                    "automations_blocked": 0,
                    "approvals_pending": 0,
                    "approvals_decided": 0,
                    "rule_changes_applied": 0,
                }
            )

    def append_recommendation(self, row: dict[str, Any]) -> None:
        self._append(self.recommendations_path, row)

    def append_job(self, row: dict[str, Any]) -> None:
        self._append(self.jobs_path, row)

    def append_approval(self, row: dict[str, Any]) -> None:
        self._append(self.approvals_path, row)

    def list_recommendations(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.recommendations_path, limit)

    def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.jobs_path, limit)

    def list_approvals(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.approvals_path, limit)

    def get_metrics(self) -> dict[str, Any]:
        if not self.metrics_path.exists():
            return {}
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))

    def save_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _tail(self, path: Path, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-limit:]
