
"""Transcript engine."""
from __future__ import annotations
from typing import Any
from audio_engine.types import TranscriptLine, new_id
from transcript.normalize import detect_dialect, normalize_vietnamese


class TranscriptEngine:
    def build(self, turns: list[dict[str, Any]] | None = None, *, raw_text: str | None = None, duration_sec: float = 0.0) -> dict[str, Any]:
        lines: list[TranscriptLine] = []
        if turns:
            for t in turns:
                original = str(t.get("text") or t.get("original_text") or "").strip()
                if not original:
                    continue
                lines.append(TranscriptLine(
                    speaker=str(t.get("speaker") or "unknown"),
                    start_sec=float(t.get("start_sec") or t.get("start") or 0),
                    end_sec=float(t.get("end_sec") or t.get("end") or 0),
                    confidence=float(t.get("confidence") or 0.75),
                    original_text=original,
                    normalized_text=normalize_vietnamese(str(t.get("normalized_text") or original)),
                    dialect=str(t.get("dialect") or detect_dialect(original)),
                ))
        elif raw_text:
            parts = [p.strip() for p in raw_text.replace("?", ".").split(".") if p.strip()]
            n = max(1, len(parts))
            step = max(1.0, float(duration_sec or n * 3) / n)
            t0 = 0.0
            for i, p in enumerate(parts):
                lines.append(TranscriptLine(
                    speaker="agent" if i % 2 == 0 else "customer",
                    start_sec=round(t0, 2), end_sec=round(t0 + step * 0.9, 2), confidence=0.7,
                    original_text=p, normalized_text=normalize_vietnamese(p), dialect=detect_dialect(p),
                ))
                t0 += step
        payload = [ln.to_dict() for ln in lines]
        avg = sum(float(x["confidence"]) for x in payload) / max(1, len(payload))
        dialects = {x["dialect"] for x in payload if x.get("dialect") and x["dialect"] != "unknown"}
        return {"ok": True, "transcript_id": new_id("tr"), "lines": payload, "avg_confidence": round(avg, 4), "dialects": sorted(dialects) or ["unknown"], "line_count": len(payload)}
