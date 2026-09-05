"""Intent resolver — blends pattern priors with contextual evidence."""

from __future__ import annotations

import re
from typing import Any

from pragmatics.context_memory import ContextWindow
from pragmatics.pattern_library import PATTERN_LIBRARY


def _compile_patterns() -> list[tuple[dict[str, Any], list[re.Pattern[str]]]]:
    compiled: list[tuple[dict[str, Any], list[re.Pattern[str]]]] = []
    for row in PATTERN_LIBRARY:
        regs = [re.compile(p, re.IGNORECASE) for p in row["patterns"]]
        compiled.append((row, regs))
    return compiled


_COMPILED = _compile_patterns()


class IntentResolver:
    def match(
        self,
        text: str,
        *,
        dialect: str,
        window: ContextWindow,
    ) -> tuple[dict[str, float], list[str], list[dict[str, Any]], dict[str, Any] | None]:
        lowered = text.lower().strip()
        if not lowered:
            return {}, [], [], None

        hits: list[tuple[dict[str, Any], re.Match[str]]] = []
        for row, regs in _COMPILED:
            dialects = set(row.get("dialects") or ["*"])
            if dialect not in dialects and "*" not in dialects and dialect != "unknown":
                continue
            for reg in regs:
                m = reg.search(lowered)
                if m:
                    hits.append((row, m))
                    break
        if not hits:
            return {}, [], [], None

        # Merge probability mass across matched patterns (evidence-weighted).
        intent_mass: dict[str, float] = {}
        pattern_ids: list[str] = []
        evidence: list[dict[str, Any]] = []
        buying_parts: list[float] = []
        exit_parts: list[float] = []
        hidden: list[str] = []

        for row, match in hits:
            pattern_ids.append(str(row["id"]))
            evidence.append(
                {
                    "type": "pattern_span",
                    "pattern_id": row["id"],
                    "quote": match.group(0),
                    "span": [match.start(), match.end()],
                }
            )
            for intent, prior in (row.get("intents") or {}).items():
                intent_mass[intent] = intent_mass.get(intent, 0.0) + float(prior)
            buying_parts.append(float(row.get("buying_prior") or 0.0))
            exit_parts.append(float(row.get("exit_prior") or 0.0))
            hidden.extend(list(row.get("hidden_meanings") or []))

        intents = self._apply_context(intent_mass, window)
        total = sum(intents.values()) or 1.0
        intents = {k: round(v / total, 4) for k, v in intents.items()}
        meta = {
            "buying_prior": round(sum(buying_parts) / len(buying_parts), 4),
            "exit_prior": round(sum(exit_parts) / len(exit_parts), 4),
            "hidden_meanings": list(dict.fromkeys(hidden)),
        }
        return intents, pattern_ids, evidence, meta

    def _apply_context(
        self, intents: dict[str, float], window: ContextWindow
    ) -> dict[str, float]:
        adjusted = dict(intents)
        after = window.joined_after
        before = window.joined_before

        buy_cues = ("giao", "bảo hành", "thanh toán", "ship", "ký", "đặt cọc", "hóa đơn")
        exit_cues = ("gác", "cúp", "đừng gọi", "thôi nhé", "bận rồi")
        info_cues = ("sao", "thế nào", "bao nhiêu", "ở đâu", "có không")

        if any(c in after for c in buy_cues):
            for key in ("ready_to_buy", "interested", "need_information"):
                if key in adjusted:
                    adjusted[key] += 0.12
            for key in ("real_refusal", "soft_rejection", "fake_agreement"):
                if key in adjusted:
                    adjusted[key] = max(0.02, adjusted[key] - 0.08)

        if any(c in after or c in before for c in exit_cues):
            for key in ("exit_imminent", "real_refusal", "soft_rejection"):
                adjusted[key] = adjusted.get(key, 0.0) + 0.10
            if "soft_agreement" in adjusted:
                adjusted["fake_agreement"] = adjusted.get("fake_agreement", 0.0) + 0.08
                adjusted["soft_agreement"] = max(0.02, adjusted["soft_agreement"] - 0.08)

        if any(c in after for c in info_cues):
            adjusted["need_information"] = adjusted.get("need_information", 0.0) + 0.08
            if "delay" in adjusted:
                adjusted["delay"] = max(0.05, adjusted["delay"] - 0.04)

        return adjusted
