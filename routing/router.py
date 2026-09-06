"""AI Lead Router — skill, DNA, close rate, industry, peak hours."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass(slots=True)
class RoutingDecision:
    lead_id: str
    agent_id: str
    lead_score: float
    assignment_reason: str
    success_probability: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LeadRouter:
    WEIGHTS = {
        "telesale_skill": 0.25,
        "conversation_dna": 0.20,
        "close_rate": 0.25,
        "industry_experience": 0.15,
        "peak_hours": 0.10,
        "workload": 0.05,
    }

    def score_lead(self, lead: dict[str, Any]) -> float:
        urgency = _clamp(float(lead.get("urgency") or 0.5))
        value = float(lead.get("value") or lead.get("lead_value") or 0)
        base = 40.0 + urgency * 30.0 + min(value / 100_000_000, 1.0) * 20.0
        if str(lead.get("source") or "") in {"facebook_lead_ads", "zalo_oa", "hubspot"}:
            base += 5.0
        return round(_clamp(base / 100.0) * 100.0, 2)

    def route(
        self,
        lead: dict[str, Any],
        agents: list[dict[str, Any]],
        *,
        hour: int | None = None,
    ) -> RoutingDecision:
        active = [a for a in agents if a.get("active", True)]
        if not active:
            raise ValueError("no active agents for routing")

        lead_industry = str(lead.get("industry") or "general").lower()
        dna_pref = str(
            lead.get("dna_preference") or lead.get("conversation_dna") or "consultative"
        ).lower()
        preferred_hour = hour if hour is not None else lead.get("preferred_hour")

        ranked: list[tuple[float, dict, list, dict]] = []
        for agent in active:
            scores: dict[str, float] = {}
            evidence: list[dict] = []

            skill = _clamp(float(agent.get("telesale_skill") or agent.get("skill") or 0.5))
            scores["telesale_skill"] = skill
            evidence.append({"factor": "telesale_skill", "value": skill})

            agent_dna = str(
                agent.get("conversation_dna") or agent.get("dna") or "consultative"
            ).lower()
            dna_match = 1.0 if agent_dna == dna_pref else 0.45
            scores["conversation_dna"] = dna_match
            evidence.append({"factor": "conversation_dna", "match": dna_match})

            close = _clamp(float(agent.get("close_rate") or 0.2))
            scores["close_rate"] = close
            evidence.append({"factor": "close_rate", "value": close})

            industries = {str(i).lower() for i in (agent.get("industry_experience") or [])}
            industry = 1.0 if lead_industry in industries else 0.35
            scores["industry_experience"] = industry
            evidence.append({"factor": "industry_experience", "match": industry})

            peaks = agent.get("peak_hours") or list(range(9, 18))
            peak = 1.0 if preferred_hour is None or int(preferred_hour) in peaks else 0.4
            scores["peak_hours"] = peak
            evidence.append({"factor": "peak_hours", "match": peak})

            workload = _clamp(1.0 - min(int(agent.get("workload") or 0), 20) / 20.0)
            scores["workload"] = workload

            fit = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
            ranked.append((fit, agent, evidence, scores))

        ranked.sort(key=lambda x: x[0], reverse=True)
        best_fit, best, evidence, scores = ranked[0]
        lead_score = self.score_lead(lead)
        success_probability = round(
            _clamp(0.35 * (lead_score / 100.0) + 0.65 * best_fit), 4
        )
        agent_id = str(best.get("agent_id") or best.get("id"))
        name = best.get("name") or agent_id
        reason = (
            f"Assigned to {name} ({agent_id}): "
            f"skill={scores['telesale_skill']:.2f}, dna_match={scores['conversation_dna']:.2f}, "
            f"close_rate={scores['close_rate']:.2f}, industry={scores['industry_experience']:.2f}, "
            f"peak={scores['peak_hours']:.2f}"
        )
        alternatives = [
            {
                "agent_id": str(a.get("agent_id") or a.get("id")),
                "name": a.get("name"),
                "fit": round(f, 4),
            }
            for f, a, _, _ in ranked[1:4]
        ]
        return RoutingDecision(
            lead_id=str(lead.get("lead_id") or lead.get("id") or "unknown"),
            agent_id=agent_id,
            lead_score=lead_score,
            assignment_reason=reason,
            success_probability=success_probability,
            evidence=evidence,
            scores={k: round(v, 4) for k, v in scores.items()},
            alternatives=alternatives,
        )


__all__ = ["LeadRouter", "RoutingDecision"]
