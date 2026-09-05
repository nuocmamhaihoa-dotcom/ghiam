"""JSONL persistence for negotiation sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class NegotiationStore:
    def __init__(self, root: Path | None = None) -> None:
        repo = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else (repo / "datasets" / "negotiation")
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.root / "sessions.jsonl"
        self.metrics_path = self.root / "metrics.json"
        if not self.metrics_path.exists():
            self.save_metrics(
                {
                    "sessions": 0,
                    "avg_buy_probability": 0.0,
                    "avg_exit_risk": 0.0,
                    "avg_win_probability": 0.0,
                    "avg_confidence": 0.0,
                    "strategy_picks": {},
                }
            )

    def append_session(self, session: dict[str, Any]) -> None:
        with self.sessions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(session, ensure_ascii=False) + "\n")

    def list_sessions(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.sessions_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.sessions_path.open(encoding="utf-8") as fh:
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
