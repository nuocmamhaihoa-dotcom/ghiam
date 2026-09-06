"""Customer Lifetime Value prediction engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cltv.quality import evaluate_quality
from cltv.scorer import prioritize_leads, score_cltv
from cltv.store import CLTVStore
from cltv.types import CLTVPrediction, new_id


class CLTVEngine:
    def __init__(self, store: CLTVStore | None = None) -> None:
        self.store = store or CLTVStore()

    def predict(
        self,
        *,
        lead_id: str | None = None,
        call_history: list[dict[str, Any]] | None = None,
        conversation_dna: dict[str, Any] | None = None,
        intent: str | dict[str, Any] | None = None,
        emotion: str | dict[str, Any] | None = None,
        buying_signal: float | dict[str, Any] | None = None,
        crm: dict[str, Any] | None = None,
        follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lead = lead_id or new_id("lead")
        scores = score_cltv(
            lead_id=lead,
            call_history=call_history,
            conversation_dna=conversation_dna,
            intent=intent,
            emotion=emotion,
            buying_signal=buying_signal,
            crm=crm,
            follow_up=follow_up,
        )
        inputs_used = {
            "call_history": call_history or [],
            "conversation_dna": conversation_dna or {},
            "intent": intent,
            "emotion": emotion,
            "buying_signal": buying_signal,
            "crm": crm or {},
            "follow_up": follow_up or {},
        }
        prediction = CLTVPrediction(
            prediction_id=new_id("cltv"),
            lead_id=lead,
            scores=scores.to_dict(),
            inputs_used=inputs_used,
        )
        payload = prediction.to_dict()
        self.store.append_prediction(payload)
        self._update_metrics(payload)
        return {
            "ok": True,
            "prediction": payload,
            "scores": scores.to_dict(),
            "priority": scores.priority,
            "lifetime_value": scores.lifetime_value,
            "churn_risk": scores.churn_risk,
        }

    def prioritize(self, leads: list[dict[str, Any]]) -> dict[str, Any]:
        predictions: list[dict[str, Any]] = []
        for lead in leads:
            out = self.predict(
                lead_id=str(lead.get("lead_id") or lead.get("id") or new_id("lead")),
                call_history=lead.get("call_history") or lead.get("calls") or [],
                conversation_dna=lead.get("conversation_dna") or lead.get("dna") or {},
                intent=lead.get("intent"),
                emotion=lead.get("emotion"),
                buying_signal=lead.get("buying_signal"),
                crm=lead.get("crm") or {},
                follow_up=lead.get("follow_up") or lead.get("followup") or {},
            )
            predictions.append(out["prediction"])
        ranked = prioritize_leads(predictions)
        return {
            "ok": True,
            "ranked": ranked,
            "counts": {
                "high_value": sum(1 for p in ranked if (p.get("scores") or {}).get("priority") == "high_value"),
                "medium": sum(1 for p in ranked if (p.get("scores") or {}).get("priority") == "medium"),
                "low": sum(1 for p in ranked if (p.get("scores") or {}).get("priority") == "low"),
            },
        }

    def dashboard(self) -> dict[str, Any]:
        rows = self.store.list_predictions()
        metrics = self.store.get_metrics()
        recent = rows[-50:]
        avg_cltv = (
            sum(float((r.get("scores") or {}).get("cltv_score") or 0) for r in recent) / max(1, len(recent))
            if recent
            else float(metrics.get("avg_cltv_score") or 0)
        )
        avg_ltv = (
            sum(float((r.get("scores") or {}).get("lifetime_value") or 0) for r in recent) / max(1, len(recent))
            if recent
            else float(metrics.get("avg_lifetime_value") or 0)
        )
        avg_churn = (
            sum(float((r.get("scores") or {}).get("churn_risk") or 0) for r in recent) / max(1, len(recent))
            if recent
            else float(metrics.get("avg_churn_risk") or 0)
        )
        upsell_opps = [
            {
                "lead_id": r.get("lead_id"),
                "upsell_score": (r.get("scores") or {}).get("upsell_score"),
                "lifetime_value": (r.get("scores") or {}).get("lifetime_value"),
            }
            for r in recent
            if float((r.get("scores") or {}).get("upsell_score") or 0) >= 0.6
        ][:10]
        referral_opps = [
            {
                "lead_id": r.get("lead_id"),
                "referral_score": (r.get("scores") or {}).get("referral_score"),
                "referral_probability": (r.get("scores") or {}).get("referral_probability"),
            }
            for r in recent
            if float((r.get("scores") or {}).get("referral_score") or 0) >= 0.55
        ][:10]
        return {
            "widgets": {
                "cltv_forecast": round(avg_cltv, 4),
                "churn_forecast": round(avg_churn, 4),
                "avg_lifetime_value": round(avg_ltv, 2),
                "upsell_opportunity": upsell_opps,
                "referral_opportunity": referral_opps,
                "prediction_count": len(rows),
                "priority_counts": metrics.get("priority_counts") or {},
            },
            "metrics": metrics,
            "recent": recent[-20:],
        }

    def quality_snapshot(self) -> dict[str, Any]:
        return evaluate_quality(self.store)

    def _update_metrics(self, prediction: dict[str, Any]) -> None:
        metrics = self.store.get_metrics()
        n = int(metrics.get("predictions") or 0)
        scores = prediction.get("scores") or {}
        cltv = float(scores.get("cltv_score") or 0)
        ltv = float(scores.get("lifetime_value") or 0)
        churn = float(scores.get("churn_risk") or 0)
        conf = float(scores.get("confidence") or 0)
        metrics["predictions"] = n + 1
        metrics["avg_cltv_score"] = round(((float(metrics.get("avg_cltv_score") or 0) * n) + cltv) / (n + 1), 4)
        metrics["avg_lifetime_value"] = round(((float(metrics.get("avg_lifetime_value") or 0) * n) + ltv) / (n + 1), 2)
        metrics["avg_churn_risk"] = round(((float(metrics.get("avg_churn_risk") or 0) * n) + churn) / (n + 1), 4)
        metrics["avg_confidence"] = round(((float(metrics.get("avg_confidence") or 0) * n) + conf) / (n + 1), 4)
        counts = dict(metrics.get("priority_counts") or {})
        pri = str(scores.get("priority") or "low")
        counts[pri] = int(counts.get(pri) or 0) + 1
        metrics["priority_counts"] = counts
        self.store.save_metrics(metrics)


_engine: CLTVEngine | None = None


def get_cltv_engine(root: Path | None = None) -> CLTVEngine:
    global _engine
    if root is not None:
        return CLTVEngine(store=CLTVStore(root=root))
    if _engine is None:
        _engine = CLTVEngine()
    return _engine
