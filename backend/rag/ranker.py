"""Evidence ranking with citation readiness."""

from __future__ import annotations

from typing import Any


def rank_evidence(hits: list[dict[str, Any]], *, min_score: float = 0.28) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hit in hits:
        if float(hit.get("score") or 0) < min_score:
            continue
        out.append(
            {
                **hit,
                "citation": {
                    "id": hit.get("id"),
                    "source": hit.get("source"),
                    "node_type": hit.get("node_type"),
                    "score": hit.get("score"),
                },
            }
        )
    out.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    return out
