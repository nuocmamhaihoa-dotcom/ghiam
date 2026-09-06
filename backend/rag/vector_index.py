"""JSON vector index for knowledge chunks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.embeddings import cosine, embed, lexical_overlap, overlap_stats

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = ROOT / "vector_store" / "rag_index.json"


class VectorIndex:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_INDEX
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.rows = []
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.rows = list(raw.get("rows") or [])

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"rows": self.rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        self.rows = []

    def upsert(self, *, doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        row = {
            "id": doc_id,
            "text": text,
            "vector": embed(text),
            "metadata": metadata or {},
        }
        for i, existing in enumerate(self.rows):
            if existing.get("id") == doc_id:
                self.rows[i] = row
                return
        self.rows.append(row)

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        min_score: float = 0.2,
        min_overlap: float = 0.2,
    ) -> list[dict[str, Any]]:
        qv = embed(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self.rows:
            overlap, n_inter, _ = overlap_stats(query, row.get("text") or "")
            if overlap < min_overlap or n_inter < 2:
                continue
            score = cosine(qv, row.get("vector") or [])
            # Blend vector similarity with lexical overlap for evidence discipline.
            blended = 0.55 * score + 0.45 * overlap
            if blended >= min_score:
                scored.append((blended, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, row in scored[:limit]:
            out.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "score": round(score, 4),
                    "metadata": row.get("metadata") or {},
                }
            )
        return out
