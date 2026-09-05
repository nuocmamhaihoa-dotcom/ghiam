"""Pipeline Orchestrator — Constitution §3 immutable stage order."""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from app.application.services.pipeline_stages import (
    INSUFFICIENT,
    PIPELINE_ORDER,
    HeuristicDiarizer,
    LocalWhisperBridge,
    OpenAIWhisperAdapter,
    PipelineContext,
    StageResult,
    extract_evidence,
    normalize_transcript,
    semantic_segment,
    verify_evidence,
)
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class PipelineOrchestrator:
    """Run Audio→…→Evidence Verify; scoring attaches later stages in order."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key = getattr(settings, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
        self._whisper_api = OpenAIWhisperAdapter(api_key) if api_key else None
        self._whisper_bridge = LocalWhisperBridge()
        self._diarizer = HeuristicDiarizer()
        self._stt_min = float(
            getattr(settings, "stt_min_avg_confidence", None)
            or getattr(settings, "stt_min_confidence", 0.65)
        )

    def run_pre_scoring(
        self,
        *,
        call_id: UUID | str,
        audio_bytes: bytes | None,
        audio_content_type: str | None = None,
        audio_s3_key: str | None = None,
        transcript_turns: list[dict[str, Any]] | None = None,
        existing_evidence: list[dict[str, Any]] | None = None,
        industry: str | None = None,
        dialect: str | None = None,
    ) -> dict[str, Any]:
        ctx = PipelineContext(
            call_id=str(call_id),
            audio_bytes=audio_bytes,
            audio_content_type=audio_content_type,
            audio_s3_key=audio_s3_key,
            existing_transcript_turns=list(transcript_turns or []),
            existing_evidence=list(existing_evidence or []),
            industry=industry,
            dialect=dialect,
        )

        if not (audio_bytes or audio_s3_key or ctx.existing_transcript_turns):
            ctx.record(
                StageResult(
                    "audio",
                    False,
                    error=f"{INSUFFICIENT}: no audio bytes, s3 key, or transcript turns.",
                )
            )
            return self._abort(ctx)

        ctx.record(
            StageResult(
                "audio",
                True,
                {
                    "has_bytes": bool(audio_bytes),
                    "s3_key": audio_s3_key,
                    "content_type": audio_content_type,
                    "fallback_transcript_turns": len(ctx.existing_transcript_turns),
                },
            )
        )

        try:
            whisper_art = self._run_whisper(ctx)
            avg_conf = float(
                whisper_art.get("avg_confidence")
                or whisper_art.get("avg_confidence")
                or 0
            )
            if avg_conf < self._stt_min:
                ctx.record(
                    StageResult(
                        "whisper",
                        False,
                        whisper_art,
                        error=(
                            f"{INSUFFICIENT}: STT avg_confidence "
                            f"{avg_conf} < {self._stt_min}."
                        ),
                    )
                )
                return self._abort(ctx)
            ctx.record(StageResult("whisper", True, whisper_art))
        except Exception as exc:  # noqa: BLE001 — stage boundary
            ctx.record(StageResult("whisper", False, error=str(exc)))
            return self._abort(ctx)

        try:
            turns = self._diarizer.diarize(
                audio_bytes or b"",
                transcript_segments=whisper_art["segments"],
            )
            ctx.record(
                StageResult("diarization", True, {"turns": turns, "turn_count": len(turns)})
            )
        except Exception as exc:  # noqa: BLE001
            ctx.record(StageResult("diarization", False, error=str(exc)))
            return self._abort(ctx)

        try:
            normalized = normalize_transcript(turns)
            ctx.record(
                StageResult(
                    "normalize", True, {"turns": normalized, "turn_count": len(normalized)}
                )
            )
        except Exception as exc:  # noqa: BLE001
            ctx.record(StageResult("normalize", False, error=str(exc)))
            return self._abort(ctx)

        try:
            segmented = semantic_segment(normalized)
            ctx.record(
                StageResult(
                    "segment", True, {"turns": segmented, "turn_count": len(segmented)}
                )
            )
        except Exception as exc:  # noqa: BLE001
            ctx.record(StageResult("segment", False, error=str(exc)))
            return self._abort(ctx)

        try:
            extracted = extract_evidence(segmented)
            for span in ctx.existing_evidence:
                if span.get("quote") and (
                    span.get("audio_ts_start") is not None
                    or span.get("start_ms") is not None
                ):
                    extracted.append(span)
            ctx.record(
                StageResult(
                    "evidence_extract",
                    True,
                    {"evidence": extracted, "count": len(extracted)},
                )
            )
        except Exception as exc:  # noqa: BLE001
            ctx.record(StageResult("evidence_extract", False, error=str(exc)))
            return self._abort(ctx)

        try:
            verified = verify_evidence(extracted, min_confidence=self._stt_min)
            ctx.record(
                StageResult(
                    "evidence_verify",
                    True,
                    {"evidence": verified, "count": len(verified)},
                )
            )
        except Exception as exc:  # noqa: BLE001
            ctx.record(StageResult("evidence_verify", False, error=str(exc)))
            return self._abort(ctx)

        return {
            "ok": True,
            "insufficient_evidence": False,
            "pipeline_order": list(PIPELINE_ORDER),
            "stages": [self._stage_dict(s) for s in ctx.stages],
            "verified_evidence": verified,
            "normalized_turns": normalized,
            "segmented_turns": segmented,
            "whisper": whisper_art,
        }

    def attach_post_scoring(
        self,
        pre: dict[str, Any],
        *,
        rule_engine: dict[str, Any],
        judge_ensemble: dict[str, Any],
        root_cause: dict[str, Any],
        coaching: dict[str, Any],
        revenue_leak: dict[str, Any],
        dashboard_json: dict[str, Any],
    ) -> dict[str, Any]:
        stages = list(pre.get("stages") or [])
        for name, artifact in (
            ("rule_engine", rule_engine),
            ("judge_ensemble", judge_ensemble),
            ("root_cause", root_cause),
            ("coaching", coaching),
            ("revenue_leak", revenue_leak),
            ("dashboard_json", dashboard_json),
        ):
            stages.append(
                {
                    "name": name,
                    "ok": True,
                    "artifact_keys": sorted(artifact.keys()),
                    "error": None,
                }
            )
        out = dict(pre)
        out["stages"] = stages
        out["dashboard_json"] = dashboard_json
        names = [s["name"] for s in stages]
        out["pipeline_complete"] = all(n in names for n in PIPELINE_ORDER)
        return out

    def _run_whisper(self, ctx: PipelineContext) -> dict[str, Any]:
        if self._whisper_api and ctx.audio_bytes:
            return self._whisper_api.transcribe(
                ctx.audio_bytes, content_type=ctx.audio_content_type
            )
        if ctx.existing_transcript_turns:
            return self._whisper_bridge.transcribe_from_turns(ctx.existing_transcript_turns)
        if ctx.audio_bytes and not self._whisper_api:
            raise RuntimeError(
                f"{INSUFFICIENT}: audio present but Whisper API key not configured "
                "and no transcript turns available."
            )
        raise RuntimeError(f"{INSUFFICIENT}: cannot run Whisper stage.")

    def _abort(self, ctx: PipelineContext) -> dict[str, Any]:
        last = ctx.stages[-1] if ctx.stages else None
        logger.warning(
            "pipeline.abort call_id=%s stage=%s error=%s",
            ctx.call_id,
            last.name if last else None,
            last.error if last else None,
        )
        return {
            "ok": False,
            "insufficient_evidence": True,
            "pipeline_order": list(PIPELINE_ORDER),
            "stages": [self._stage_dict(s) for s in ctx.stages],
            "verified_evidence": [],
            "error": last.error if last else INSUFFICIENT,
        }

    @staticmethod
    def _stage_dict(stage: StageResult) -> dict[str, Any]:
        return {
            "name": stage.name,
            "ok": stage.ok,
            "artifact_keys": sorted(stage.artifact.keys()),
            "error": stage.error,
        }
