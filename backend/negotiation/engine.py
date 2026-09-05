"""Negotiation Strategy Engine — predict next moves + multi-strategy graph."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from negotiation.predictor import predict_next_steps
from negotiation.quality import evaluate_quality
from negotiation.store import NegotiationStore
from negotiation.strategy import (
    build_strategy_graph,
    generate_strategies,
    pick_best_strategy,
    strategy_consistency,
)
from negotiation.types import NegotiationSession, new_id, now_iso


class NegotiationEngine:
    def __init__(self, store: NegotiationStore | None = None) -> None:
        self.store = store or NegotiationStore()

    def analyze(
        self,
        customer_utterance: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        select_best: bool = True,
    ) -> dict[str, Any]:
        history = history or []
        context = context or {}
        prediction = predict_next_steps(
            customer_utterance, history=history, context=context
        )
        strategies = generate_strategies(
            customer_utterance,
            prediction=prediction.to_dict(),
            context=context,
        )
        graph = build_strategy_graph(
            strategies,
            prediction.to_dict(),
            customer_utterance=customer_utterance,
        )
        best = pick_best_strategy(strategies) if strategies else None
        selected_id = best.strategy_id if best and select_best else None

        timeline = [
            {"ts": now_iso(), "event": "customer_utterance", "text": customer_utterance},
            {
                "ts": now_iso(),
                "event": "prediction",
                "next_question": prediction.next_question,
                "next_objection": prediction.next_objection,
                "exit_risk": prediction.exit_risk,
                "buy_probability": prediction.buy_probability,
            },
            {
                "ts": now_iso(),
                "event": "strategies_generated",
                "count": len(strategies),
                "kinds": [s.kind for s in strategies],
            },
        ]
        if best:
            timeline.append(
                {
                    "ts": now_iso(),
                    "event": "next_best_action",
                    "strategy_id": best.strategy_id,
                    "kind": best.kind,
                    "win_probability": best.win_probability,
                    "script": best.recommended_script,
                }
            )

        session = NegotiationSession(
            session_id=new_id("neg"),
            customer_utterance=customer_utterance,
            context=context,
            prediction=prediction.to_dict(),
            strategies=[s.to_dict() for s in strategies],
            selected_strategy_id=selected_id,
            graph=graph.to_dict(),
            timeline=timeline,
        )
        payload = session.to_dict()
        self.store.append_session(payload)
        self._update_metrics(payload, best)
        return {
            "ok": True,
            "session": payload,
            "prediction": prediction.to_dict(),
            "strategies": [s.to_dict() for s in strategies],
            "best_strategy": best.to_dict() if best else None,
            "next_best_action": best.next_best_action if best else None,
            "graph": graph.to_dict(),
            "win_probability": best.win_probability if best else 0.0,
            "strategy_consistency": strategy_consistency(strategies),
            "static_tree": False,
        }

    def predict(
        self,
        customer_utterance: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pred = predict_next_steps(customer_utterance, history=history, context=context)
        return {"ok": True, "prediction": pred.to_dict()}

    def compare_strategies(
        self,
        customer_utterance: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pred = predict_next_steps(customer_utterance, context=context or {})
        strategies = generate_strategies(
            customer_utterance, prediction=pred.to_dict(), context=context or {}
        )
        ranked = sorted(
            strategies,
            key=lambda s: s.win_probability - 0.45 * s.risk_score,
            reverse=True,
        )
        comparisons = [
            {
                "rank": i + 1,
                "strategy_id": s.strategy_id,
                "kind": s.kind,
                "label": s.label,
                "win_probability": s.win_probability,
                "risk_score": s.risk_score,
                "utility": round(s.win_probability - 0.45 * s.risk_score, 4),
                "recommended_script": s.recommended_script,
                "forbidden_script": s.forbidden_script,
            }
            for i, s in enumerate(ranked)
        ]
        return {
            "ok": True,
            "prediction": pred.to_dict(),
            "comparisons": comparisons,
            "best": comparisons[0] if comparisons else None,
            "consistency": strategy_consistency(strategies),
        }

    def dashboard(self) -> dict[str, Any]:
        sessions = self.store.list_sessions()
        metrics = self.store.get_metrics()
        timeline: list[dict[str, Any]] = []
        evolution: list[dict[str, Any]] = []
        for s in sessions[-20:]:
            timeline.extend(s.get("timeline") or [])
            g = s.get("graph") or {}
            evolution.extend(g.get("evolution") or [])
        best_wins = [
            float((s.get("strategies") or [{}])[0].get("win_probability") or 0)
            for s in sessions[-20:]
            if s.get("strategies")
        ]
        return {
            "widgets": {
                "win_probability": round(sum(best_wins) / max(1, len(best_wins)), 4)
                if best_wins
                else float(metrics.get("avg_win_probability") or 0),
                "next_best_action": (
                    (sessions[-1].get("timeline") or [{}])[-1] if sessions else {"event": "idle"}
                ),
                "negotiation_timeline": timeline[-30:],
                "strategy_evolution": evolution[-30:],
                "session_count": len(sessions),
                "avg_exit_risk": metrics.get("avg_exit_risk") or 0,
                "avg_buy_probability": metrics.get("avg_buy_probability") or 0,
            },
            "strategy_picks": metrics.get("strategy_picks") or {},
            "metrics": metrics,
            "static_tree_forbidden": True,
        }

    def quality_snapshot(self) -> dict[str, Any]:
        return evaluate_quality(self.store)

    def _update_metrics(self, session: dict[str, Any], best: Any) -> None:
        metrics = self.store.get_metrics()
        n = int(metrics.get("sessions") or 0)
        pred = session.get("prediction") or {}
        buy = float(pred.get("buy_probability") or 0)
        exit_r = float(pred.get("exit_risk") or 0)
        conf = float(pred.get("confidence") or 0)
        win = float(best.win_probability) if best else 0.0
        metrics["sessions"] = n + 1
        metrics["avg_buy_probability"] = round(
            ((float(metrics.get("avg_buy_probability") or 0) * n) + buy) / (n + 1), 4
        )
        metrics["avg_exit_risk"] = round(
            ((float(metrics.get("avg_exit_risk") or 0) * n) + exit_r) / (n + 1), 4
        )
        metrics["avg_win_probability"] = round(
            ((float(metrics.get("avg_win_probability") or 0) * n) + win) / (n + 1), 4
        )
        metrics["avg_confidence"] = round(
            ((float(metrics.get("avg_confidence") or 0) * n) + conf) / (n + 1), 4
        )
        picks = dict(metrics.get("strategy_picks") or {})
        if best:
            picks[best.kind] = int(picks.get(best.kind) or 0) + 1
        metrics["strategy_picks"] = picks
        self.store.save_metrics(metrics)


_engine: NegotiationEngine | None = None


def get_negotiation_engine(root: Path | None = None) -> NegotiationEngine:
    global _engine
    if root is not None:
        return NegotiationEngine(store=NegotiationStore(root=root))
    if _engine is None:
        _engine = NegotiationEngine()
    return _engine
