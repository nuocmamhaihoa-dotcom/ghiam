"""Deterministic Judge Ensemble.

When LLM providers are unavailable, this ensemble adjudicates using evidence only.
Never invents scores without evidence — returns Insufficient Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.entities import EvidenceEntity, RuleEntity
from app.domain.enums import Verdict


@dataclass(frozen=True, slots=True)
class JudgeResult:
    verdict: Verdict
    confidence: float
    explanation: str
    path: str


class JudgePort(Protocol):
    async def adjudicate(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> JudgeResult: ...


class DeterministicJudgeEnsemble:
    """Multi-judge consensus over keyword / slot / regex signals — no LLM required."""

    async def adjudicate(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> JudgeResult:
        if not evidence:
            return JudgeResult(
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                explanation="Insufficient Evidence: empty evidence pack for judge.",
                path="judge_ensemble.ie_empty",
            )

        votes: list[JudgeResult] = [
            self._judge_keyword(rule, evidence),
            self._judge_slot(rule, evidence),
            self._judge_coverage(rule, evidence),
        ]

        # Consensus: if any judge says IE and none says pass firmly → IE
        ie_votes = sum(1 for v in votes if v.verdict == Verdict.INSUFFICIENT_EVIDENCE)
        fail_votes = sum(1 for v in votes if v.verdict == Verdict.FAIL)
        pass_votes = sum(1 for v in votes if v.verdict == Verdict.PASS)

        if ie_votes >= 2:
            return JudgeResult(
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                explanation="Insufficient Evidence: judge ensemble consensus.",
                path="judge_ensemble.consensus_ie",
            )

        if candidate_verdict == Verdict.INSUFFICIENT_EVIDENCE:
            return JudgeResult(
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                explanation=candidate_explanation,
                path="judge_ensemble.confirm_ie",
            )

        if fail_votes > pass_votes:
            avg_conf = sum(e.confidence for e in evidence) / len(evidence)
            return JudgeResult(
                verdict=Verdict.FAIL,
                confidence=round(avg_conf, 3),
                explanation=candidate_explanation or "Judge ensemble majority fail.",
                path="judge_ensemble.consensus_fail",
            )

        if pass_votes >= 2:
            avg_conf = sum(e.confidence for e in evidence) / len(evidence)
            return JudgeResult(
                verdict=Verdict.PASS,
                confidence=round(avg_conf, 3),
                explanation=candidate_explanation or "Judge ensemble majority pass.",
                path="judge_ensemble.consensus_pass",
            )

        return JudgeResult(
            verdict=candidate_verdict,
            confidence=round(sum(e.confidence for e in evidence) / len(evidence), 3),
            explanation=candidate_explanation,
            path="judge_ensemble.defer_rule_engine",
        )

    def _judge_keyword(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> JudgeResult:
        keywords = list(rule.evaluator_config.get("keywords") or [])
        if not keywords:
            return JudgeResult(
                Verdict.NOT_APPLICABLE, 0.0, "No keywords configured.", "judge.keyword.na"
            )
        text = " ".join(e.quote.lower() for e in evidence)
        hits = [k for k in keywords if str(k).lower() in text]
        if hits:
            return JudgeResult(
                Verdict.PASS, 0.8, f"Keyword hits: {hits}", "judge.keyword.pass"
            )
        return JudgeResult(
            Verdict.FAIL, 0.7, "Required keywords not found in evidence.", "judge.keyword.fail"
        )

    def _judge_slot(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> JudgeResult:
        req_slots = list(rule.evidence_requirements.get("slots") or [])
        if not req_slots:
            return JudgeResult(
                Verdict.NOT_APPLICABLE, 0.0, "No slots required.", "judge.slot.na"
            )
        found = {e.slot for e in evidence if e.slot}
        missing = [s for s in req_slots if s not in found]
        if missing:
            # Missing required slot with evidence present for other things → fail or IE
            if evidence:
                return JudgeResult(
                    Verdict.FAIL,
                    0.6,
                    f"Missing required slots: {missing}",
                    "judge.slot.fail",
                )
            return JudgeResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                f"Insufficient Evidence for slots: {missing}",
                "judge.slot.ie",
            )
        return JudgeResult(Verdict.PASS, 0.85, "Required slots present.", "judge.slot.pass")

    def _judge_coverage(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> JudgeResult:
        min_conf = float(rule.evidence_requirements.get("min_confidence") or 0.7)
        high = [e for e in evidence if e.confidence >= min_conf]
        if not high:
            return JudgeResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                f"Insufficient Evidence: no spans >= {min_conf} confidence.",
                "judge.coverage.ie",
            )
        return JudgeResult(
            Verdict.PASS, 0.75, "Evidence confidence coverage OK.", "judge.coverage.pass"
        )


class PassthroughLlmJudge:
    """Placeholder LLM judge — always defers to deterministic ensemble when no API key."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    async def adjudicate(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> JudgeResult:
        if not self._enabled:
            ensemble = DeterministicJudgeEnsemble()
            return await ensemble.adjudicate(
                rule,
                evidence,
                candidate_verdict=candidate_verdict,
                candidate_explanation=candidate_explanation,
            )
        # Even if "enabled" without a real client, never invent — IE when empty
        if not evidence:
            return JudgeResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: LLM judge refused empty evidence.",
                "llm_judge.ie_empty",
            )
        ensemble = DeterministicJudgeEnsemble()
        return await ensemble.adjudicate(
            rule,
            evidence,
            candidate_verdict=candidate_verdict,
            candidate_explanation=candidate_explanation,
        )
