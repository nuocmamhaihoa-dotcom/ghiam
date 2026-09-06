"""Persistence for War Room events, alerts, and metrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WarRoomStore:
    def __init__(self, root: Path | None = None) -> None:
        repo = Path(__file__).resolve().parents[2]
        self.root = Path(root) if root else (repo / "datasets" / "war_room")
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.alerts_path = self.root / "alerts.jsonl"
        self.metrics_path = self.root / "metrics.json"
        if not self.metrics_path.exists():
            self.save_metrics(
                {
                    "events": 0,
                    "alerts": 0,
                    "active_calls": 0,
                    "online_agents": 0,
                    "critical_alerts": 0,
                    "avg_buy_signal": 0.0,
                }
            )

    def append_event(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_alert(self, alert: dict[str, Any]) -> None:
        with self.alerts_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert, ensure_ascii=False) + "\n")

    def list_events(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.events_path, limit)

    def list_alerts(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._tail(self.alerts_path, limit)

    def get_metrics(self) -> dict[str, Any]:
        if not self.metrics_path.exists():
            return {}
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))

    def save_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
