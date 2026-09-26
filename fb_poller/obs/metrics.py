from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Metrics:
    """In-process rolling metrics for PA1 KPI gates."""

    path: Path | None = None
    window: int = 500
    _lock: Lock = field(default_factory=Lock)
    _latencies: deque[int] = field(default_factory=lambda: deque(maxlen=500))
    _results: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    jobs: int = 0
    ok: int = 0
    fail: int = 0
    fetched: int = 0
    inserted: int = 0
    errors: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self._latencies = deque(maxlen=self.window)
        self._results = deque(maxlen=self.window)

    def record(
        self,
        *,
        ok: bool,
        latency_ms: int,
        fetched: int,
        inserted: int,
        error_code: str | None,
        post_id: int,
        tier: str,
        worker_slot: int,
    ) -> None:
        with self._lock:
            self.jobs += 1
            self.fetched += fetched
            self.inserted += inserted
            self._latencies.append(latency_ms)
            if ok:
                self.ok += 1
                self._results.append("ok")
            else:
                self.fail += 1
                code = error_code or "unknown"
                self.errors[code] += 1
                self._results.append(code)

            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                row = {
                    "ts": _utcnow().isoformat(),
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "fetched": fetched,
                    "inserted": inserted,
                    "error_code": error_code,
                    "post_id": post_id,
                    "tier": tier,
                    "worker_slot": worker_slot,
                }
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict:
        with self._lock:
            lats = list(self._latencies)
            results = list(self._results)
        lats_sorted = sorted(lats)
        p95 = lats_sorted[int(0.95 * (len(lats_sorted) - 1))] if lats_sorted else None
        p50 = lats_sorted[len(lats_sorted) // 2] if lats_sorted else None
        window_ok = results.count("ok")
        window_n = len(results) or 1
        return {
            "jobs": self.jobs,
            "ok": self.ok,
            "fail": self.fail,
            "success_rate": round(self.ok / self.jobs, 4) if self.jobs else None,
            "window_success_rate": round(window_ok / window_n, 4),
            "fetched": self.fetched,
            "inserted": self.inserted,
            "p50_ms": p50,
            "p95_ms": p95,
            "errors": dict(self.errors),
            "ready_for_30s": bool(
                self.jobs >= 50
                and (self.ok / self.jobs) >= 0.85
                and p95 is not None
                and p95 <= 5000
            ),
        }
