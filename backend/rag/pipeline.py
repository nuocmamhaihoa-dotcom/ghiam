"""RAG pipeline: Question → Embed → Vector → Graph → Rank → Answer + Citation."""

from __future__ import annotations

from typing import Any

from memory_graph.graph import EnterpriseMemoryGraph
from memory_graph.sync import sync_all
from memory_graph.types import NO_DATA
from rag.ranker import rank_evidence
from rag.retriever import HybridRetriever
from rag.vector_index import VectorIndex


class EvidenceRAG:
    def __init__(
        self,
        graph: EnterpriseMemoryGraph | None = None,
        index: VectorIndex | None = None,
        *,
        auto_sync: bool = False,
    ) -> None:
        self.graph = graph or EnterpriseMemoryGraph()
        self.index = index or VectorIndex()
        self.retriever = HybridRetriever(self.graph, self.index)
        if auto_sync and not self.graph.store.list_nodes():
            self.bootstrap()

    def bootstrap(self) -> dict[str, Any]:
        result = sync_all(self.graph, reset=False)
        self.reindex()
        return result

    def reindex(self) -> int:
        self.index.clear()
        for node in self.graph.store.list_nodes():
            text = f"{node.label}. {node.content}".strip()
            self.index.upsert(
                doc_id=node.id,
                text=text,
                metadata={
                    "type": node.type,
                    "source": node.source,
                    "version": node.version,
                    "confidence": node.confidence,
                    "label": node.label,
                },
            )
        self.index.save()
        return len(self.index.rows)

    def ask(self, question: str, *, limit: int = 6) -> dict[str, Any]:
        q = (question or "").strip()
        if not q:
            return {
                "answer": NO_DATA,
                "citations": [],
                "evidence": [],
                "status": "no_data",
            }
        hits = self.retriever.retrieve(q, limit=max(limit, 8))
        evidence = rank_evidence(hits, min_score=0.28)[:limit]
        if not evidence:
            return {
                "answer": NO_DATA,
                "citations": [],
                "evidence": [],
                "status": "no_data",
            }
        lines: list[str] = []
        citations: list[dict[str, Any]] = []
        for i, ev in enumerate(evidence, start=1):
            snippet = str(ev.get("text") or "").strip()
            if not snippet:
                continue
            lines.append(f"[{i}] {snippet}")
            citations.append(ev["citation"])
        if not lines:
            return {
                "answer": NO_DATA,
                "citations": [],
                "evidence": [],
                "status": "no_data",
            }
        nl = chr(10)
        answer = "Dựa trên dữ liệu Memory Graph:" + nl + nl.join(lines)
        return {
            "answer": answer.replace("\\n", "\n") if False else (
                "Dựa trên dữ liệu Memory Graph:\n" + "\n".join(lines)
            ),
            "citations": citations,
            "evidence": evidence,
            "status": "ok",
            "question": q,
        }
