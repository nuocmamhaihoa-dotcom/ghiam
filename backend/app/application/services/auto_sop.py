"""Auto SOP Generator — derive SOP/checklist/rules/coaching from top calls."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


class AutoSOPGenerator:
    def generate(self, golden_calls: list[dict[str, Any]], *, version: str | None = None) -> dict[str, Any]:
        if len(golden_calls) < 1:
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: need successful calls to generate SOP.",
            }

        stage_counter: Counter[str] = Counter()
        tip_counter: Counter[str] = Counter()
        for call in golden_calls:
            for stage in call.get("stages") or call.get("stage_flow") or []:
                stage_counter[str(stage)] += 1
            for tip in call.get("coaching_highlights") or call.get("strengths") or []:
                tip_counter[str(tip)] += 1

        if not stage_counter:
            # Fall back to canonical sales stages when calls lack explicit stage tags
            stage_counter = Counter(
                {
                    "Opening": len(golden_calls),
                    "Discovery": len(golden_calls),
                    "Value": len(golden_calls),
                    "Objection Handling": len(golden_calls),
                    "Closing": len(golden_calls),
                }
            )

        checklist = [
            {"step": idx + 1, "item": stage, "required": True, "support_count": count}
            for idx, (stage, count) in enumerate(stage_counter.most_common())
        ]
        rules = [
            {
                "rule_id": f"AUTO-{idx+1:03d}",
                "name": f"Must cover {stage}",
                "description": f"Top calls consistently include stage '{stage}'.",
                "weight": round(count / max(len(golden_calls), 1), 3),
                "evidence": f"Observed in {count}/{len(golden_calls)} golden calls",
            }
            for idx, (stage, count) in enumerate(stage_counter.most_common(10))
        ]
        coaching = [
            {"tip": tip, "frequency": freq}
            for tip, freq in tip_counter.most_common(10)
        ] or [
            {"tip": "Giữ discovery trước khi báo giá", "frequency": len(golden_calls)},
            {"tip": "Xác nhận objection trước khi reframe", "frequency": len(golden_calls)},
            {"tip": "Luôn có câu chốt rõ ràng", "frequency": len(golden_calls)},
        ]

        ver = version or datetime.now(timezone.utc).strftime("sop-%Y%m%dT%H%M%SZ")
        return {
            "status": "ok",
            "version": ver,
            "source_call_count": len(golden_calls),
            "sop": {
                "title": "Auto-generated Sales SOP",
                "stages": checklist,
            },
            "checklist": checklist,
            "rules": rules,
            "coaching": coaching,
            "version_history_entry": {
                "version": ver,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_call_count": len(golden_calls),
            },
        }
