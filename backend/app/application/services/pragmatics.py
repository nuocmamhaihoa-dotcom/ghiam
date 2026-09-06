"""Vietnamese Pragmatics Engine application service.

Wraps VPE 2.0 (`pragmatics.VietnamesePragmaticsEngine`) for scoring, API, and
downstream coaching / root-cause consumers. Evidence-bound: never invents intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pragmatics import VietnamesePragmaticsEngine
from pragmatics.types import INSUFFICIENT


@dataclass(frozen=True, slots=True)
class PragmaticTurnResult:
    turn_index: int
    text: str
    dialect: str
    matched_pattern_id: str | None
    intent_probability: dict[str, float]
    emotion_probability: dict[str, float]
    hidden_meaning: list[str]
    buying_probability: float | None
    exit_risk: float | None
    confidence: float
    evidence_quote: str | None
    context_window: list[int]
    status: str


@dataclass(frozen=True, slots=True)
class PragmaticsResult:
    status: str
    dialect: str
    turns: list[PragmaticTurnResult]
    timeline: list[dict[str, Any]]
    summary: dict[str, Any]
    explanation: str
    intent_evolution: list[dict[str, Any]]
    emotion_evolution: list[dict[str, Any]]


class PragmaticsEngine:
    """Application facade over Vietnamese Pragmatics Engine 2.0."""

    def __init__(self, *, context_radius: int = 5) -> None:
        radius = max(5, min(10, int(context_radius)))
        self._engine = VietnamesePragmaticsEngine(context_radius=radius)

    def analyze_transcript(
        self,
        turns: list[dict[str, Any]],
        *,
        dialect_hint: str | None = None,
    ) -> PragmaticsResult:
        normalized: list[dict[str, Any]] = []
        for turn in turns:
            normalized.append(
                {
                    "speaker": turn.get("speaker") or turn.get("role") or "customer",
                    "text": turn.get("text") or turn.get("utterance") or "",
                    "turn_index": turn.get("turn_index"),
                }
            )
        bundle = self._engine.analyze(normalized, dialect_hint=dialect_hint)
        mapped_turns: list[PragmaticTurnResult] = []
        for row in bundle.turns:
            quote = None
            for ev in row.evidence:
                if isinstance(ev, dict) and ev.get("quote"):
                    quote = str(ev["quote"])
                    break
            if quote is None and row.text:
                quote = row.text
            pattern_id = row.matched_pattern_ids[0] if row.matched_pattern_ids else None
            mapped_turns.append(
                PragmaticTurnResult(
                    turn_index=row.turn_index,
                    text=row.text,
                    dialect=row.dialect,
                    matched_pattern_id=pattern_id,
                    intent_probability=dict(row.intent_probability),
                    emotion_probability=dict(row.emotion_probability),
                    hidden_meaning=list(row.hidden_meaning),
                    buying_probability=row.buying_probability,
                    exit_risk=row.exit_risk,
                    confidence=row.confidence,
                    evidence_quote=quote,
                    context_window=sorted(set(row.context_before + [row.turn_index] + row.context_after)),
                    status=row.status,
                )
            )
        return PragmaticsResult(
            status=bundle.status,
            dialect=bundle.dialect,
            turns=mapped_turns,
            timeline=list(bundle.timeline),
            summary=dict(bundle.summary),
            explanation=bundle.explanation,
            intent_evolution=list(bundle.intent_evolution),
            emotion_evolution=list(bundle.emotion_evolution),
        )

    def to_dict(self, result: PragmaticsResult) -> dict[str, Any]:
        intents: list[str] = []
        objections: list[str] = []
        objection_keys = {
            "soft_rejection",
            "real_refusal",
            "price_concern",
            "trust_concern",
            "fake_agreement",
            "exit_imminent",
            "negotiation",
            "decision_maker_missing",
        }
        for turn in result.turns:
            if turn.status != "ok" or not turn.intent_probability:
                continue
            top = max(turn.intent_probability, key=turn.intent_probability.get)
            if top not in intents:
                intents.append(top)
            if top in objection_keys and top not in objections:
                objections.append(top)
        if result.status == INSUFFICIENT or result.status != "ok":
            intents = intents or []
            objections = objections or []
        return {
            "status": result.status,
            "dialect": result.dialect,
            "explanation": result.explanation,
            "summary": result.summary,
            "timeline": result.timeline,
            "intent_evolution": result.intent_evolution,
            "emotion_evolution": result.emotion_evolution,
            "intents": intents,
            "objections": objections,
            "turns": [
                {
                    "turn_index": turn.turn_index,
                    "text": turn.text,
                    "dialect": turn.dialect,
                    "matched_pattern_id": turn.matched_pattern_id,
                    "intent_probability": turn.intent_probability,
                    "emotion_probability": turn.emotion_probability,
                    "hidden_meaning": turn.hidden_meaning,
                    "buying_probability": turn.buying_probability,
                    "exit_risk": turn.exit_risk,
                    "confidence": turn.confidence,
                    "evidence_quote": turn.evidence_quote,
                    "context_window": turn.context_window,
                    "status": turn.status,
                }
                for turn in result.turns
            ],
        }


# Backwards-compatible alias
PragmaticsEngineV2 = PragmaticsEngine
