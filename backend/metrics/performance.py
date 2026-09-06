"""Performance Optimizer — query/cache/queue/memory/CPU/GPU suggestions."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import new_id, now_iso


class PerformanceOptimizer:
    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()

    def analyze(self, snapshot: Mapping[str, float]) -> dict[str, Any]:
        suggestions: list[dict[str, Any]] = []

        def add(area: str, severity: str, message: str, action: str) -> None:
            suggestions.append(
                {
                    "suggestion_id": new_id("perf"),
                    "area": area,
                    "severity": severity,
                    "message": message,
                    "action": action,
                }
            )

        q = float(snapshot.get("query_p95_ms") or 0)
        if q > 500:
            add("query", "high", f"Query p95 {q:.0f}ms above 500ms", "Add indexes / reduce collect()")
        cache = float(snapshot.get("cache_hit_rate") or 1.0)
        if cache < 0.7:
            add("cache", "medium", f"Cache hit rate {cache:.2%} below 70%", "Warm hot paths / raise TTL")
        qd = float(snapshot.get("queue_depth") or 0)
        if qd > 100:
            add("queue", "high", f"Queue depth {qd:.0f}", "Scale workers / shed load")
        mem = float(snapshot.get("memory_pct") or 0)
        if mem > 85:
            add("memory", "high", f"Memory {mem:.0f}%", "Trim caches / raise instance size")
        cpu = float(snapshot.get("cpu_pct") or 0)
        if cpu > 80:
            add("cpu", "medium", f"CPU {cpu:.0f}%", "Profile hot loops / batch work")
        gpu = float(snapshot.get("gpu_pct") or 0)
        if gpu > 90:
            add("gpu", "high", f"GPU {gpu:.0f}%", "Batch inference / queue ASR jobs")

        report = {
            "report_id": new_id("preport"),
            "snapshot": dict(snapshot),
            "suggestions": suggestions,
            "suggestion_count": len(suggestions),
            "created_at": now_iso(),
        }
        if suggestions:
            self.store.append_alert(
                {
                    "alert_id": new_id("alrt"),
                    "kind": "performance",
                    "severity": "high" if any(s["severity"] == "high" for s in suggestions) else "medium",
                    "message": f"{len(suggestions)} performance suggestions",
                    "created_at": now_iso(),
                    "resolved": False,
                    "payload": report,
                }
            )
        return report
