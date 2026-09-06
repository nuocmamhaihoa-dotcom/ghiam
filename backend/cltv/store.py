"""JSONL persistence for CLTV predictions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CLTVStore:
    def __init__(self, root: Path | None = None) -> None:
        repo = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else (repo / "datasets" / "cltv")
        self.root.mkdir(parents=True, exist_ok=True)
        self.predictions_path = self.root / "predictions.jsonl"
        self.metrics_path = self.root / "metrics.json"
        if not self.metrics_path.exists():
            self.save_metrics(
                {
                    "predictions": 0,
                    "avg_cltv_score": 0.0,
                    "avg_lifetime_value": 0.0,
                    "avg_churn_risk": 0.0,
                    "avg_confidence": 0.0,
                    "priority_counts": {"high_value": 0, "medium": 0, "low": 0},
                }
            )

    def append_prediction(self, prediction: dict[str, Any]) -> None:
        with self.predictions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(prediction, ensure_ascii=False) + "\n")

    def list_predictions(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.predictions_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.predictions_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-limit:]

    def get_metrics(self) -> dict[str, Any]:
        if not self.metrics_path.exists():
            return {}
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))

    def save_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
