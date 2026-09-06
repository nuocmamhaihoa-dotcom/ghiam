"""Enterprise Memory Graph service — long-term knowledge + evidence RAG."""

from __future__ import annotations

from typing import Any

from memory_graph.graph import EnterpriseMemoryGraph
from memory_graph.sync import sync_all, sync_corpus
from memory_graph.types import NO_DATA, NODE_TYPES
from rag.pipeline import EvidenceRAG


class MemoryGraphService:
    """Application façade over EnterpriseMemoryGraph + EvidenceRAG."""

    NODE_TYPES = NODE_TYPES

    def __init__(self) -> None:
        self.graph = EnterpriseMemoryGraph()
        self.rag = EvidenceRAG(graph=self.graph)

    def build_from_analysis(
        self,
        *,
        call_id: str,
        violations: list[dict[str, Any]],
        root_cause: dict[str, Any],
        coaching: dict[str, Any],
        intents: list[dict[str, Any]] | None = None,
        objections: list[dict[str, Any]] | None = None,
        products: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = self.graph.build_from_analysis(
            call_id=call_id,
            violations=violations,
            root_cause=root_cause,
            coaching=coaching,
            intents=intents,
            objections=objections,
            products=products,
        )
        # Keep scoring/pipeline compatible shape.
        return {
            "call_id": payload.get("call_id", call_id),
            "nodes": payload.get("nodes") or [],
            "edges": payload.get("edges") or [],
            "stats": payload.get("stats")
            or {
                "node_count": len(payload.get("nodes") or []),
                "edge_count": len(payload.get("edges") or []),
                "types": sorted({n.get("type") for n in (payload.get("nodes") or []) if n.get("type")}),
            },
            "visualization": payload.get("visualization")
            or {
                "layout": "force-directed",
                "node_types": list(self.NODE_TYPES),
            },
            "nodes_touched": payload.get("nodes_touched") or [],
            "status": payload.get("status") or "ok",
        }

    def search(
        self,
        graph: dict[str, Any] | None = None,
        *,
        node_type: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Search provided graph snapshot (legacy) or live enterprise graph."""
        q = (query or "").strip().lower()
        aliases = {"rule": "rulebook", "rules": "rulebook"}
        resolved_type = aliases.get(node_type or "", node_type)
        if graph and (graph.get("nodes") is not None):
            nodes = graph.get("nodes") or []
            filtered = []
            for node in nodes:
                if resolved_type and node.get("type") != resolved_type:
                    continue
                if q and q not in str(node.get("label", "")).lower() and q not in str(node.get("id", "")).lower():
                    continue
                filtered.append(node)
            return {"matches": filtered, "count": len(filtered), "mode": "snapshot"}

        hits = self.graph.search_nodes(query or "", node_type=resolved_type, limit=50)
        matches = [n.to_dict() for n in hits]
        return {"matches": matches, "count": len(matches), "mode": "live"}

    def search_knowledge(self, query: str, *, node_type: str | None = None, limit: int = 20) -> dict[str, Any]:
        hits = self.graph.search_nodes(query, node_type=node_type, limit=limit)
        if not hits:
            return {"matches": [], "count": 0, "message": NO_DATA}
        return {"matches": [n.to_dict() for n in hits], "count": len(hits)}

    def similar_objection(self, text: str, *, limit: int = 10) -> dict[str, Any]:
        rows = self.graph.similar_objections(text, limit=limit)
        if not rows:
            return {"matches": [], "count": 0, "message": NO_DATA}
        return {"matches": rows, "count": len(rows)}

    def similar_call(self, text: str, *, limit: int = 10) -> dict[str, Any]:
        rows = self.graph.similar_calls(text, limit=limit)
        if not rows:
            return {"matches": [], "count": 0, "message": NO_DATA}
        return {"matches": rows, "count": len(rows)}

    def update_knowledge(
        self,
        *,
        node_id: str,
        node_type: str,
        label: str,
        content: str,
        source: str,
        confidence: float = 0.9,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node = self.graph.upsert_node(
            node_id=node_id,
            node_type=node_type,
            label=label,
            content=content,
            source=source,
            confidence=confidence,
            metadata=metadata,
        )
        self.graph.store.save()
        self.rag.reindex()
        return {"node": node.to_dict(), "status": "ok"}

    def version_history(self, entity_id: str, *, limit: int = 50) -> dict[str, Any]:
        rows = self.graph.version_history(entity_id, limit=limit)
        if not rows:
            return {"history": [], "count": 0, "message": NO_DATA}
        return {"history": rows, "count": len(rows)}

    def explore(self, *, limit: int = 200) -> dict[str, Any]:
        return self.graph.explore(limit=limit)

    def ask(self, question: str, *, limit: int = 6) -> dict[str, Any]:
        if not self.graph.store.list_nodes():
            self.sync()
        return self.rag.ask(question, limit=limit)

    def sync(self, *, kind: str | None = None, reset: bool = False) -> dict[str, Any]:
        if kind:
            result = sync_corpus(kind, self.graph)
        else:
            result = sync_all(self.graph, reset=reset)
        self.rag.reindex()
        return result

    def quality_report(self) -> dict[str, Any]:
        return self.graph.quality_report()
