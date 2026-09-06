"""Failure Replay Engine — capture and replay AI failures for QA."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from evolution.store import EvolutionStore
from evolution.types import new_id, now_iso


class FailureReplayEngine:
    def __init__(self, store: Optional[EvolutionStore] = None) -> None:
        self.store = store or EvolutionStore()

    def capture(
        self,
        *,
        call_id: str,
        audio_ref: str = "",
        transcript: str = "",
        rule: Mapping[str, Any] | None = None,
        ai_output: Mapping[str, Any] | None = None,
        error: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        row = {
            "failure_id": new_id("fail"),
            "call_id": call_id,
            "audio_ref": audio_ref,
            "transcript": transcript,
            "rule": dict(rule or {}),
            "ai_output": dict(ai_output or {}),
            "error": error,
            "created_at": now_iso(),
            "replayable": True,
            "metadata": dict(metadata or {}),
        }
        self.store.append_failure(row)
        return row

    def list_failures(self, *, call_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list_failures()
        if call_id:
            rows = [r for r in rows if r.get("call_id") == call_id]
        return rows

    def replay(self, failure_id: str) -> dict[str, Any]:
        row = next((r for r in self.store.list_failures() if r.get("failure_id") == failure_id), None)
        if row is None:
            raise ValueError(f"Failure not found: {failure_id}")
        return {
            "failure_id": failure_id,
            "replay": {
                "audio_ref": row.get("audio_ref"),
                "transcript": row.get("transcript"),
                "rule": row.get("rule"),
                "ai_output": row.get("ai_output"),
                "error": row.get("error"),
            },
            "qa_review_ready": True,
            "replayed_at": now_iso(),
        }
