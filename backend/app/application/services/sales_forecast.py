"""Sales Forecast — KPI forecast from conversion and leak signals."""

from __future__ import annotations

from typing import Any


class SalesForecastService:
    def forecast(
        self,
        *,
        historical: list[dict[str, Any]],
        horizon_days: int = 30,
    ) -> dict[str, Any]:
        if not historical:
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: no historical KPI points for forecast.",
            }
        rates = [float(p.get("conversion_rate") or 0) for p in historical]
        revenues = [float(p.get("revenue") or 0) for p in historical]
        avg_rate = sum(rates) / len(rates)
        avg_rev = sum(revenues) / len(revenues)
        # Simple trend: last-3 vs prior-3 when available
        if len(rates) >= 6:
            recent = sum(rates[-3:]) / 3
            prior = sum(rates[-6:-3]) / 3
            trend = recent - prior
        else:
            trend = 0.0
        projected_rate = max(0.0, min(1.0, avg_rate + trend))
        projected_revenue = avg_rev * (1 + trend) * max(horizon_days / 30, 0.1)
        return {
            "status": "ok",
            "horizon_days": horizon_days,
            "projected_conversion_rate": round(projected_rate, 4),
            "projected_revenue": round(projected_revenue, 2),
            "trend": round(trend, 4),
            "basis_points": len(historical),
            "method": "moving-average-with-short-trend",
            "confidence": round(min(0.9, 0.4 + 0.05 * len(historical)), 3),
        }
