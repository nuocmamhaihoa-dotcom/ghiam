"""JSON-backed graph store with version history and duplicate guards."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from memory_graph.types import NODE_TYPES, Edge, Node

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH_PATH = ROOT / "vector_store" / "memory_graph.json"
DEFAULT_HISTORY_PATH = ROOT / "vector_store" / "memory_graph_history.jsonl"


class GraphStore:
    def __init__(
        self,
        *,
        graph_path: Path | None = None,
        history_path: Path | None = None,
    ) -> None:
        self.graph_path = graph_path or DEFAULT_GRAPH_PATH
        self.history_path = history_path or DEFAULT_HISTORY_PATH
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}
        self.load()

    def load(self) -> None:
        if not self.graph_path.exists():
            return
        raw = json.loads(self.graph_path.read_text(encoding="utf-8"))
        for row in raw.get("nodes") or []:
            node = Node(**{k: row[k] for k in Node.__dataclass_fields__ if k in row})
            self._nodes[node.id] = node
        for row in raw.get("edges") or []:
            edge = Edge(**{k: row[k] for k in Edge.__dataclass_fields__ if k in row})
            self._edges[edge.id] = edge

    def save(self) -> None:
        payload = {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
            "saved_at": time.time(),
        }
        self.graph_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def upsert_node(self, node: Node) -> Node:
        if node.type not in NODE_TYPES:
            raise ValueError(f"unsupported node type: {node.type}")
        existing = self._nodes.get(node.id)
        if existing and existing.content == node.content and existing.label == node.label:
            existing.confidence = max(existing.confidence, node.confidence)
            existing.timestamp = max(existing.timestamp, node.timestamp or time.time())
            self._nodes[node.id] = existing
            self._append_history("node_touch", existing.to_dict())
            return existing
        if existing:
            major, minor, patch = _parse_version(existing.version)
            node.version = f"{major}.{minor}.{patch + 1}"
            node.metadata = {
                **existing.metadata,
                **node.metadata,
                "previous_version": existing.version,
            }
        if not node.timestamp:
            node.timestamp = time.time()
        self._nodes[node.id] = node
        self._append_history("node_upsert", node.to_dict())
        return node

    def upsert_edge(self, edge: Edge) -> Edge:
        for existing in self._edges.values():
            if (
                existing.source == edge.source
                and existing.target == edge.target
                and existing.relation == edge.relation
            ):
                existing.confidence = max(existing.confidence, edge.confidence)
                existing.timestamp = max(existing.timestamp, edge.timestamp or time.time())
                self._edges[existing.id] = existing
                self._append_history("edge_touch", existing.to_dict())
                return existing
        if not edge.timestamp:
            edge.timestamp = time.time()
        self._edges[edge.id] = edge
        self._append_history("edge_upsert", edge.to_dict())
        return edge

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def list_nodes(self, *, node_type: str | None = None) -> list[Node]:
        rows = list(self._nodes.values())
        if node_type:
            rows = [n for n in rows if n.type == node_type]
        return rows

    def list_edges(self) -> list[Edge]:
        return list(self._edges.values())

    def neighbors(self, node_id: str, *, relation: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for edge in self._edges.values():
            if relation and edge.relation != relation:
                continue
            if edge.source == node_id:
                target = self._nodes.get(edge.target)
                if target:
                    out.append({"direction": "out", "edge": edge.to_dict(), "node": target.to_dict()})
            elif edge.target == node_id:
                source = self._nodes.get(edge.source)
                if source:
                    out.append({"direction": "in", "edge": edge.to_dict(), "node": source.to_dict()})
        return out

    def version_history(self, entity_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.history_path.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                payload = row.get("payload") or {}
                if payload.get("id") == entity_id:
                    rows.append(row)
        return rows[-limit:]

    def export_graph(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
            "stats": {
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
                "types": sorted({n.type for n in self._nodes.values()}),
            },
        }

    def validate_integrity(self) -> list[str]:
        errors: list[str] = []
        node_ids = set(self._nodes)
        for edge in self._edges.values():
            if edge.source not in node_ids:
                errors.append(f"dangling edge source: {edge.id} -> {edge.source}")
            if edge.target not in node_ids:
                errors.append(f"dangling edge target: {edge.id} -> {edge.target}")
        seen_content: set[tuple[str, str, str]] = set()
        for node in self._nodes.values():
            key = (node.type, node.label.strip().lower(), node.content.strip().lower())
            if key in seen_content and node.content.strip():
                errors.append(f"duplicate node content: {node.id}")
            seen_content.add(key)
        return errors

    def clear(self) -> None:
        self._nodes.clear()
        self._edges.clear()

    def _append_history(self, event: str, payload: dict[str, Any]) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"event": event, "timestamp": time.time(), "payload": payload},
                    ensure_ascii=False,
                )
                + "\n"
            )


def _parse_version(version: str) -> tuple[int, int, int]:
    parts = (version or "1.0.0").split(".")
    nums = [int(p) if p.isdigit() else 0 for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]
