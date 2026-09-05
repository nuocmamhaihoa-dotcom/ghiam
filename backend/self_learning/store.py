"""Persistent store for Self-Learning Lab."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "datasets" / "self_learning"
DATA.mkdir(parents=True, exist_ok=True)

EMPTY_KNOWLEDGE = {
    "layer_1_raw_calls": [],
    "layer_2_verified_knowledge": [],
    "layer_3_approved_rules": [],
    "layer_4_production_knowledge": [],
}

EMPTY_METRICS = {
    "pattern_accuracy": 0.85,
    "intent_accuracy": 0.82,
    "emotion_accuracy": 0.80,
    "root_cause_accuracy": 0.78,
    "revenue_leak_accuracy": 0.76,
    "alerts": [],
    "learning_velocity": 0.0,
    "knowledge_growth": 0,
}


class LearningStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DATA
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.root / "raw_calls.jsonl"
        self.patterns_path = self.root / "patterns.jsonl"
        self.clusters_path = self.root / "clusters.jsonl"
        self.proposals_path = self.root / "proposals.jsonl"
        self.knowledge_path = self.root / "knowledge.json"
        self.versions_path = self.root / "versions.jsonl"
        self.metrics_path = self.root / "metrics.json"
        self.ab_path = self.root / "ab_tests.jsonl"
        self.evals_path = self.root / "self_evals.jsonl"
        if not self.knowledge_path.exists():
            self._write_json(self.knowledge_path, dict(EMPTY_KNOWLEDGE))
        if not self.metrics_path.exists():
            self._write_json(self.metrics_path, dict(EMPTY_METRICS))

    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def append_raw_call(self, row: dict[str, Any]) -> None: self._append(self.raw_path, row)
    def list_raw_calls(self) -> list[dict[str, Any]]: return self._read_jsonl(self.raw_path)
    def append_pattern(self, row: dict[str, Any]) -> None: self._append(self.patterns_path, row)
    def list_patterns(self) -> list[dict[str, Any]]: return self._read_jsonl(self.patterns_path)
    def append_cluster(self, row: dict[str, Any]) -> None: self._append(self.clusters_path, row)
    def list_clusters(self) -> list[dict[str, Any]]: return self._read_jsonl(self.clusters_path)
    def append_proposal(self, row: dict[str, Any]) -> None: self._append(self.proposals_path, row)
    def list_proposals(self) -> list[dict[str, Any]]: return self._read_jsonl(self.proposals_path)
    def rewrite_proposals(self, rows: list[dict[str, Any]]) -> None: self._write_jsonl(self.proposals_path, rows)
    def get_knowledge(self) -> dict[str, Any]: return self._read_json(self.knowledge_path)
    def save_knowledge(self, data: dict[str, Any]) -> None: self._write_json(self.knowledge_path, data)
    def append_version(self, row: dict[str, Any]) -> None: self._append(self.versions_path, row)
    def list_versions(self) -> list[dict[str, Any]]: return self._read_jsonl(self.versions_path)
    def get_metrics(self) -> dict[str, Any]: return self._read_json(self.metrics_path)
    def save_metrics(self, data: dict[str, Any]) -> None: self._write_json(self.metrics_path, data)
    def append_ab(self, row: dict[str, Any]) -> None: self._append(self.ab_path, row)
    def list_ab(self) -> list[dict[str, Any]]: return self._read_jsonl(self.ab_path)
    def rewrite_ab(self, rows: list[dict[str, Any]]) -> None: self._write_jsonl(self.ab_path, rows)
    def append_eval(self, row: dict[str, Any]) -> None: self._append(self.evals_path, row)
    def list_evals(self) -> list[dict[str, Any]]: return self._read_jsonl(self.evals_path)
