"""Full Audio Intelligence pipeline — no stage skipping."""
from __future__ import annotations

from typing import Any

from audio_engine.types import PIPELINE_STAGES, QUALITY_THRESHOLDS, QualityReport, new_id, now_iso
from audio_repair.repair import AudioRepairEngine
from speaker.diarization import diarize_speakers
from speaker.separation import separate_voices
from transcript.engine import TranscriptEngine


def _estimate_quality(meta: dict[str, Any], hints: dict[str, Any] | None = None) -> QualityReport:
    hints = hints or {}
    score = float(hints.get("quality_score") or 78.0)
    noise = float(hints.get("noise") or 0.15)
    echo = float(hints.get("echo") or 0.08)
    distortion = float(hints.get("distortion") or 0.05)
    clipping = float(hints.get("clipping") or 0.02)
    silence = float(hints.get("silence_ratio") or 0.12)
    low_vol = float(hints.get("low_volume") or 0.1)
    overlap = float(hints.get("overlap_ratio") or 0.05)
    if not meta.get("integrity_ok", True):
        score = min(score, 40.0)
    if int(meta.get("sample_rate") or 0) and int(meta["sample_rate"]) < 8000:
        score = min(score, 45.0)
        distortion = max(distortion, 0.4)
    if float(meta.get("duration_sec") or 0) < 3:
        score = min(score, 50.0)
        silence = max(silence, 0.5)
    blockers: list[str] = []
    if score < QUALITY_THRESHOLDS["audio_quality_min"]:
        blockers.append("audio_quality_below_threshold")
    return QualityReport(
        score=round(score, 2),
        noise=noise,
        echo=echo,
        distortion=distortion,
        clipping=clipping,
        silence_ratio=silence,
        low_volume=low_vol,
        overlap_ratio=overlap,
        repair_recommended=score < 70 or noise >= 0.25,
        blockers=blockers,
        passed_gate=score >= QUALITY_THRESHOLDS["audio_quality_min"] and not blockers,
    )


