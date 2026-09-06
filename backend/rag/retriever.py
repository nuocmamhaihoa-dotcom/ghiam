"""Hybrid retriever: vector search + knowledge graph expansion."""

from __future__ import annotations

from typing import Any

from memory_graph.graph import EnterpriseMemoryGraph
from rag.embeddings import lexical_overlap, overlap_stats
from rag.vector_index import VectorIndex


class HybridRetriever:
    def __init__(
        self,
        graph: EnterpriseMemoryGraph | None = None,
        index: VectorIndex | None = None,
    ) -> None:
        self.graph = graph or EnterpriseMemoryGraph()
        self.index = index or VectorIndex()

    def retrieve(self, question: str, *, limit: int = 8) -> list[dict[str, Any]]:
        vector_hits = self.index.search(question, limit=limit, min_score=0.25, min_overlap=0.25)
        graph_hits = self.graph.search_nodes(question, limit=limit)
        merged: dict[str, dict[str, Any]] = {}

        for hit in vector_hits:
            merged[hit["id"]] = {
                "id": hit["id"],
                "text": hit["text"],
                "score": float(hit["score"]),
                "source": (hit.get("metadata") or {}).get("source", "vector"),
                "node_type": (hit.get("metadata") or {}).get("type"),
                "channel": "vector",
                "metadata": hit.get("metadata") or {},
            }

        for node in graph_hits:
            text = f"{node.label}. {node.content}".strip()
            overlap, n_inter, _ = overlap_stats(question, text)
            if overlap < 0.25 or n_inter < 2:
                continue
            score = (0.4 * node.confidence) + (0.6 * overlap)
            existing = merged.get(node.id)
            if existing:
                existing["score"] = max(existing["score"], score)
                existing["channel"] = "hybrid"
            else:
                merged[node.id] = {
                    "id": node.id,
                    "text": text,
                    "score": score,
                    "source": node.source,
                    "node_type": node.type,
                    "channel": "graph",
                    "metadata": {
                        "label": node.label,
                        "version": node.version,
                        **node.metadata,
                    },
                }
            for neigh in self.graph.neighbors(node.id)[:3]:
                n = neigh["node"]
                ntext = f"{n.get('label','')}. {n.get('content','')}".strip()
                noverlap, n_inter2, _ = overlap_stats(question, ntext)
                if noverlap < 0.2 or n_inter2 < 2:
                    continue
                nid = n["id"]
                bonus = 0.2 * noverlap + 0.1 * float(neigh["edge"].get("confidence") or 0.5)
                if nid in merged:
                    merged[nid]["score"] += bonus
                else:
                    merged[nid] = {
                        "id": nid,
                        "text": ntext,
                        "score": bonus,
                        "source": n.get("source"),
                        "node_type": n.get("type"),
                        "channel": "graph_expand",
                        "metadata": {
                            "via": node.id,
                            "relation": neigh["edge"].get("relation"),
                        },
                    }

        ranked = sorted(merged.values(), key=lambda r: r["score"], reverse=True)
        return ranked[:limit]
