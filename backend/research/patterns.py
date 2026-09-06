"""Novel pattern detection."""
from __future__ import annotations

from typing import Any

from research.lexicon import (
    KNOWN_BUYING_SIGNALS, KNOWN_CLOSINGS, KNOWN_EMOTIONS, KNOWN_OBJECTIONS,
    normalize, novelty_score,
)
from self_learning.types import Evidence, PatternHit, new_id

OBJECTION_HINTS = ("de em", "khong", "sau", "dat", "cao", "choi", "co hon", "thang")
BUY_HINTS = ("chuyen khoan", "dia chi", "lay", "dat", "chot", "ship", "bao nhieu", "stk")
CLOSE_HINTS = ("chot", "xac nhan", "gui hang", "thanh toan")
EMOTION_HINTS = {
    "tuc": "anger", "buc": "anger", "vui": "joy", "ok": "positive",
    "ngai": "hesitation", "so": "fear", "met": "fatigue", "do du": "hesitation",
}


class PatternDetector:
    def detect(self, call: dict[str, Any], *, novelty_threshold: float = 0.45) -> list[PatternHit]:
        call_id = str(call.get("call_id") or call.get("id") or "unknown")
        turns = call.get("turns") or call.get("transcript") or []
        hits: list[PatternHit] = []
        if isinstance(turns, str):
            turns = [{"speaker": "customer", "text": turns}]
        for turn in turns:
            speaker = str(turn.get("speaker") or "customer").lower()
            text = str(turn.get("text") or "").strip()
            if not text:
                continue
            if speaker not in {"customer", "khach", "client", "user", "", "unknown"}:
                continue
            norm = normalize(text)
            kind = self._classify(norm)
            known_set = self._known_for(kind)
            novelty = novelty_score(norm, known_set)
            known = novelty < novelty_threshold
            if known and novelty < 0.25:
                continue
            conf = min(0.99, 0.55 + novelty * 0.4 + (0.1 if len(norm.split()) >= 4 else 0))
            hits.append(PatternHit(
                pattern_id=new_id("pat"), kind=kind, text=text, normalized=norm,
                novelty=round(novelty, 4), confidence=round(conf, 4),
                evidence=[Evidence(
                    call_id=call_id, quote=text, start_ms=turn.get("start_ms"),
                    end_ms=turn.get("end_ms"), speaker=speaker or "customer",
                    meta={"kind": kind},
                )],
                known=known,
            ))
        return hits

    def _classify(self, norm: str) -> str:
        for key, label in EMOTION_HINTS.items():
            if key in norm:
                return f"emotion:{label}"
        if any(h in norm for h in CLOSE_HINTS) and any(h in norm for h in BUY_HINTS):
            return "closing"
        if any(h in norm for h in BUY_HINTS):
            return "buying_signal"
        if any(h in norm for h in OBJECTION_HINTS):
            return "objection"
        return "phrase"

    def _known_for(self, kind: str) -> set[str]:
        if kind.startswith("emotion"):
            return KNOWN_EMOTIONS
        if kind == "closing":
            return KNOWN_CLOSINGS
        if kind == "buying_signal":
            return KNOWN_BUYING_SIGNALS
        if kind == "objection":
            return KNOWN_OBJECTIONS
        return KNOWN_OBJECTIONS | KNOWN_BUYING_SIGNALS | KNOWN_CLOSINGS
