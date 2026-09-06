
"""Speaker diarization — never invent roles when uncertain."""
from __future__ import annotations
from typing import Any
from audio_engine.types import new_id


def diarize_speakers(
    *,
    duration_sec: float,
    transcript_lines: list[dict[str, Any]] | None = None,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    lines = transcript_lines or []
    speakers: dict[str, dict[str, Any]] = {}
    segments: list[dict[str, Any]] = []
    if not lines:
        speakers["unknown_1"] = {
            "speaker_id": "unknown_1", "label": "Unknown Speaker",
            "role": "unknown", "confidence": 0.0, "certain": False,
        }
        segments.append({"speaker_id": "unknown_1", "start_sec": 0.0, "end_sec": float(duration_sec or 0), "confidence": 0.0, "certain": False})
        return {"ok": True, "speakers": list(speakers.values()), "segments": segments, "avg_confidence": 0.0, "uncertain": True, "method": "insufficient_signal"}

    role_map = {
        "agent": ("agent", "Agent"), "a": ("agent", "Agent"), "sale": ("agent", "Agent"), "seller": ("agent", "Agent"),
        "customer": ("customer", "Customer"), "client": ("customer", "Customer"), "c": ("customer", "Customer"),
    }
    confs: list[float] = []
    for line in lines:
        raw = str(line.get("speaker") or "unknown").strip().lower()
        conf = float(line.get("confidence") or 0.7)
        confs.append(conf)
        role, label = role_map.get(raw, ("unknown", "Unknown Speaker"))
        certain = conf >= min_confidence and role != "unknown"
        if not certain:
            role, label = "unknown", "Unknown Speaker"
        sid = role if certain else f"unknown_{raw or 'x'}"
        if sid not in speakers:
            speakers[sid] = {"speaker_id": sid, "label": label, "role": role, "confidence": conf, "certain": certain}
        else:
            speakers[sid]["confidence"] = max(float(speakers[sid]["confidence"]), conf)
            speakers[sid]["certain"] = speakers[sid]["certain"] or certain
        segments.append({
            "speaker_id": sid, "start_sec": float(line.get("start_sec") or 0),
            "end_sec": float(line.get("end_sec") or 0), "confidence": conf, "certain": certain,
            "text": line.get("normalized_text") or line.get("original_text") or line.get("text") or "",
        })
    avg = sum(confs) / max(1, len(confs))
    return {
        "ok": True, "diarization_id": new_id("diar"), "speakers": list(speakers.values()),
        "segments": segments, "avg_confidence": round(avg, 4),
        "uncertain": avg < min_confidence or any(not s["certain"] for s in speakers.values()),
        "method": "transcript_aligned",
    }
