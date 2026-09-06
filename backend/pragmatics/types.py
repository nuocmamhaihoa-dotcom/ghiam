"""Shared types for Vietnamese Pragmatics Engine 2.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


INSUFFICIENT = "Insufficient Evidence"

DIALECTS = ("north", "central", "south")

CORE_INTENTS = (
    "delay",
    "soft_rejection",
    "real_refusal",
    "soft_agreement",
    "fake_agreement",
    "price_concern",
    "trust_concern",
    "need_information",
    "decision_maker_missing",
    "busy",
    "interested",
    "ready_to_buy",
    "topic_shift",
    "exit_imminent",
    "negotiation",
)


@dataclass(slots=True)
class TurnAnalysis:
    turn_index: int
    text: str
    speaker: str
    dialect: str
    intent_probability: dict[str, float]
    emotion_probability: dict[str, float]
    buying_probability: float | None
    exit_risk: float | None
    confidence: float
    hidden_meaning: list[str]
    evidence: list[dict[str, Any]]
    context_before: list[int]
    context_after: list[int]
    matched_pattern_ids: list[str]
    status: str


@dataclass(slots=True)
class PragmaticsBundle:
    status: str
    dialect: str
    explanation: str
    turns: list[TurnAnalysis] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    intent_evolution: list[dict[str, Any]] = field(default_factory=list)
    emotion_evolution: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dialect": self.dialect,
            "explanation": self.explanation,
            "summary": self.summary,
            "timeline": self.timeline,
            "intent_evolution": self.intent_evolution,
            "emotion_evolution": self.emotion_evolution,
            "turns": [
                {
                    "turn_index": t.turn_index,
                    "text": t.text,
                    "speaker": t.speaker,
                    "dialect": t.dialect,
                    "intent_probability": t.intent_probability,
                    "emotion_probability": t.emotion_probability,
                    "buying_probability": t.buying_probability,
                    "exit_risk": t.exit_risk,
                    "confidence_score": t.confidence,
                    "confidence": t.confidence,
                    "hidden_meaning": t.hidden_meaning,
                    "evidence": t.evidence,
                    "context_before": t.context_before,
                    "context_after": t.context_after,
                    "matched_pattern_ids": t.matched_pattern_ids,
                    "status": t.status,
                }
                for t in self.turns
            ],
        }
