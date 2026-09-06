"""Enterprise Sales OS dashboard widgets by role."""
from __future__ import annotations

from typing import Any


ROLES = ("CEO", "Sales Director", "Team Leader", "QA", "Telesale")

WIDGETS = (
    "Revenue Forecast",
    "Lead Health",
    "Revenue Leak",
    "Conversion Funnel",
    "Personality Distribution",
    "Coaching Progress",
    "Team Ranking",
    "AI Confidence",
    "Golden Call Gap",
    "Repeat Mistake",
)

ROLE_WIDGETS: dict[str, tuple[str, ...]] = {
    "CEO": WIDGETS,
    "Sales Director": WIDGETS,
    "Team Leader": (
        "Revenue Forecast",
        "Lead Health",
        "Conversion Funnel",
        "Coaching Progress",
        "Team Ranking",
        "AI Confidence",
        "Golden Call Gap",
        "Repeat Mistake",
    ),
    "QA": (
        "Lead Health",
        "AI Confidence",
        "Golden Call Gap",
        "Repeat Mistake",
        "Personality Distribution",
        "Coaching Progress",
    ),
    "Telesale": (
        "Lead Health",
        "Coaching Progress",
        "Golden Call Gap",
        "Repeat Mistake",
        "AI Confidence",
    ),
}


def build_dashboard(
    *,
    role: str,
    forecast: dict[str, Any] | None = None,
    routing_stats: dict[str, Any] | None = None,
    automation_stats: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role_key = role if role in ROLE_WIDGETS else "CEO"
    forecast = forecast or {}
    routing_stats = routing_stats or {}
    automation_stats = automation_stats or {}
    extras = extras or {}

    widgets: dict[str, Any] = {
        "Revenue Forecast": {
            "weekly": forecast.get("weekly_revenue", 0),
            "monthly": forecast.get("monthly_revenue", 0),
            "quarterly": forecast.get("quarterly_revenue", 0),
            "close_rate": forecast.get("close_rate", 0),
            "pipeline_risk": forecast.get("pipeline_risk", 0),
            "confidence": forecast.get("confidence", 0),
        },
        "Lead Health": {
            "avg_lead_score": routing_stats.get(
                "avg_lead_score", extras.get("avg_lead_score", 0)
            ),
            "assigned": routing_stats.get("assigned", 0),
            "unassigned": routing_stats.get("unassigned", 0),
            "stale": extras.get("stale_leads", 0),
        },
        "Revenue Leak": {
            "leak_amount": extras.get("leak_amount", 0),
            "top_causes": extras.get(
                "leak_causes",
                ["missed_callback", "weak_close", "wrong_routing"],
            ),
        },
        "Conversion Funnel": extras.get(
            "funnel",
            {
                "leads": 1000,
                "contacted": 700,
                "qualified": 320,
                "proposal": 180,
                "won": 90,
            },
        ),
        "Personality Distribution": extras.get(
            "personality",
            {
                "consultative": 0.34,
                "assertive": 0.28,
                "empathic": 0.22,
                "analytical": 0.16,
            },
        ),
        "Coaching Progress": {
            "plans_open": extras.get(
                "coaching_open", automation_stats.get("coaching", 0)
            ),
            "completed": extras.get("coaching_done", 0),
            "avg_improvement": extras.get("coaching_delta", 0.08),
        },
        "Team Ranking": extras.get(
            "team_ranking",
            [
                {"agent_id": "A1", "close_rate": 0.31},
                {"agent_id": "A2", "close_rate": 0.27},
                {"agent_id": "A3", "close_rate": 0.24},
            ],
        ),
        "AI Confidence": {
            "routing": routing_stats.get("avg_confidence", 0.78),
            "nba": extras.get("nba_confidence", 0.74),
            "forecast": forecast.get("confidence", 0.7),
        },
        "Golden Call Gap": {
            "gap_score": extras.get("golden_gap", 0.22),
            "top_gaps": extras.get(
                "golden_gaps",
                ["discovery_depth", "value_stack", "trial_close"],
            ),
        },
        "Repeat Mistake": {
            "top_mistakes": extras.get(
                "repeat_mistakes",
                [
                    {"code": "RM-OBJ-01", "count": 12},
                    {"code": "RM-CLOSE-02", "count": 9},
                ],
            ),
        },
    }

    selected = {w: widgets[w] for w in ROLE_WIDGETS[role_key]}
    return {
        "role": role_key,
        "widgets": selected,
        "widget_names": list(ROLE_WIDGETS[role_key]),
        "synced_at": extras.get("synced_at"),
        "status": "ok",
    }


__all__ = ["ROLES", "WIDGETS", "ROLE_WIDGETS", "build_dashboard"]
