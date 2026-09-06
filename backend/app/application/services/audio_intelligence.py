"""Application service for Audio Intelligence Engine."""
from __future__ import annotations

from typing import Any

from audio_engine import AudioIntelligenceEngine, get_audio_engine


class AudioIntelligenceService:
    def __init__(self, engine: AudioIntelligenceEngine | None = None) -> None:
        self._engine = engine or get_audio_engine()

    def upload(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        return self._engine.upload(files)

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._engine.process(payload)

    def repair(self, *, file_id: str, quality: dict[str, Any], force: bool = False) -> dict[str, Any]:
        return self._engine.repair(file_id=file_id, quality=quality, force=force)

    def rollback_repair(self, repair: dict[str, Any]) -> dict[str, Any]:
        return self._engine.rollback_repair(repair)

    def transcript(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._engine.process(payload)
        return {
            "ok": result.get("ok"),
            "blocked": result.get("blocked"),
            "block_reason": result.get("block_reason"),
            "transcript": result.get("transcript"),
            "analysis_allowed": result.get("analysis_allowed"),
            "scoring_allowed": result.get("scoring_allowed"),
        }

    def diarization(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._engine.process(payload)
        return {
            "ok": result.get("ok"),
            "blocked": result.get("blocked"),
            "diarization": result.get("diarization"),
            "separation": result.get("separation"),
        }

    def quality(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload:
            result = self._engine.process(payload)
            return {
                "ok": result.get("ok"),
                "quality": result.get("quality"),
                "quality_gate": result.get("quality_gate"),
                "scoring_allowed": result.get("scoring_allowed"),
                "blocked": result.get("blocked"),
            }
        return self._engine.quality_snapshot()

    def emotion(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._engine.process(payload)
        return {
            "ok": result.get("ok"),
            "emotion_timeline": result.get("emotion_timeline"),
            "blocked": result.get("blocked"),
        }

    def evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._engine.process(payload)
        return {
            "ok": result.get("ok"),
            "evidence": result.get("evidence"),
            "buying_signals": result.get("buying_signals"),
            "objections": result.get("objections"),
            "blocked": result.get("blocked"),
            "scoring_allowed": result.get("scoring_allowed"),
        }

    def export(self, job_id: str, *, fmt: str = "json") -> dict[str, Any]:
        return self._engine.export(job_id, fmt=fmt)

    def batch(self, items: list[dict[str, Any]], *, resume_from: int = 0) -> dict[str, Any]:
        return self._engine.batch_process(items, resume_from=resume_from)

    def search(self, query: dict[str, Any]) -> dict[str, Any]:
        return self._engine.search(query)

    def live(self, chunk: dict[str, Any]) -> dict[str, Any]:
        return self._engine.live_session(chunk)

    def dashboard(self) -> dict[str, Any]:
        return self._engine.dashboard()
