"""AI Judge Ensemble — 5 agents, evidence-bound, consensus only when data is sufficient.

Agents (fixed):
1. Evidence AI — find / validate evidence only
2. SOP Judge — score against Rulebook from DB
3. Psychology AI — customer psychology signals
4. Sales Expert AI — sales skill quality
5. Consensus AI — final verdict only when enough data

Never invents conclusions without evidence → Insufficient Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.entities import EvidenceEntity, RuleEntity
from app.domain.enums import Verdict


@dataclass(frozen=True, slots=True)
class AgentVote:
    agent: str
    verdict: Verdict
    confidence: float
    explanation: str
    evidence_span_ids: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnsembleResult:
    verdict: Verdict
    confidence: float
    explanation: str
    path: str
    votes: tuple[AgentVote, ...]
    insufficient_evidence: bool


class JudgePort(Protocol):
    async def adjudicate(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> EnsembleResult: ...


def _avg_conf(evidence: list[EvidenceEntity]) -> float:
    if not evidence:
        return 0.0
    return round(sum(e.confidence for e in evidence) / len(evidence), 3)


def _span_ids(evidence: list[EvidenceEntity]) -> tuple[str, ...]:
    return tuple(getattr(e, "id", None) or getattr(e, "span_id", "") for e in evidence)


class EvidenceAI:
    """Agent 1 — only validates that evidence pack is usable for the rule."""

    name = "Evidence AI"

    def vote(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> AgentVote:
        req = rule.evidence_requirements or {}
        min_spans = int(req.get("min_spans") or 1)
        min_conf = float(req.get("min_confidence") or 0.7)
        require_ts = bool(req.get("require_timestamp", True))

        if not evidence:
            return AgentVote(
                self.name,
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: empty evidence pack.",
            )

        usable = [e for e in evidence if e.confidence >= min_conf]
        if len(usable) < min_spans:
            return AgentVote(
                self.name,
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                f"Insufficient Evidence: need ≥{min_spans} spans with confidence≥{min_conf}.",
                _span_ids(evidence),
            )

        if require_ts:
            missing_ts = [
                e
                for e in usable
                if getattr(e, "audio_ts_start", None) is None
                and getattr(e, "start_ms", None) is None
            ]
            if missing_ts:
                return AgentVote(
                    self.name,
                    Verdict.INSUFFICIENT_EVIDENCE,
                    0.0,
                    "Insufficient Evidence: timestamp required but missing on evidence spans.",
                    _span_ids(usable),
                )

        return AgentVote(
            self.name,
            Verdict.PASS,
            _avg_conf(usable),
            f"Evidence pack OK ({len(usable)} usable spans).",
            _span_ids(usable),
            {"usable_spans": len(usable)},
        )


class SOPJudge:
    """Agent 2 — Rulebook / SOP compliance using evaluator config from DB rule."""

    name = "SOP Judge"

    def vote(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> AgentVote:
        if not evidence:
            return AgentVote(
                self.name,
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: SOP Judge has no spans.",
            )

        cfg = rule.evaluator_config or {}
        keywords = [str(k).lower() for k in (cfg.get("keywords") or [])]
        text = " ".join((e.quote or "").lower() for e in evidence)

        # Slot requirements
        req_slots = list((rule.evidence_requirements or {}).get("slots") or [])
        found_slots = {e.slot for e in evidence if getattr(e, "slot", None)}
        missing_slots = [s for s in req_slots if s not in found_slots]

        if keywords:
            hits = [k for k in keywords if k in text]
            if hits and not missing_slots:
                return AgentVote(
                    self.name,
                    Verdict.PASS,
                    0.82,
                    f"SOP keywords matched: {hits}.",
                    _span_ids(evidence),
                    {"hits": hits},
                )
            if not hits:
                return AgentVote(
                    self.name,
                    Verdict.FAIL,
                    0.75,
                    "SOP keywords not found in evidence quotes.",
                    _span_ids(evidence),
                )

        if missing_slots:
            return AgentVote(
                self.name,
                Verdict.FAIL,
                0.7,
                f"Missing required SOP slots: {missing_slots}.",
                _span_ids(evidence),
            )

        # No keyword config → cannot hardcode business logic; defer with IE-ish low confidence pass of coverage only
        if not keywords and not req_slots:
            return AgentVote(
                self.name,
                Verdict.NOT_APPLICABLE,
                0.0,
                "SOP Judge N/A: rule has no keyword/slot evaluator config in DB.",
                _span_ids(evidence),
            )

        return AgentVote(
            self.name,
            Verdict.PASS,
            0.78,
            "SOP constraints satisfied.",
            _span_ids(evidence),
        )


class PsychologyAI:
    """Agent 3 — customer psychology from labeled emotion/objection evidence slots."""

    name = "Psychology AI"

    _NEG = {"angry", "frustration", "exit_intent", "confused", "hesitation"}
    _POS = {"interested", "curious", "trust"}

    def vote(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> AgentVote:
        if not evidence:
            return AgentVote(
                self.name,
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: no spans for psychology read.",
            )

        emotions: list[str] = []
        for e in evidence:
            meta = getattr(e, "metadata", None) or {}
            if isinstance(meta, dict) and meta.get("emotion"):
                emotions.append(str(meta["emotion"]).lower())
            for lab in getattr(e, "labels", None) or []:
                if str(lab).lower().startswith("emotion:"):
                    emotions.append(str(lab).split(":", 1)[1].lower())
                elif str(lab).lower() in self._NEG | self._POS:
                    emotions.append(str(lab).lower())
            slot = (getattr(e, "slot", None) or "").lower()
            if slot.startswith("emotion:"):
                emotions.append(slot.split(":", 1)[1])

        stage = (rule.category or "").lower()
        psych_stages = {"rapport", "objection", "closing", "discovery"}
        if not emotions and stage not in psych_stages:
            return AgentVote(
                self.name,
                Verdict.NOT_APPLICABLE,
                0.0,
                "Psychology N/A for this rule/stage without emotion labels.",
            )

        if not emotions:
            return AgentVote(
                self.name,
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: psychology stage requires emotion labels on evidence.",
            )

        neg = [e for e in emotions if e in self._NEG]
        pos = [e for e in emotions if e in self._POS]
        if neg and stage in {"closing", "pricing"} and not pos:
            return AgentVote(
                self.name,
                Verdict.FAIL,
                0.7,
                f"Negative customer state during {stage}: {neg}.",
                _span_ids(evidence),
                {"emotions": emotions},
            )
        return AgentVote(
            self.name,
            Verdict.PASS,
            0.74,
            f"Psychology signals readable: {emotions}.",
            _span_ids(evidence),
            {"emotions": emotions},
        )


class SalesExpertAI:
    """Agent 4 — sales skill heuristics bound to evidence (no invented coaching facts)."""

    name = "Sales Expert AI"

    def vote(self, rule: RuleEntity, evidence: list[EvidenceEntity]) -> AgentVote:
        if not evidence:
            return AgentVote(
                self.name,
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: sales expert has no spans.",
            )

        text = " ".join((e.quote or "").lower() for e in evidence)
        stage = (rule.category or "").lower()
        stages = {
            (getattr(e, "stage_key", None) or (getattr(e, "metadata", {}) or {}).get("stage"))
            for e in evidence
        }
        price_tokens = ["giá", "bao nhiêu", "rẻ", "đắt", "ưu đãi", "giảm"]
        if stage in {"discovery", "opening", "rapport"} and any(t in text for t in price_tokens):
            if "discovery" in {str(s).lower() for s in stages if s} or stage == "discovery":
                return AgentVote(
                    self.name,
                    Verdict.FAIL,
                    0.72,
                    "Sales skill issue: price talk appears before value/discovery complete (evidence-bound).",
                    _span_ids(evidence),
                )

        close_tokens = ["chốt", "đăng ký", "ký", "giao", "lịch", "hợp đồng"]
        if stage == "closing" and not any(t in text for t in close_tokens):
            return AgentVote(
                self.name,
                Verdict.FAIL,
                0.7,
                "Closing stage evidence lacks explicit next-step/close ask.",
                _span_ids(evidence),
            )

        return AgentVote(
            self.name,
            Verdict.PASS,
            0.76,
            "Sales skill signals acceptable on provided evidence.",
            _span_ids(evidence),
        )


class ConsensusAI:
    """Agent 5 — final verdict only when enough agent data is present."""

    name = "Consensus AI"

    def decide(
        self,
        votes: list[AgentVote],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> EnsembleResult:
        substantive = [v for v in votes if v.verdict != Verdict.NOT_APPLICABLE]
        ie = [v for v in substantive if v.verdict == Verdict.INSUFFICIENT_EVIDENCE]
        fails = [v for v in substantive if v.verdict == Verdict.FAIL]
        passes = [v for v in substantive if v.verdict == Verdict.PASS]

        # Evidence AI IE always blocks
        evidence_vote = next((v for v in votes if v.agent == EvidenceAI.name), None)
        if evidence_vote and evidence_vote.verdict == Verdict.INSUFFICIENT_EVIDENCE:
            return EnsembleResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                evidence_vote.explanation,
                "consensus.blocked_by_evidence_ai",
                tuple(votes),
                True,
            )

        if candidate_verdict == Verdict.INSUFFICIENT_EVIDENCE:
            return EnsembleResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                candidate_explanation or "Insufficient Evidence from rule engine candidate.",
                "consensus.confirm_candidate_ie",
                tuple(votes),
                True,
            )

        # Need at least Evidence AI + SOP Judge substantive (or N/A SOP with Evidence pass)
        if len(substantive) < 2:
            return EnsembleResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: Consensus AI refuses to conclude with <2 substantive votes.",
                "consensus.too_few_votes",
                tuple(votes),
                True,
            )

        if ie and not passes and not fails:
            return EnsembleResult(
                Verdict.INSUFFICIENT_EVIDENCE,
                0.0,
                "Insufficient Evidence: ensemble agents could not adjudicate.",
                "consensus.all_ie",
                tuple(votes),
                True,
            )

        if len(fails) > len(passes):
            conf = round(sum(v.confidence for v in fails) / len(fails), 3)
            return EnsembleResult(
                Verdict.FAIL,
                conf,
                candidate_explanation or "Consensus: majority FAIL among substantive agents.",
                "consensus.majority_fail",
                tuple(votes),
                False,
            )

        if len(passes) >= max(1, len(fails)):
            conf = round(sum(v.confidence for v in passes) / len(passes), 3) if passes else 0.0
            return EnsembleResult(
                Verdict.PASS,
                conf,
                candidate_explanation or "Consensus: majority PASS among substantive agents.",
                "consensus.majority_pass",
                tuple(votes),
                False,
            )

        return EnsembleResult(
            Verdict.INSUFFICIENT_EVIDENCE,
            0.0,
            "Insufficient Evidence: Consensus AI found no clear majority.",
            "consensus.no_majority",
            tuple(votes),
            True,
        )


class AIJudgeEnsemble:
    """Orchestrates the 5-agent ensemble."""

    def __init__(self) -> None:
        self.evidence_ai = EvidenceAI()
        self.sop_judge = SOPJudge()
        self.psychology_ai = PsychologyAI()
        self.sales_expert_ai = SalesExpertAI()
        self.consensus_ai = ConsensusAI()

    async def adjudicate(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> EnsembleResult:
        votes = [
            self.evidence_ai.vote(rule, evidence),
            self.sop_judge.vote(rule, evidence),
            self.psychology_ai.vote(rule, evidence),
            self.sales_expert_ai.vote(rule, evidence),
        ]
        return self.consensus_ai.decide(
            votes,
            candidate_verdict=candidate_verdict,
            candidate_explanation=candidate_explanation,
        )


# Backward-compatible aliases used by RuleEngine / Scoring DI
DeterministicJudgeEnsemble = AIJudgeEnsemble
JudgeResult = EnsembleResult  # historical name
JudgePort = JudgePort  # RuleEngine import name


class PassthroughLlmJudge:
    """LLM gateway stub — always falls back to 5-agent ensemble without inventing."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._ensemble = AIJudgeEnsemble()

    async def adjudicate(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        candidate_verdict: Verdict,
        candidate_explanation: str,
    ) -> EnsembleResult:
        # Even if LLM "enabled", never invent without evidence; ensemble remains source of truth here.
        return await self._ensemble.adjudicate(
            rule,
            evidence,
            candidate_verdict=candidate_verdict,
            candidate_explanation=candidate_explanation,
        )
