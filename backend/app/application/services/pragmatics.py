"""Vietnamese Pragmatics Engine (VPE).

Interprets soft refusals, delays, polite exits, dialect variants, and hidden
meanings using ±N turn context. Never invents intent without textual evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
LIB_DIRS = (
    ROOT / "datasets" / "pragmatics",
    ROOT / "ai-brain" / "pragmatics",
)

_BUILTIN_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "PRAG-DELAY-XEM",
        "dialects": ["north", "central", "south", "*"],
        "patterns": [
            r"để\s+(em|anh|chị|mình)\s+(coi|xem|nghĩ|cân nhắc)",
            r"để\s+em\s+xem\s+đã",
            r"em\s+xem\s+lại",
        ],
        "intents": {
            "delay": 0.45,
            "soft_rejection": 0.25,
            "need_information": 0.20,
            "real_refusal": 0.10,
        },
        "hidden_meanings": [
            "Customer wants time; not necessarily rejecting.",
            "May be polite stall while evaluating value.",
        ],
        "exit_risk": 0.35,
        "buying_probability": 0.25,
    },
    {
        "id": "PRAG-SOFT-OK",
        "dialects": ["north", "central", "south", "*"],
        "patterns": [
            r"ừ\s+cũng\s+được",
            r"cũng\s+được\s+đấy",
            r"nghe\s+cũng\s+ổn",
            r"được\s+đấy",
        ],
        "intents": {
            "soft_agreement": 0.40,
            "interested": 0.30,
            "delay": 0.20,
            "fake_agreement": 0.10,
        },
        "hidden_meanings": [
            "Mild positive lean; needs a clear close ask.",
            "Agreement may be social courtesy — confirm commitment.",
        ],
        "exit_risk": 0.20,
        "buying_probability": 0.55,
    },
    {
        "id": "PRAG-PRICE-HIGH",
        "dialects": ["north", "central", "south", "*"],
        "patterns": [
            r"đắt\s+quá",
            r"mắc\s+quá",
            r"giá\s+(cao|mắc|đắt)",
            r"hơi\s+(cao|mắc|đắt)",
        ],
        "intents": {
            "price_concern": 0.55,
            "negotiation": 0.25,
            "soft_rejection": 0.15,
            "real_refusal": 0.05,
        },
        "hidden_meanings": [
            "Price pushback; often asks for value reframe, not hard no.",
            "May accept if cost-per-use or bundle is explained.",
        ],
        "exit_risk": 0.40,
        "buying_probability": 0.35,
    },
    {
        "id": "PRAG-ASK-SPOUSE",
        "dialects": ["north", "central", "south", "*"],
        "patterns": [
            r"để\s+(hỏi|bàn)\s+(vợ|chồng|ba|mẹ|sếp|gia đình)",
            r"phải\s+hỏi\s+(vợ|chồng|sếp)",
            r"vợ\s+(em|anh)\s+quyết",
        ],
        "intents": {
            "decision_maker_missing": 0.50,
            "delay": 0.30,
            "soft_rejection": 0.15,
            "real_refusal": 0.05,
        },
        "hidden_meanings": [
            "Decision maker not on the call; schedule joint follow-up.",
            "Sometimes a polite exit — probe who decides and when.",
        ],
        "exit_risk": 0.45,
        "buying_probability": 0.30,
    },
    {
        "id": "PRAG-CALL-LATER",
        "dialects": ["north", "central", "south", "*"],
        "patterns": [
            r"mai\s+(gọi|alo)\s+lại",
            r"gọi\s+lại\s+(sau|mai|chiều|tối)",
            r"đang\s+bận",
            r"xong\s+việc\s+rồi\s+gọi",
        ],
        "intents": {
            "delay": 0.40,
            "busy": 0.30,
            "soft_rejection": 0.20,
            "exit_intent": 0.10,
        },
        "hidden_meanings": [
            "Time objection or soft exit; lock a concrete callback slot.",
            "If repeated, treat as rising exit risk.",
        ],
        "exit_risk": 0.50,
        "buying_probability": 0.20,
    },
    {
        "id": "PRAG-SOUTH-DELAY",
        "dialects": ["south"],
        "patterns": [
            r"để\s+em\s+coi\s+đã",
            r"ừ\s+rồi\s+tính",
            r"khoan\s+đã",
            r"đợi\s+em\s+xíu",
        ],
        "intents": {
            "delay": 0.50,
            "need_information": 0.25,
            "soft_rejection": 0.15,
            "real_refusal": 0.10,
        },
        "hidden_meanings": [
            "Southern soft stall; continue discovery before pitching harder.",
        ],
        "exit_risk": 0.30,
        "buying_probability": 0.28,
    },
    {
        "id": "PRAG-NORTH-POLITE-NO",
        "dialects": ["north"],
        "patterns": [
            r"em\s+cảm\s+ơn",
            r"em\s+xin\s+phép\s+không",
            r"có\s+lẽ\s+không\s+tiện",
            r"em\s+nghĩ\s+lại\s+đã",
        ],
        "intents": {
            "soft_rejection": 0.45,
            "delay": 0.30,
            "real_refusal": 0.15,
            "exit_intent": 0.10,
        },
        "hidden_meanings": [
            "Northern polite decline; ask one clarifying question before accepting no.",
        ],
        "exit_risk": 0.55,
        "buying_probability": 0.18,
    },
    {
        "id": "PRAG-BUYING-SIGNAL",
        "dialects": ["north", "central", "south", "*"],
        "patterns": [
            r"bao\s+giờ\s+giao",
            r"có\s+bảo\s+hành\s+không",
            r"thanh\s+toán\s+(sao|thế\s+nào|như\s+thế\s+nào)",
            r"có\s+(hóa\s+đơn|xuất\s+VAT|vat)",
            r"lấy\s+(hai|2|ba|3)\s*(cái|hộp|chai)?",
        ],
        "intents": {
            "ready_to_buy": 0.55,
            "interested": 0.30,
            "need_information": 0.15,
        },
        "hidden_meanings": [
            "Strong buying signal — ask for commitment and logistics.",
        ],
        "exit_risk": 0.10,
        "buying_probability": 0.80,
    },
]


@dataclass(frozen=True, slots=True)
class PragmaticTurnResult:
    turn_index: int
    text: str
    dialect: str
    matched_pattern_id: str | None
    intent_probability: dict[str, float]
    emotion_probability: dict[str, float]
    hidden_meaning: list[str]
    buying_probability: float | None
    exit_risk: float | None
    confidence: float
    evidence_quote: str | None
    context_window: list[int]
    status: str


@dataclass(frozen=True, slots=True)
class PragmaticsResult:
    status: str
    dialect: str
    turns: list[PragmaticTurnResult]
    timeline: list[dict[str, Any]]
    summary: dict[str, Any]
    explanation: str


def _detect_dialect(text: str, hint: str | None = None) -> str:
    if hint in {"north", "central", "south"}:
        return hint
    south_markers = ("coi", "ráng", "xíu", "hen", "ghê")
    north_markers = ("ạ", "cháu", "bác", "nhé", "cơ")
    central_markers = ("mi", "tau", "răng", "chi")
    lower = text.lower()
    scores = {
        "south": sum(1 for m in south_markers if m in lower),
        "north": sum(1 for m in north_markers if m in lower),
        "central": sum(1 for m in central_markers if m in lower),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "south"


def _emotion_from_intents(intents: dict[str, float]) -> dict[str, float]:
    mapping = {
        "price_concern": {"hesitation": 0.5, "frustration": 0.3, "neutral": 0.2},
        "ready_to_buy": {"interested": 0.5, "excited": 0.3, "trust": 0.2},
        "soft_rejection": {"hesitation": 0.4, "exit_intent": 0.3, "neutral": 0.3},
        "delay": {"hesitation": 0.5, "neutral": 0.3, "curious": 0.2},
        "interested": {"curious": 0.4, "interested": 0.4, "trust": 0.2},
        "busy": {"neutral": 0.5, "exit_intent": 0.3, "frustration": 0.2},
        "decision_maker_missing": {"hesitation": 0.4, "neutral": 0.4, "curious": 0.2},
        "soft_agreement": {"interested": 0.4, "trust": 0.3, "neutral": 0.3},
        "fake_agreement": {"neutral": 0.5, "hesitation": 0.3, "exit_intent": 0.2},
        "exit_intent": {"exit_intent": 0.6, "frustration": 0.2, "neutral": 0.2},
        "negotiation": {"curious": 0.4, "hesitation": 0.3, "interested": 0.3},
        "need_information": {"curious": 0.5, "neutral": 0.3, "interested": 0.2},
        "real_refusal": {"exit_intent": 0.5, "frustration": 0.3, "neutral": 0.2},
    }
    if not intents:
        return {"neutral": 1.0}
    top = max(intents, key=intents.get)
    return mapping.get(top, {"neutral": 1.0})


@lru_cache(maxsize=1)
def _load_library() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = list(_BUILTIN_PATTERNS)
    for directory in LIB_DIRS:
        path = directory / "patterns.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _match_pattern(
    text: str, dialect: str, library: list[dict[str, Any]]
) -> dict[str, Any] | None:
    lower = text.lower().strip()
    if not lower:
        return None
    for row in library:
        dialects = row.get("dialects") or ["*"]
        if "*" not in dialects and dialect not in dialects:
            continue
        for pattern in row.get("patterns") or []:
            if re.search(pattern, lower, flags=re.IGNORECASE):
                return row
    return None


def _window_indices(index: int, total: int, radius: int = 3) -> list[int]:
    start = max(0, index - radius)
    end = min(total, index + radius + 1)
    return list(range(start, end))


def _is_customer(speaker: str) -> bool:
    value = speaker.lower().strip()
    return value in {
        "customer",
        "cust",
        "client",
        "khach",
        "khách",
        "khách hàng",
    }


class PragmaticsEngine:
    """Context memory + intent resolver + hidden-meaning detector."""

    def __init__(self, *, context_radius: int = 3) -> None:
        self._radius = context_radius
        self._library = _load_library()

    def analyze_transcript(
        self,
        turns: list[dict[str, Any]],
        *,
        dialect_hint: str | None = None,
    ) -> PragmaticsResult:
        if not turns:
            return PragmaticsResult(
                status="Insufficient Evidence",
                dialect=dialect_hint or "unknown",
                turns=[],
                timeline=[],
                summary={},
                explanation="Insufficient Evidence: no transcript turns for pragmatics.",
            )

        joined = " ".join(str(t.get("text") or "") for t in turns)
        dialect = _detect_dialect(joined, dialect_hint)
        results: list[PragmaticTurnResult] = []
        timeline: list[dict[str, Any]] = []

        for idx, turn in enumerate(turns):
            text = str(turn.get("text") or "").strip()
            speaker = str(turn.get("speaker") or "customer")
            if not _is_customer(speaker):
                continue

            matched = _match_pattern(text, dialect, self._library)
            window = _window_indices(idx, len(turns), self._radius)
            if matched is None:
                results.append(
                    PragmaticTurnResult(
                        turn_index=idx,
                        text=text,
                        dialect=dialect,
                        matched_pattern_id=None,
                        intent_probability={},
                        emotion_probability={},
                        hidden_meaning=[],
                        buying_probability=None,
                        exit_risk=None,
                        confidence=0.0,
                        evidence_quote=None,
                        context_window=window,
                        status="Insufficient Evidence",
                    )
                )
                continue

            intents = {
                str(key): float(value)
                for key, value in (matched.get("intents") or {}).items()
            }
            neighbor_text = " ".join(
                str(turns[j].get("text") or "") for j in window if j != idx
            ).lower()
            if any(
                token in neighbor_text
                for token in ("bảo hành", "giao", "thanh toán", "hóa đơn")
            ):
                if "ready_to_buy" in intents:
                    intents["ready_to_buy"] = min(0.9, intents["ready_to_buy"] + 0.1)
                if "delay" in intents:
                    intents["delay"] = max(0.05, intents["delay"] - 0.05)

            total = sum(intents.values()) or 1.0
            intents = {key: round(value / total, 4) for key, value in intents.items()}
            emotions = _emotion_from_intents(intents)
            confidence = round(max(intents.values()) if intents else 0.0, 3)
            buying = float(matched.get("buying_probability") or 0.0)
            exit_risk = float(matched.get("exit_risk") or 0.0)
            result = PragmaticTurnResult(
                turn_index=idx,
                text=text,
                dialect=dialect,
                matched_pattern_id=str(matched.get("id")),
                intent_probability=intents,
                emotion_probability=emotions,
                hidden_meaning=list(matched.get("hidden_meanings") or []),
                buying_probability=buying,
                exit_risk=exit_risk,
                confidence=confidence,
                evidence_quote=text,
                context_window=window,
                status="ok",
            )
            results.append(result)
            timeline.append(
                {
                    "turn_index": idx,
                    "pattern_id": result.matched_pattern_id,
                    "top_intent": max(intents, key=intents.get) if intents else None,
                    "buying_probability": buying,
                    "exit_risk": exit_risk,
                    "confidence": confidence,
                    "evidence_quote": text,
                }
            )

        matched_turns = [row for row in results if row.status == "ok"]
        if not matched_turns:
            return PragmaticsResult(
                status="Insufficient Evidence",
                dialect=dialect,
                turns=results,
                timeline=[],
                summary={},
                explanation=(
                    "Insufficient Evidence: no pragmatic patterns matched "
                    "in customer turns."
                ),
            )

        avg_buy = sum(row.buying_probability or 0.0 for row in matched_turns) / len(
            matched_turns
        )
        avg_exit = sum(row.exit_risk or 0.0 for row in matched_turns) / len(
            matched_turns
        )
        intent_counter: dict[str, float] = {}
        for row in matched_turns:
            for intent, prob in row.intent_probability.items():
                intent_counter[intent] = intent_counter.get(intent, 0.0) + prob
        top_intent = max(intent_counter, key=intent_counter.get)

        return PragmaticsResult(
            status="ok",
            dialect=dialect,
            turns=results,
            timeline=timeline,
            summary={
                "top_intent": top_intent,
                "avg_buying_probability": round(avg_buy, 3),
                "avg_exit_risk": round(avg_exit, 3),
                "matched_turns": len(matched_turns),
                "pattern_ids": list(
                    dict.fromkeys(
                        row.matched_pattern_id
                        for row in matched_turns
                        if row.matched_pattern_id
                    )
                ),
            },
            explanation=(
                f"Pragmatics resolved {len(matched_turns)} customer turns "
                f"(dialect={dialect}, top_intent={top_intent})."
            ),
        )

    def to_dict(self, result: PragmaticsResult) -> dict[str, Any]:
        return {
            "status": result.status,
            "dialect": result.dialect,
            "explanation": result.explanation,
            "summary": result.summary,
            "timeline": result.timeline,
            "turns": [
                {
                    "turn_index": turn.turn_index,
                    "text": turn.text,
                    "dialect": turn.dialect,
                    "matched_pattern_id": turn.matched_pattern_id,
                    "intent_probability": turn.intent_probability,
                    "emotion_probability": turn.emotion_probability,
                    "hidden_meaning": turn.hidden_meaning,
                    "buying_probability": turn.buying_probability,
                    "exit_risk": turn.exit_risk,
                    "confidence": turn.confidence,
                    "evidence_quote": turn.evidence_quote,
                    "context_window": turn.context_window,
                    "status": turn.status,
                }
                for turn in result.turns
            ],
        }
