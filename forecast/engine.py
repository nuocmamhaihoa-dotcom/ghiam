"""Sales Forecast — close rate, weekly/monthly/quarterly revenue, pipeline risk."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ForecastResult:
    close_rate: float
    weekly_revenue: float
    monthly_revenue: float
    quarterly_revenue: float
    pipeline_risk: float
    confidence: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    method: str = "weighted-moving-average"
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ForecastEngine:
    def forecast(
        self,
        *,
        historical: list[dict[str, Any]] | None = None,
        pipeline: list[dict[str, Any]] | None = None,
        horizon_days: int = 30,
    ) -> ForecastResult:
        historical = historical or []
        pipeline = pipeline or []
        if not historical and not pipeline:
            return ForecastResult(
                close_rate=0.0,
                weekly_revenue=0.0,
                monthly_revenue=0.0,
                quarterly_revenue=0.0,
                pipeline_risk=1.0,
                confidence=0.0,
                evidence=[{"reason": "no_historical_or_pipeline"}],
                status="Insufficient Evidence",
            )

        rates = [
            float(p.get("conversion_rate") or p.get("close_rate") or 0)
            for p in historical
        ]
        revenues = [float(p.get("revenue") or 0) for p in historical]
        avg_rate = sum(rates) / len(rates) if rates else 0.2
        avg_rev = sum(revenues) / len(revenues) if revenues else 0.0

        if len(rates) >= 6:
            trend = (sum(rates[-3:]) / 3) - (sum(rates[-6:-3]) / 3)
        elif len(rates) >= 2:
            trend = rates[-1] - rates[0]
        else:
            trend = 0.0

        close_rate = max(0.0, min(1.0, avg_rate + trend))

        stage_weights = {
            "new": 0.05,
            "contacted": 0.12,
            "qualified": 0.25,
            "proposal": 0.45,
            "negotiation": 0.65,
            "won": 1.0,
            "lost": 0.0,
        }
        open_value = sum(float(d.get("value") or 0) for d in pipeline)
        weighted = 0.0
        stale = 0
        for deal in pipeline:
            stage = str(deal.get("stage") or "new").lower()
            w = stage_weights.get(stage, 0.15)
            weighted += float(deal.get("value") or 0) * w * close_rate
            if float(deal.get("days_in_stage") or 0) > 21:
                stale += 1

        monthly_from_hist = avg_rev * (1 + trend) * max(horizon_days / 30, 0.1)
        monthly = 0.55 * monthly_from_hist + 0.45 * (
            weighted if pipeline else monthly_from_hist
        )
        weekly = monthly / 4.0
        quarterly = monthly * 3.0

        risk = 0.2
        if pipeline:
            risk += min(0.5, stale / max(len(pipeline), 1))
            if close_rate < 0.15:
                risk += 0.15
            if trend < -0.03:
                risk += 0.1
        risk = max(0.0, min(1.0, risk))
        confidence = min(0.92, 0.35 + 0.04 * len(historical) + 0.03 * len(pipeline))

        return ForecastResult(
            close_rate=round(close_rate, 4),
            weekly_revenue=round(weekly, 2),
            monthly_revenue=round(monthly, 2),
            quarterly_revenue=round(quarterly, 2),
            pipeline_risk=round(risk, 4),
            confidence=round(confidence, 3),
            evidence=[
                {"metric": "avg_close_rate", "value": round(avg_rate, 4)},
                {"metric": "trend", "value": round(trend, 4)},
                {"metric": "pipeline_open_value", "value": open_value},
                {"metric": "stale_deals", "value": stale},
            ],
            status="ok",
        )


__all__ = ["ForecastEngine", "ForecastResult"]
