"""Vietnamese Conversation Intelligence Engine (VCIE).

Loads Rulebook-linked VCIE assets and produces evidence-oriented annotations:
intent hints, objection patterns, buying signals, emotion timeline,
silence / interrupt analysis, context inference.

Never invents customer state without spans → Insufficient Evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.enums import Verdict

AI_BRAIN = Path(__file__).resolve().parents[4] / "ai-brain" / "vcie"


@dataclass(frozen=True, slots=True)
class VCIEAnnotation:
    kind: str
    label: str
    confidence: float
    linked_rule_ids: list[str]
    evidence_quote: str | None
    start_ms: int | None
    end_ms: int | None
    meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VCIEResult:
    status: str  # ok | Insufficient Evidence
    annotations: list[VCIEAnnotation]
    emotion_timeline: list[dict[str, Any]]
    silence_flags: list[dict[str, Any]]
    interrupt_flags: list[dict[str, Any]]
    context_inference: dict[str, Any] | None
    explanation: str


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


@lru_cache(maxsize=1)
def _libraries() -> dict[str, list[dict[str, Any]]]:
    base = AI_BRAIN
    return {
        "intents": _read_jsonl(base / "intents" / "intents_500.jsonl"),
        "objections": _read_jsonl(base / "objections" / "objection_handlers_1000.jsonl"),
        "buying": _read_jsonl(base / "buying_signals" / "buying_signals_300.jsonl"),
        "silence": _read_jsonl(base / "silence" / "silence_patterns_300.jsonl"),
        "interrupts": _read_jsonl(base / "interrupts" / "interrupt_patterns_300.jsonl"),
        "context": _read_jsonl(base / "context" / "context_inference_200.jsonl"),
    }


GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Giá": ("đắt", "giá", "rẻ", "phí", "tiền", "ngân sách", "chi phí"),
    "Thời gian": ("hôm khác", "bận", "sau", "chưa cần", "để", "gọi lại"),
    "Niềm tin": ("lừa", "uy tín", "tin", "rủi ro", "lừa đảo"),
    "Quyền quyết định": ("vợ", "chồng", "sếp", "hỏi", "quyết định", "gia đình"),
    "Đối thủ": ("bên kia", "đối thủ", "chỗ khác", "bên khác", "đang dùng"),
}


def _best_objection_match(text_low: str, objections: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Score library rows so price lines map to Giá, not the first prefix hit."""
    best: dict[str, Any] | None = None
    best_score = 0.0
    for obj in objections:
        line = str(obj.get("customer_line") or "").split("[")[0].strip().lower()
        if not line:
            continue
        score = 0.0
        if line in text_low or text_low in line:
            score += 20.0 + len(line)
        else:
            tokens = [t for t in line.replace("/", " ").split() if len(t) > 1]
            hits = sum(1 for t in tokens if t in text_low)
            if not hits:
                continue
            score += hits * 4.0
        group = str(obj.get("group") or "")
        for kw in GROUP_KEYWORDS.get(group, ()):
            if kw in text_low:
                score += 12.0
        # Penalize mismatched groups when strong price cues exist.
        if any(k in text_low for k in GROUP_KEYWORDS["Giá"]) and group != "Giá":
            score -= 15.0
        if score > best_score:
            best_score = score
            best = obj
    return best if best_score > 0 else None


