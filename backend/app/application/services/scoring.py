"""Scoring orchestration service."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from app.application.services.coaching import CoachingService
from app.application.services.judge_ensemble import (
    DeterministicJudgeEnsemble,
    PassthroughLlmJudge,
)
from app.application.services.revenue_leak import RevenueLeakService
from app.application.services.root_cause import RootCauseService
from app.application.services.rule_engine import RuleEngine
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.enums import CallStatus, ScoreResult, Verdict
from app.domain.value_objects import ScoringResponse, insufficient_evidence_response
from app.infrastructure.repositories.audit import SqlAlchemyAuditRepository
from app.infrastructure.repositories.call import SqlAlchemyCallRepository
from app.infrastructure.repositories.coaching import SqlAlchemyCoachingRepository
from app.infrastructure.repositories.evidence import SqlAlchemyEvidenceRepository
from app.infrastructure.repositories.revenue import SqlAlchemyRevenueLeakRepository
from app.infrastructure.repositories.root_cause import SqlAlchemyRootCauseRepository
from app.infrastructure.repositories.rule import SqlAlchemyRuleRepository
from app.infrastructure.repositories.score import SqlAlchemyScoreRepository

logger = get_logger(__name__)


class ScoringService:
    def __init__(
        self,
        *,
        calls: SqlAlchemyCallRepository,
        rules: SqlAlchemyRuleRepository,
        evidence: SqlAlchemyEvidenceRepository,
        scores: SqlAlchemyScoreRepository,
        root_causes: SqlAlchemyRootCauseRepository,
        coaching: SqlAlchemyCoachingRepository,
        revenue: SqlAlchemyRevenueLeakRepository,
        audit: SqlAlchemyAuditRepository,
    ) -> None:
        settings = get_settings()
        judge = PassthroughLlmJudge(enabled=settings.llm_judge_enabled)
        # Always have deterministic ensemble available
        self._ensemble = DeterministicJudgeEnsemble()
        self._engine = RuleEngine(judge=judge)
        self._root_cause = RootCauseService()
        self._coaching = CoachingService()
        self._revenue = RevenueLeakService()
        self._calls = calls
        self._rules = rules
        self._evidence = evidence
        self._scores = scores
        self._root_causes = root_causes
        self._coaching_repo = coaching
        self._revenue_repo = revenue
        self._audit = audit
        self._stt_min = settings.stt_min_avg_confidence

    async def score_call(
        self,
        call_id: UUID,
        *,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> ScoringResponse:
        call = await self._calls.get(call_id)
        if call is None:
            raise ValueError("Call not found")

        await self._calls.update_status(call_id, CallStatus.PROCESSING.value)

        evidence = await self._evidence.list_for_call(call_id)
        transcript = await self._calls.get_transcript(call_id)
        rules = await self._rules.list_active()

        if not evidence:
            response = insufficient_evidence_response(
                explanation="Insufficient Evidence: no evidence spans for this call."
            )
            await self._persist(call_id, call.agent_user_id, response, actor_user_id, request_id)
            await self._calls.update_status(
                call_id, CallStatus.INSUFFICIENT_EVIDENCE.value
            )
            return response

        if not rules:
            response = insufficient_evidence_response(
                explanation="Insufficient Evidence: no active rules in database."
            )
            await self._persist(call_id, call.agent_user_id, response, actor_user_id, request_id)
            await self._calls.update_status(
                call_id, CallStatus.INSUFFICIENT_EVIDENCE.value
            )
            return response

        avg_conf = transcript.avg_confidence if transcript else 0.0
        if transcript is None or transcript.turn_count == 0:
            # Evidence without transcript turns still allowed if evidence exists,
            # but STT gate uses 0 → IE for required rules handled inside engine.
            avg_conf = min((e.confidence for e in evidence), default=0.0)

        items = await self._engine.evaluate_all(
            rules=rules,
            evidence=evidence,
            call=call,
            transcript_avg_confidence=avg_conf,
            stt_min_confidence=self._stt_min,
        )

        rules_by_code = {r.rule_code: r for r in rules}
        aggregated = self._aggregate(items)
        root_cause = self._root_cause.analyze(items, rules_by_code)
        coaching = self._coaching.generate_call_tips(items, rules_by_code, root_cause)
        revenue_leak = self._revenue.estimate(
            call=call, items=items, rules_by_code=rules_by_code
        )

        evidence_payload = [
            {
                "id": str(e.id),
                "quote": e.quote,
                "speaker": e.speaker,
                "stage_key": e.stage_key,
                "slot": e.slot,
                "confidence": e.confidence,
                "audio_ts_start": e.audio_ts_start,
                "audio_ts_end": e.audio_ts_end,
                "turn_index": e.turn_index,
            }
            for e in evidence
        ]

        violations = [
            {
                "rule_code": i.rule_code,
                "title": i.title,
                "severity": i.severity,
                "verdict": i.verdict.value,
                "explanation": i.explanation,
                "weight": i.weight,
                "auto_fail": i.auto_fail,
            }
            for i in items
            if i.verdict == Verdict.FAIL
        ]

        response = ScoringResponse(
            score=aggregated["score"],
            stage_scores=aggregated["stage_scores"],
            violations=violations,
            evidence=evidence_payload,
            root_cause=root_cause,
            coaching=coaching,
            revenue_leak=revenue_leak,
            result=aggregated["result"],
            explanation=aggregated["explanation"],
            auto_fail_triggered=aggregated["auto_fail_triggered"],
            items=items,
        )

        await self._persist(call_id, call.agent_user_id, response, actor_user_id, request_id)
        final_status = (
            CallStatus.INSUFFICIENT_EVIDENCE.value
            if response.result == ScoreResult.INSUFFICIENT_EVIDENCE
            else CallStatus.SCORED.value
        )
        await self._calls.update_status(call_id, final_status)
        return response

    def _aggregate(self, items: list[Any]) -> dict[str, Any]:
        scored = [
            i
            for i in items
            if i.verdict in {Verdict.PASS, Verdict.FAIL} and i.score is not None
        ]
        ie_only = [
            i
            for i in items
            if i.verdict == Verdict.INSUFFICIENT_EVIDENCE
        ]
        applicable = [i for i in items if i.verdict != Verdict.NOT_APPLICABLE]

        auto_fail = any(i.auto_fail and i.verdict == Verdict.FAIL for i in items)

        stage_weights: dict[str, float] = defaultdict(float)
        stage_earned: dict[str, float] = defaultdict(float)
        for i in scored:
            cat = i.category or "other"
            stage_weights[cat] += i.weight
            stage_earned[cat] += i.weight * float(i.score)

        stage_scores = {
            cat: round((stage_earned[cat] / stage_weights[cat]) * 100, 2)
            if stage_weights[cat] > 0
            else 0.0
            for cat in stage_weights
        }

        if not scored:
            if ie_only or not applicable:
                return {
                    "score": 0.0,
                    "stage_scores": {},
                    "result": ScoreResult.INSUFFICIENT_EVIDENCE,
                    "explanation": "Insufficient Evidence: no criteria could be scored.",
                    "auto_fail_triggered": False,
                }
            return {
                "score": 0.0,
                "stage_scores": {},
                "result": ScoreResult.INSUFFICIENT_EVIDENCE,
                "explanation": "Insufficient Evidence.",
                "auto_fail_triggered": False,
            }

        total_w = sum(i.weight for i in scored)
        earned = sum(i.weight * float(i.score) for i in scored)
        score = round((earned / total_w) * 100, 2) if total_w else 0.0

        if auto_fail:
            result = ScoreResult.FAIL
            explanation = "Auto-fail triggered by critical rule failure."
        elif score >= 80:
            result = ScoreResult.PASS
            explanation = f"Overall score {score}."
        else:
            result = ScoreResult.FAIL
            explanation = f"Overall score {score} below pass threshold."

        return {
            "score": score,
            "stage_scores": stage_scores,
            "result": result,
            "explanation": explanation,
            "auto_fail_triggered": auto_fail,
        }

    async def _persist(
        self,
        call_id: UUID,
        agent_user_id: UUID | None,
        response: ScoringResponse,
        actor_user_id: UUID | None,
        request_id: str | None,
    ) -> None:
        score_id = await self._scores.save_scoring(call_id, response)
        await self._root_causes.save(call_id, score_id, response.root_cause)
        plan_id = await self._coaching_repo.save_for_call(
            call_id, agent_user_id, response.coaching
        )
        response.coaching["plan_id"] = str(plan_id)
        await self._revenue_repo.save(call_id, score_id, response.revenue_leak)
        await self._audit.record(
            action="score.generated",
            actor_user_id=actor_user_id,
            resource_type="call",
            resource_id=str(call_id),
            request_id=request_id,
            after={"score": response.score, "result": response.result.value},
        )
        logger.info(
            "score.generated",
            extra={"call_id": str(call_id), "action": "score.generated"},
        )

    async def get_analysis(self, call_id: UUID) -> dict[str, Any]:
        payload = await self._scores.get_latest_for_call(call_id)
        if payload is None:
            return insufficient_evidence_response(
                explanation="No scoring result yet for this call."
            ).to_dict()
        # Ensure required top-level keys always present
        return {
            "score": payload.get("score", 0),
            "stage_scores": payload.get("stage_scores") or {},
            "violations": payload.get("violations") or [],
            "evidence": payload.get("evidence") or [],
            "root_cause": payload.get("root_cause") or {},
            "coaching": payload.get("coaching") or {},
            "revenue_leak": payload.get("revenue_leak") or {},
            "result": payload.get("result"),
            "explanation": payload.get("explanation"),
            "auto_fail_triggered": payload.get("auto_fail_triggered", False),
            "schema_version": payload.get("schema_version", "1.0.0"),
            "call_id": str(call_id),
        }
