"""Rule Engine — evaluates DB-loaded rules against evidence."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.application.services.judge_ensemble import JudgePort
from app.domain.entities import CallEntity, EvidenceEntity, RuleEntity
from app.domain.enums import Verdict
from app.domain.value_objects import EvidenceSpanRef, ScoreItemResult


class RuleEngine:
    def __init__(self, judge: JudgePort) -> None:
        self._judge = judge

    async def evaluate_all(
        self,
        *,
        rules: list[RuleEntity],
        evidence: list[EvidenceEntity],
        call: CallEntity,
        transcript_avg_confidence: float,
        stt_min_confidence: float,
    ) -> list[ScoreItemResult]:
        if not rules:
            return []

        results: list[ScoreItemResult] = []
        for rule in rules:
            if not self._is_applicable(rule, call):
                results.append(
                    self._item(
                        rule,
                        Verdict.NOT_APPLICABLE,
                        score=None,
                        confidence=0.0,
                        explanation="Rule not applicable for this call context.",
                        spans=[],
                        path=["rule_engine.applicability.na"],
                    )
                )
                continue

            item = await self._evaluate_one(
                rule,
                evidence,
                transcript_avg_confidence=transcript_avg_confidence,
                stt_min_confidence=stt_min_confidence,
            )
            results.append(item)
        return results

    def _is_applicable(self, rule: RuleEntity, call: CallEntity) -> bool:
        cfg = rule.evaluator_config.get("applicability") or {}
        directions = cfg.get("directions")
        if directions and call.direction.value not in directions:
            return False
        campaign_codes = cfg.get("campaign_codes")
        if campaign_codes and call.campaign_code not in campaign_codes:
            return False
        require_keys = cfg.get("require_metadata_keys") or []
        for key in require_keys:
            if key not in (call.metadata or {}):
                return False
        return True

    async def _evaluate_one(
        self,
        rule: RuleEntity,
        evidence: list[EvidenceEntity],
        *,
        transcript_avg_confidence: float,
        stt_min_confidence: float,
    ) -> ScoreItemResult:
        req = rule.evidence_requirements or {}
        min_spans = int(req.get("min_spans") or 1)
        max_spans = int(req.get("max_spans") or 5)
        speakers = set(req.get("speakers_allowed") or [])
        stage_keys = set(req.get("stage_keys") or [])
        slots = set(req.get("slots") or [])
        min_conf = float(req.get("min_confidence") or 0.7)

        if transcript_avg_confidence < stt_min_confidence and rule.required:
            return self._item(
                rule,
                Verdict.INSUFFICIENT_EVIDENCE,
                score=None,
                confidence=0.0,
                explanation=(
                    f"Insufficient Evidence: STT avg_confidence "
                    f"{transcript_avg_confidence:.2f} < {stt_min_confidence}."
                ),
                spans=[],
                path=["rule_engine.preflight.stt_ie"],
            )

        candidates = list(evidence)
        if speakers:
            candidates = [e for e in candidates if e.speaker in speakers]
        if stage_keys:
            candidates = [e for e in candidates if e.stage_key in stage_keys]
        if slots:
            candidates = [e for e in candidates if e.slot in slots]
        candidates = [e for e in candidates if e.confidence >= min_conf]
        candidates = sorted(candidates, key=lambda e: e.confidence, reverse=True)[:max_spans]

        if len(candidates) < min_spans:
            return self._item(
                rule,
                Verdict.INSUFFICIENT_EVIDENCE,
                score=None,
                confidence=0.0,
                explanation=(
                    f"Insufficient Evidence: need >= {min_spans} spans matching "
                    f"requirements; found {len(candidates)}."
                ),
                spans=[],
                path=["rule_engine.evidence.ie"],
            )

        candidate_verdict, explanation, path = self._run_evaluator(rule, candidates)
        judged = await self._judge.adjudicate(
            rule,
            candidates,
            candidate_verdict=candidate_verdict,
            candidate_explanation=explanation,
        )
        final_verdict = judged.verdict
        score: float | None
        if final_verdict == Verdict.PASS:
            score = 1.0
        elif final_verdict == Verdict.FAIL:
            score = 0.0
        else:
            score = None

        return self._item(
            rule,
            final_verdict,
            score=score,
            confidence=judged.confidence,
            explanation=judged.explanation,
            spans=candidates,
            path=[*path, judged.path],
        )

    def _run_evaluator(
        self, rule: RuleEntity, evidence: list[EvidenceEntity]
    ) -> tuple[Verdict, str, list[str]]:
        etype = rule.evaluator_type
        text = " ".join(e.quote for e in evidence)

        if etype in {"keyword", "span_classifier"}:
            keywords = list(rule.evaluator_config.get("keywords") or [])
            positive_labels = set(rule.evaluator_config.get("positive_labels") or [])
            if keywords:
                hits = [k for k in keywords if str(k).lower() in text.lower()]
                if hits:
                    return (
                        Verdict.PASS,
                        f"Matched keywords: {hits}",
                        ["rule_engine.keyword"],
                    )
                return (
                    Verdict.FAIL,
                    "Keywords not found in bound evidence.",
                    ["rule_engine.keyword"],
                )
            if positive_labels:
                found_labels = {lab for e in evidence for lab in e.labels}
                if positive_labels & found_labels:
                    return (
                        Verdict.PASS,
                        f"Positive labels present: {sorted(positive_labels & found_labels)}",
                        ["rule_engine.span_classifier"],
                    )
                return (
                    Verdict.FAIL,
                    "Required positive labels missing on evidence.",
                    ["rule_engine.span_classifier"],
                )
            # Evidence present but no classifier config → pass on presence
            return (
                Verdict.PASS,
                "Evidence spans satisfy requirements.",
                ["rule_engine.presence"],
            )

        if etype == "regex":
            pattern = str(rule.evaluator_config.get("pattern") or "")
            if not pattern:
                return (
                    Verdict.INSUFFICIENT_EVIDENCE,
                    "Insufficient Evidence: regex pattern missing in rule config.",
                    ["rule_engine.regex.ie"],
                )
            if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
                return Verdict.PASS, "Regex matched evidence text.", ["rule_engine.regex"]
            return Verdict.FAIL, "Regex did not match evidence text.", ["rule_engine.regex"]

        if etype == "absence":
            # Absence of forbidden patterns = pass only with enough evidence coverage
            forbidden = list(rule.evaluator_config.get("forbidden_keywords") or [])
            hits = [k for k in forbidden if str(k).lower() in text.lower()]
            if hits:
                return (
                    Verdict.FAIL,
                    f"Forbidden language detected: {hits}",
                    ["rule_engine.absence"],
                )
            return (
                Verdict.PASS,
                "No forbidden language in bound evidence.",
                ["rule_engine.absence"],
            )

        # Unknown evaluator with evidence → do not invent; IE
        return (
            Verdict.INSUFFICIENT_EVIDENCE,
            f"Insufficient Evidence: unsupported evaluator_type '{etype}'.",
            ["rule_engine.unsupported"],
        )

    def _item(
        self,
        rule: RuleEntity,
        verdict: Verdict,
        *,
        score: float | None,
        confidence: float,
        explanation: str,
        spans: list[EvidenceEntity],
        path: list[str],
    ) -> ScoreItemResult:
        return ScoreItemResult(
            rule_code=rule.rule_code,
            title=rule.title,
            verdict=verdict,
            score=score,
            weight=rule.weight,
            confidence=confidence,
            evaluated_at=datetime.now(UTC),
            explanation=explanation,
            evidence_spans=[
                EvidenceSpanRef(
                    id=e.id,
                    quote=e.quote,
                    audio_ts_start=e.audio_ts_start,
                    audio_ts_end=e.audio_ts_end,
                    turn_index=e.turn_index,
                    confidence=e.confidence,
                    stage_key=e.stage_key,
                    slot=e.slot,
                    speaker=e.speaker,
                )
                for e in spans
            ],
            scoring_path=path,
            category=rule.category,
            severity=rule.severity.value,
            auto_fail=rule.auto_fail,
        )
