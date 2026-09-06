"""Persistence for Audio Intelligence Engine jobs and artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AudioStore:
    def __init__(self, root: Path | None = None) -> None:
        repo = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else (repo / "datasets" / "audio_engine")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "original").mkdir(exist_ok=True)
        (self.root / "repaired").mkdir(exist_ok=True)
        self.jobs_path = self.root / "jobs.jsonl"
        self.artifacts_path = self.root / "artifacts.jsonl"
        self.search_index_path = self.root / "search_index.jsonl"
        self.metrics_path = self.root / "metrics.json"
        if not self.metrics_path.exists():
            self.save_metrics(
                {
                    "uploads": 0,
                    "repairs": 0,
                    "transcripts": 0,
                    "blocked_analysis": 0,
                    "completed_pipelines": 0,
                    "batch_jobs": 0,
                    "rollbacks": 0,
                    "live_sessions": 0,
                }
            )

    def append_job(self, row: dict[str, Any]) -> None:
        self._append(self.jobs_path, row)

    def append_artifact(self, row: dict[str, Any]) -> None:
        self._append(self.artifacts_path, row)

    def append_search(self, row: dict[str, Any]) -> None:
        self._append(self.search_index_path, row)

    def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.jobs_path, limit)

    def list_artifacts(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.artifacts_path, limit)

    def list_search(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self._tail(self.search_index_path, limit)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        for row in reversed(self.list_jobs(5000)):
            if row.get("job_id") == job_id:
                return row
        return None

    def get_metrics(self) -> dict[str, Any]:
        if not self.metrics_path.exists():
            return {}
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))

    def save_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    def bump(self, key: str, amount: int | float = 1) -> dict[str, Any]:
        m = self.get_metrics()
        m[key] = type(amount)(m.get(key) or 0) + amount
        self.save_metrics(m)
        return m

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