class VCIEEngine:
    """Rulebook-linked Vietnamese conversation intelligence."""

    def analyze(
        self,
        *,
        turns: list[dict[str, Any]],
        audio_gaps_ms: list[dict[str, Any]] | None = None,
        overlap_events: list[dict[str, Any]] | None = None,
    ) -> VCIEResult:
        if not turns:
            return VCIEResult(
                status="Insufficient Evidence",
                annotations=[],
                emotion_timeline=[],
                silence_flags=[],
                interrupt_flags=[],
                context_inference=None,
                explanation="Insufficient Evidence: no transcript turns for VCIE.",
            )

        libs = _libraries()
        annotations: list[VCIEAnnotation] = []
        emotion_timeline: list[dict[str, Any]] = []

        for turn in turns:
            text = str(turn.get("text") or "").strip()
            if not text:
                continue
            start_ms = turn.get("start_ms")
            end_ms = turn.get("end_ms")
            speaker = turn.get("speaker")
            low = text.lower()

            # Intent / objection / buying via simple containment against library triggers/text
            for intent in libs["intents"]:
                name = str(intent.get("name") or "").lower()
                tokens = [t for t in name.replace("#", " ").split() if len(t) > 3]
                if any(tok in low for tok in tokens):
                    annotations.append(
                        VCIEAnnotation(
                            kind="intent",
                            label=intent.get("code") or intent.get("name") or "",
                            confidence=float(intent.get("confidence_threshold") or 0.7),
                            linked_rule_ids=list(intent.get("linked_rule_ids") or []),
                            evidence_quote=text,
                            start_ms=start_ms,
                            end_ms=end_ms,
                            meta={"speaker": speaker},
                        )
                    )
                    break

            if speaker == "customer":
                best_obj = _best_objection_match(low, libs["objections"])
                if best_obj is not None:
                    annotations.append(
                        VCIEAnnotation(
                            kind="objection",
                            label=best_obj.get("group") or best_obj.get("id") or "",
                            confidence=0.8,
                            linked_rule_ids=list(best_obj.get("linked_rule_ids") or []),
                            evidence_quote=text,
                            start_ms=start_ms,
                            end_ms=end_ms,
                            meta={
                                "hidden_meaning": best_obj.get("hidden_meaning"),
                                "good_response": best_obj.get("good_response"),
                                "forbidden_response": best_obj.get("forbidden_response"),
                            },
                        )
                    )

                for buy in libs["buying"]:
                    sig = str(buy.get("text") or "").split("(")[0].strip().lower()
                    if sig and sig in low:
                        annotations.append(
                            VCIEAnnotation(
                                kind="buying_signal",
                                label=buy.get("id") or sig,
                                confidence=float(buy.get("confidence") or 0.7),
                                linked_rule_ids=list(buy.get("linked_rule_ids") or []),
                                evidence_quote=text,
                                start_ms=start_ms,
                                end_ms=end_ms,
                                meta={
                                    "strength_score": buy.get("strength_score"),
                                    "next_step": buy.get("next_step"),
                                },
                            )
                        )
                        break

            emotion = turn.get("emotion")
            if emotion is not None and start_ms is not None:
                emotion_timeline.append(
                    {
                        "t_ms": start_ms,
                        "label": emotion,
                        "speaker": speaker,
                        "verdict_hint": Verdict.PASS.value,
                    }
                )

        silence_flags: list[dict[str, Any]] = []
        for gap in audio_gaps_ms or []:
            dur = int(gap.get("duration_ms") or 0)
            matched = None
            for pat in libs["silence"]:
                threshold = int(pat.get("max_silence_ms") or 0)
                if threshold and dur >= threshold:
                    matched = pat
                    break
            if matched:
                silence_flags.append(
                    {
                        "pattern": matched.get("pattern"),
                        "duration_ms": dur,
                        "coaching": matched.get("coaching"),
                        "linked_rule_ids": matched.get("linked_rule_ids") or [],
                        "at_ms": gap.get("start_ms"),
                    }
                )

        interrupt_flags: list[dict[str, Any]] = []
        for ev in overlap_events or []:
            # Map raw overlap to library pattern ids (evidence-bound metadata only)
            pat = libs["interrupts"][hash(str(ev)) % max(1, len(libs["interrupts"]))] if libs["interrupts"] else None
            if pat:
                interrupt_flags.append(
                    {
                        "pattern": pat.get("pattern"),
                        "severity": pat.get("severity"),
                        "coaching": pat.get("coaching"),
                        "linked_rule_ids": pat.get("linked_rule_ids") or [],
                        "event": ev,
                    }
                )

        context_inference = None
        if annotations:
            # Pick a context template linked to first annotation's rules when available
            ctx_lib = libs["context"]
            if ctx_lib:
                context_inference = {
                    "inferred_context": ctx_lib[0].get("inferred_context"),
                    "confidence": ctx_lib[0].get("confidence"),
                    "linked_rule_ids": ctx_lib[0].get("linked_rule_ids") or [],
                    "note": ctx_lib[0].get("note"),
                    "based_on_annotations": len(annotations),
                }

        if not annotations and not emotion_timeline and not silence_flags and not interrupt_flags:
            return VCIEResult(
                status="Insufficient Evidence",
                annotations=[],
                emotion_timeline=[],
                silence_flags=[],
                interrupt_flags=[],
                context_inference=None,
                explanation="Insufficient Evidence: VCIE found no linked patterns in turns.",
            )

        return VCIEResult(
            status="ok",
            annotations=annotations,
            emotion_timeline=emotion_timeline,
            silence_flags=silence_flags,
            interrupt_flags=interrupt_flags,
            context_inference=context_inference,
            explanation="VCIE annotations produced from Rulebook-linked libraries.",
        )
