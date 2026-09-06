
"""Audio Repair Engine."""
from __future__ import annotations

import hashlib
from typing import Any

from audio_engine.types import QualityReport, new_id, now_iso


class AudioRepairEngine:
    def analyze_and_repair(
        self,
        *,
        file_id: str,
        quality: QualityReport | dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        q = quality if isinstance(quality, dict) else quality.to_dict()
        actions: list[str] = []
        if float(q.get("noise") or 0) >= 0.25 or force:
            actions.append("noise_reduction")
        if float(q.get("echo") or 0) >= 0.20 or force:
            actions.append("echo_cancellation")
        if float(q.get("low_volume") or 0) >= 0.25 or force:
            actions.append("volume_normalization")
        if float(q.get("clipping") or 0) >= 0.10 or force:
            actions.append("clipping_repair")
        if float(q.get("silence_ratio") or 0) >= 0.35 or force:
            actions.append("silence_optimization")
        if not actions and float(q.get("score") or 0) < 70:
            actions = ["noise_reduction", "volume_normalization"]
        repair_id = new_id("repair")
        fingerprint = hashlib.sha256(f"{file_id}|{','.join(actions)}".encode()).hexdigest()[:16]
        improved = min(100.0, float(q.get("score") or 0) + 8.0 * max(1, len(actions)))
        return {
            "ok": True,
            "repair_id": repair_id,
            "file_id": file_id,
            "actions": actions,
            "original_ref": f"original/{file_id}",
            "repaired_ref": f"repaired/{file_id}.{repair_id}",
            "fingerprint": fingerprint,
            "quality_before": float(q.get("score") or 0),
            "quality_after": round(improved, 2),
            "rollback_available": True,
            "created_at": now_iso(),
        }

    def rollback(self, repair: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "rolled_back": True,
            "file_id": repair.get("file_id"),
            "active_ref": repair.get("original_ref"),
            "previous_repair_id": repair.get("repair_id"),
            "created_at": now_iso(),
        }