class AudioPipeline:
    def __init__(self) -> None:
        self.repairer = AudioRepairEngine()
        self.transcript_engine = TranscriptEngine()

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = payload.get("job_id") or new_id("aie")
        stages: dict[str, Any] = {}
        order = list(PIPELINE_STAGES)

        meta = dict(payload.get("file_meta") or {})
        stages["upload"] = {"ok": True, "file_meta": meta, "at": now_iso()}

        integrity_ok = bool(meta.get("integrity_ok", True))
        ext_ok = bool(meta.get("extension"))
        valid = integrity_ok and ext_ok
        stages["validation"] = {
            "ok": valid,
            "integrity_ok": integrity_ok,
            "issues": list(meta.get("integrity_issues") or []),
        }
        if not valid:
            return self._blocked(job_id, stages, order, reason="file_invalid")

        quality = _estimate_quality(meta, payload.get("quality_hints"))
        repair = None
        repaired_quality = quality

        if quality.repair_recommended or payload.get("force_repair"):
            repair = self.repairer.analyze_and_repair(
                file_id=str(meta.get("file_id") or job_id),
                quality=quality,
                force=bool(payload.get("force_repair")),
            )
            repaired_quality = QualityReport(
                score=float(repair["quality_after"]),
                noise=max(0.0, quality.noise - 0.15),
                echo=max(0.0, quality.echo - 0.1),
                distortion=max(0.0, quality.distortion - 0.05),
                clipping=max(0.0, quality.clipping - 0.05),
                silence_ratio=max(0.0, quality.silence_ratio - 0.05),
                low_volume=max(0.0, quality.low_volume - 0.15),
                overlap_ratio=quality.overlap_ratio,
                repair_recommended=False,
                blockers=[],
                passed_gate=float(repair["quality_after"]) >= QUALITY_THRESHOLDS["audio_quality_min"],
            )
            stages["audio_repair"] = {"ok": True, **repair}
        else:
            stages["audio_repair"] = {"ok": True, "skipped_actions": True, "reason": "quality_ok"}

        stages["noise_removal"] = {
            "ok": True,
            "applied": bool(repair and "noise_reduction" in (repair.get("actions") or [])),
            "noise_before": quality.noise,
            "noise_after": repaired_quality.noise,
        }

        if repaired_quality.score < QUALITY_THRESHOLDS["audio_quality_min"]:
            stages["quality_score"] = {"ok": False, "report": repaired_quality.to_dict()}
            return {
                "ok": False,
                "blocked": True,
                "block_reason": "audio_quality_below_threshold",
                "job_id": job_id,
                "pipeline_order": order,
                "stages_completed": [s for s in order if s in stages],
                "stages": stages,
                "quality": repaired_quality.to_dict(),
                "analysis_allowed": False,
                "message": "Không được chấm điểm khi chất lượng âm thanh chưa đạt ngưỡng.",
            }

        turns = list(payload.get("transcript_turns") or [])
        separation = separate_voices(
            duration_sec=float(meta.get("duration_sec") or payload.get("duration_sec") or 60),
            channels=int(meta.get("channels") or 1),
            overlap_hint=float(repaired_quality.overlap_ratio),
            transcript_hint=turns,
        )
        stages["voice_separation"] = separation

        diar = diarize_speakers(
            duration_sec=float(meta.get("duration_sec") or 60),
            transcript_lines=turns,
            min_confidence=QUALITY_THRESHOLDS["speaker_confidence_min"],
        )
        stages["speaker_diarization"] = diar

        transcript = self.transcript_engine.build(
            turns or None,
            raw_text=payload.get("raw_text"),
            duration_sec=float(meta.get("duration_sec") or 60),
        )
        stages["transcript"] = transcript

        diar2 = diarize_speakers(
            duration_sec=float(meta.get("duration_sec") or 60),
            transcript_lines=transcript.get("lines") or [],
            min_confidence=QUALITY_THRESHOLDS["speaker_confidence_min"],
        )
        stages["speaker_diarization"] = diar2

        if float(transcript.get("avg_confidence") or 0) < QUALITY_THRESHOLDS["transcript_confidence_min"]:
            return {
                "ok": False,
                "blocked": True,
                "block_reason": "transcript_confidence_below_threshold",
                "job_id": job_id,
                "pipeline_order": order,
                "stages": stages,
                "quality": repaired_quality.to_dict(),
                "analysis_allowed": False,
            }
        if float(diar2.get("avg_confidence") or 0) < QUALITY_THRESHOLDS["speaker_confidence_min"]:
            stages["quality_score"] = {"ok": False, "report": repaired_quality.to_dict()}
            return {
                "ok": False,
                "blocked": True,
                "block_reason": "speaker_confidence_below_threshold",
                "job_id": job_id,
                "pipeline_order": order,
                "stages": stages,
                "quality": repaired_quality.to_dict(),
                "transcript": transcript,
                "diarization": diar2,
                "analysis_allowed": False,
            }

        stages["evidence_extraction"] = {"ok": True, "pending_engine": True}
        repaired_quality.passed_gate = True
        stages["quality_score"] = {"ok": True, "report": repaired_quality.to_dict()}
        stages["ai_analysis"] = {
            "ok": True,
            "allowed": True,
            "note": "Quality gate passed — AI analysis permitted",
        }

        return {
            "ok": True,
            "blocked": False,
            "job_id": job_id,
            "pipeline_order": order,
            "stages_completed": order,
            "stages": stages,
            "quality": repaired_quality.to_dict(),
            "repair": repair,
            "separation": separation,
            "diarization": diar2,
            "transcript": transcript,
            "analysis_allowed": True,
            "created_at": now_iso(),
        }

    def _blocked(
        self, job_id: str, stages: dict[str, Any], order: list[str], *, reason: str
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "blocked": True,
            "block_reason": reason,
            "job_id": job_id,
            "pipeline_order": order,
            "stages": stages,
            "analysis_allowed": False,
            "created_at": now_iso(),
        }


def run_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    return AudioPipeline().run(payload)
