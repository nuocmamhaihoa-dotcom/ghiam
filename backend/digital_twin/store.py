"""JSONL persistence for Digital Twin profiles and roleplay sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "datasets" / "digital_twin"


class TwinStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.twins_path = self.root / "twins.jsonl"
        self.sessions_path = self.root / "roleplay_sessions.jsonl"
        self.training_index_path = self.root / "training_index.jsonl"
        self.metrics_path = self.root / "metrics.json"
        if not self.metrics_path.exists():
            self.save_metrics(
                {
                    "twins_trained": 0,
                    "roleplay_sessions": 0,
                    "avg_similarity": 0.0,
                    "avg_improvement": 0.0,
                    "style_consistency": 0.85,
                    "twin_accuracy": 0.82,
                    "coaching_quality": 0.80,
                    "similarity_stability": 0.78,
                }
            )

    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def save_twin(self, twin: dict[str, Any]) -> None:
        twin_id = twin.get("twin_id")
        rows = self._read_jsonl(self.twins_path)
        out: list[dict[str, Any]] = []
        replaced = False
        for row in rows:
            if row.get("twin_id") == twin_id:
                out.append(twin)
                replaced = True
            else:
                out.append(row)
        if not replaced:
            out.append(twin)
        with self.twins_path.open("w", encoding="utf-8") as fh:
            for row in out:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def list_twins(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.twins_path)

    def get_twin(self, twin_id: str) -> dict[str, Any] | None:
        for row in self.list_twins():
            if row.get("twin_id") == twin_id:
                return row
        return None

    def append_session(self, session: dict[str, Any]) -> None:
        self._append(self.sessions_path, session)

    def list_sessions(self, twin_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._read_jsonl(self.sessions_path)
        if twin_id:
            return [r for r in rows if r.get("twin_id") == twin_id]
        return rows

    def append_training_index(self, row: dict[str, Any]) -> None:
        self._append(self.training_index_path, row)

    def get_metrics(self) -> dict[str, Any]:
        if not self.metrics_path.exists():
            return {}
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))

    def save_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
