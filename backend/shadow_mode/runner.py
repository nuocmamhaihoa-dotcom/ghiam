"""Shadow Mode dual-pipeline runner.

Pipeline A = Production (current).
Pipeline B = Candidate (experiment).

Never affects the end user — comparison and reporting only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import PipelineOutput, ShadowDelta, new_id, now_iso

DEFAULT_THRESHOLDS: dict[str, float] = {
    "score": 8.0,
    "root_cause": 0.5,
    "emotion": 0.5,
    "buying_signal": 0.15,
    "coaching": 0.5,
    "revenue_leak": 0.15,
}


@dataclass
class ShadowCompareReport:
    report_id: str
    call_id: str
    production: dict[str, Any]
    candidate: dict[str, Any]
    deltas: list[dict[str, Any]]
    significant: bool
    thresholds: dict[str, float]
    user_impact: bool
    created_at: str
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_output(value: Any, *, default_call_id: str = "") -> PipelineOutput:
    if isinstance(value, PipelineOutput):
        return value
    if isinstance(value, Mapping):
        return PipelineOutput(
            call_id=str(value.get("call_id") or default_call_id or ""),
            score=float(value.get("score", 0.0) or 0.0),
            root_cause=str(value.get("root_cause", "") or ""),
            emotion=str(value.get("emotion", "") or ""),
            buying_signal=float(value.get("buying_signal", 0.0) or 0.0),
            coaching=str(value.get("coaching", "") or ""),
            revenue_leak=float(value.get("revenue_leak", 0.0) or 0.0),
            latency_ms=float(value.get("latency_ms", 0.0) or 0.0),
            model_versions=dict(value.get("model_versions") or {}),
            extras=dict(value.get("extras") or {}),
        )
    raise TypeError(f"Unsupported pipeline output type: {type(value)!r}")


def _text_delta(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta and not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb) or 1
    return 1.0 - (inter / union)


class ShadowMode:
    """Run production + candidate pipelines and compare outputs."""

    def __init__(
        self,
        store: Optional[EvolutionStore] = None,
        thresholds: Optional[Mapping[str, float]] = None,
    ) -> None:
        self.store = store or EvolutionStore()
        self.thresholds = {**DEFAULT_THRESHOLDS, **(dict(thresholds) if thresholds else {})}

    def compare(
        self,
        call_id: str,
        production: Any,
        candidate: Any,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ShadowCompareReport:
        prod = _as_output(production, default_call_id=call_id)
        cand = _as_output(candidate, default_call_id=call_id)
        deltas: list[ShadowDelta] = []

        score_thr = self.thresholds["score"]
        score_diff = abs(prod.score - cand.score)
        deltas.append(
            ShadowDelta(
                call_id=call_id,
                field="score",
                production=prod.score,
                candidate=cand.score,
                abs_delta=score_diff,
                threshold=score_thr,
                exceeded=score_diff > score_thr,
            )
        )

        for field_name, a, b, thr, numeric in (
            ("root_cause", prod.root_cause, cand.root_cause, self.thresholds["root_cause"], False),
            ("emotion", prod.emotion, cand.emotion, self.thresholds["emotion"], False),
            ("buying_signal", prod.buying_signal, cand.buying_signal, self.thresholds["buying_signal"], True),
            ("coaching", prod.coaching, cand.coaching, self.thresholds["coaching"], False),
            ("revenue_leak", prod.revenue_leak, cand.revenue_leak, self.thresholds["revenue_leak"], True),
        ):
            d = abs(float(a) - float(b)) if numeric else _text_delta(str(a), str(b))
            deltas.append(
                ShadowDelta(
                    call_id=call_id,
                    field=field_name,
                    production=a,
                    candidate=b,
                    abs_delta=d,
                    threshold=thr,
                    exceeded=d > thr,
                )
            )

        significant = any(x.exceeded for x in deltas)
        n_exc = sum(1 for x in deltas if x.exceeded)
        summary = (
            f"Shadow compare for {call_id}: {n_exc}/{len(deltas)} fields above threshold"
            if significant
            else f"Shadow compare for {call_id}: within thresholds"
        )
        report = ShadowCompareReport(
            report_id=new_id("shd"),
            call_id=call_id,
            production=prod.to_dict(),
            candidate=cand.to_dict(),
            deltas=[d.to_dict() for d in deltas],
            significant=significant,
            thresholds=dict(self.thresholds),
            user_impact=False,
            created_at=now_iso(),
            summary=summary,
            metadata=dict(metadata or {}),
        )
        self.store.append_shadow_report(report.to_dict())
        return report

    def run(
        self,
        call_id: str,
        production_fn: Callable[[], Any],
        candidate_fn: Callable[[], Any],
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ShadowCompareReport:
        """Execute both pipelines; candidate failures never propagate to user path."""
        production = production_fn()
        try:
            candidate = candidate_fn()
        except Exception as exc:  # noqa: BLE001 — shadow must not break prod
            candidate = PipelineOutput(
                call_id=call_id,
                score=0.0,
                root_cause="candidate_error",
                emotion="",
                buying_signal=0.0,
                coaching="",
                revenue_leak=0.0,
                extras={"error": str(exc)},
            )
        return self.compare(call_id, production, candidate, metadata=metadata)
