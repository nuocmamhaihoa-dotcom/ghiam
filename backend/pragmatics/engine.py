"""Vietnamese Pragmatics Engine 2.0 orchestrator."""

from __future__ import annotations

from typing import Any

from pragmatics.context_memory import ContextMemory
from pragmatics.detectors import (
    BuyingSignalDetector,
    ExitRiskDetector,
    HiddenMeaningDetector,
    PragmaticClassifier,
)
from pragmatics.dialect import detect_dialect
from pragmatics.intent_resolver import IntentResolver
from pragmatics.types import INSUFFICIENT, PragmaticsBundle, TurnAnalysis


def _is_customer(speaker: str) -> bool:
    value = (speaker or "").lower().strip()
    return value in {
        "customer",
        "cust",
        "client",
        "khach",
        "khách",
        "khách hàng",
        "caller",
    }


class VietnamesePragmaticsEngine:
    """VPE 2.0 — context ±5..10 turns, multi-intent probabilities, evidence-bound."""

    def __init__(self, *, context_radius: int = 5) -> None:
        self.memory = ContextMemory(radius=context_radius)
        self.intents = IntentResolver()
        self.classifier = PragmaticClassifier()
        self.hidden = HiddenMeaningDetector()
        self.buying = BuyingSignalDetector()
        self.exit = ExitRiskDetector()

    def analyze(
        self,
        turns: list[dict[str, Any]],
        *,
        dialect_hint: str | None = None,
    ) -> PragmaticsBundle:
        if not turns:
            return PragmaticsBundle(
                status=INSUFFICIENT,
                dialect=dialect_hint or "unknown",
                explanation=f"{INSUFFICIENT}: empty transcript for pragmatics.",
            )

        joined = " ".join(str(t.get("text") or "") for t in turns)
        dialect = detect_dialect(joined, dialect_hint)
        analyses: list[TurnAnalysis] = []
        timeline: list[dict[str, Any]] = []
        intent_evolution: list[dict[str, Any]] = []
        emotion_evolution: list[dict[str, Any]] = []

        for idx, turn in enumerate(turns):
            speaker = str(turn.get("speaker") or turn.get("role") or "customer")
            text = str(turn.get("text") or "").strip()
            if not _is_customer(speaker):
                continue
            window = self.memory.window(turns, idx)
            intents, pattern_ids, evidence, meta = self.intents.match(
                text, dialect=dialect, window=window
            )
            if not intents or meta is None:
                analyses.append(
                    TurnAnalysis(
                        turn_index=idx,
                        text=text,
                        speaker=speaker,
                        dialect=dialect,
                        intent_probability={},
                        emotion_probability={},
                        buying_probability=None,
                        exit_risk=None,
                        confidence=0.0,
                        hidden_meaning=[],
                        evidence=[],
                        context_before=window.before_indices,
                        context_after=window.after_indices,
                        matched_pattern_ids=[],
                        status=INSUFFICIENT,
                    )
                )
                continue

            emotions = self.classifier.emotions(intents)
            buying = self.buying.score(
                text,
                intents=intents,
                prior=float(meta["buying_prior"]),
                window=window,
            )
            exit_risk = self.exit.score(
                text,
                intents=intents,
                prior=float(meta["exit_prior"]),
                window=window,
            )
            hidden = self.hidden.detect(
                intents=intents,
                priors=list(meta["hidden_meanings"]),
                window=window,
            )
            confidence = round(max(intents.values()), 4)
            evidence.append(
                {
                    "type": "context_window",
                    "before_turn_indices": window.before_indices,
                    "after_turn_indices": window.after_indices,
                    "radius": self.memory.radius,
                }
            )
            row = TurnAnalysis(
                turn_index=idx,
                text=text,
                speaker=speaker,
                dialect=dialect,
                intent_probability=intents,
                emotion_probability=emotions,
                buying_probability=buying,
                exit_risk=exit_risk,
                confidence=confidence,
                hidden_meaning=hidden,
                evidence=evidence,
                context_before=window.before_indices,
                context_after=window.after_indices,
                matched_pattern_ids=pattern_ids,
                status="ok",
            )
            analyses.append(row)
            top_intent = max(intents, key=intents.get)
            top_emotion = max(emotions, key=emotions.get) if emotions else None
            timeline.append(
                {
                    "turn_index": idx,
                    "text": text,
                    "top_intent": top_intent,
                    "top_emotion": top_emotion,
                    "buying_probability": buying,
                    "exit_risk": exit_risk,
                    "confidence": confidence,
                    "hidden_meaning": hidden[:2],
                    "evidence_quote": evidence[0]["quote"] if evidence else text,
                }
            )
            intent_evolution.append(
                {"turn_index": idx, "intent": top_intent, "probability": intents[top_intent]}
            )
            if top_emotion:
                emotion_evolution.append(
                    {
                        "turn_index": idx,
                        "emotion": top_emotion,
                        "probability": emotions[top_emotion],
                    }
                )

        matched = [a for a in analyses if a.status == "ok"]
        if not matched:
            return PragmaticsBundle(
                status=INSUFFICIENT,
                dialect=dialect,
                explanation=(
                    f"{INSUFFICIENT}: no pragmatic pattern matched with evidence "
                    "in customer turns."
                ),
                turns=analyses,
            )

        intent_acc: dict[str, float] = {}
        for row in matched:
            for intent, prob in row.intent_probability.items():
                intent_acc[intent] = intent_acc.get(intent, 0.0) + prob
        top_intent = max(intent_acc, key=intent_acc.get)
        avg_buy = sum(r.buying_probability or 0.0 for r in matched) / len(matched)
        avg_exit = sum(r.exit_risk or 0.0 for r in matched) / len(matched)
        avg_conf = sum(r.confidence for r in matched) / len(matched)

        return PragmaticsBundle(
            status="ok",
            dialect=dialect,
            explanation=(
                f"VPE 2.0 resolved {len(matched)}/{len(analyses)} customer turns "
                f"(dialect={dialect}, top_intent={top_intent}, radius=±{self.memory.radius})."
            ),
            turns=analyses,
            timeline=timeline,
            intent_evolution=intent_evolution,
            emotion_evolution=emotion_evolution,
            summary={
                "top_intent": top_intent,
                "avg_buying_probability": round(avg_buy, 4),
                "avg_exit_risk": round(avg_exit, 4),
                "avg_confidence": round(avg_conf, 4),
                "matched_turns": len(matched),
                "context_radius": self.memory.radius,
                "pattern_ids": list(
                    dict.fromkeys(pid for r in matched for pid in r.matched_pattern_ids)
                ),
            },
        )

    # Compatibility aliases used by existing application service / API
    def analyze_transcript(
        self,
        turns: list[dict[str, Any]],
        *,
        dialect_hint: str | None = None,
    ) -> PragmaticsBundle:
        return self.analyze(turns, dialect_hint=dialect_hint)

    def to_dict(self, result: PragmaticsBundle) -> dict[str, Any]:
        return result.to_dict()
