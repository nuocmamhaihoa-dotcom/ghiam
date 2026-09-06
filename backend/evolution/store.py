"""File-backed Evolution Engine store."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class EvolutionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else Path("data/evolution")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        for name in (
            "proposals.jsonl",
            "shadow_reports.jsonl",
            "experiments.jsonl",
            "failures.jsonl",
            "alerts.jsonl",
            "versions.jsonl",
        ):
            (self.root / name).touch(exist_ok=True)
        if not (self.root / "scorecard.json").exists():
            (self.root / "scorecard.json").write_text("{}\n", encoding="utf-8")
        if not (self.root / "observability.json").exists():
            (self.root / "observability.json").write_text(
                json.dumps(
                    {
                        "error_rate": 0.0,
                        "ai_latency_ms": 0.0,
                        "queue_depth": 0,
                        "upload_speed_mbps": 0.0,
                        "transcript_speed_x": 0.0,
                        "dashboard_p95_ms": 0.0,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    def _append(self, filename: str, row: dict[str, Any]) -> None:
        with self._lock:
            with (self.root / filename).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read(self, filename: str) -> list[dict[str, Any]]:
        path = self.root / filename
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _rewrite(self, filename: str, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            (self.root / filename).write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )

    def append_proposal(self, row: dict[str, Any]) -> None:
        self._append("proposals.jsonl", row)

    def list_proposals(self) -> list[dict[str, Any]]:
        return self._read("proposals.jsonl")

    def update_proposal(self, proposal_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        rows = self.list_proposals()
        found = None
        for i, row in enumerate(rows):
            if row.get("proposal_id") == proposal_id:
                rows[i] = {**row, **patch}
                found = rows[i]
                break
        if found is None:
            raise ValueError(f"Proposal not found: {proposal_id}")
        self._rewrite("proposals.jsonl", rows)
        return found

    def append_shadow_report(self, row: dict[str, Any]) -> None:
        self._append("shadow_reports.jsonl", row)

    def list_shadow_reports(self) -> list[dict[str, Any]]:
        return self._read("shadow_reports.jsonl")

    def append_experiment(self, row: dict[str, Any]) -> None:
        self._append("experiments.jsonl", row)

    def list_experiments(self) -> list[dict[str, Any]]:
        return self._read("experiments.jsonl")

    def update_experiment(self, experiment_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        rows = self.list_experiments()
        found = None
        for i, row in enumerate(rows):
            if row.get("experiment_id") == experiment_id:
                rows[i] = {**row, **patch}
                found = rows[i]
                break
        if found is None:
            raise ValueError(f"Experiment not found: {experiment_id}")
        self._rewrite("experiments.jsonl", rows)
        return found

    def append_failure(self, row: dict[str, Any]) -> None:
        self._append("failures.jsonl", row)

    def list_failures(self) -> list[dict[str, Any]]:
        return self._read("failures.jsonl")

    def append_alert(self, row: dict[str, Any]) -> None:
        self._append("alerts.jsonl", row)

    def list_alerts(self) -> list[dict[str, Any]]:
        return self._read("alerts.jsonl")

    def append_version(self, row: dict[str, Any]) -> None:
        self._append("versions.jsonl", row)

    def list_versions(self, artifact_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._read("versions.jsonl")
        if artifact_id:
            rows = [r for r in rows if r.get("artifact_id") == artifact_id]
        return rows

    def get_scorecard(self) -> dict[str, Any]:
        return json.loads((self.root / "scorecard.json").read_text(encoding="utf-8") or "{}")

    def save_scorecard(self, data: dict[str, Any]) -> None:
        with self._lock:
            (self.root / "scorecard.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

    def get_observability(self) -> dict[str, Any]:
        return json.loads((self.root / "observability.json").read_text(encoding="utf-8"))

    def save_observability(self, data: dict[str, Any]) -> None:
        with self._lock:
            (self.root / "observability.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
