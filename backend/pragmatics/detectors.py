"""Hidden meaning, buying signal, exit risk, emotion classifiers."""

from __future__ import annotations

from typing import Any

from pragmatics.context_memory import ContextWindow


class HiddenMeaningDetector:
    def detect(
        self,
        *,
        intents: dict[str, float],
        priors: list[str],
        window: ContextWindow,
    ) -> list[str]:
        meanings = list(priors)
        top = max(intents, key=intents.get) if intents else None
        if top == "delay" and any(
            c in window.joined_after for c in ("giao", "bảo hành", "thanh toán")
        ):
            meanings.append(
                "Sau câu hoãn, khách vẫn hỏi điều kiện mua → nghiêng về cần thông tin hơn là từ chối."
            )
        if top == "soft_agreement" and not any(
            c in window.joined_after for c in ("địa chỉ", "thanh toán", "đặt", "ký")
        ):
            meanings.append(
                "Đồng ý mềm nhưng không có hành động tiếp theo → kiểm tra fake agreement."
            )
        if top == "decision_maker_missing":
            meanings.append(
                "Thiếu decision maker — đề xuất lịch gọi có cả người quyết định."
            )
        # Deduplicate while preserving order
        return list(dict.fromkeys(meanings))


class BuyingSignalDetector:
    BUY_CUES = (
        "giao khi nào",
        "bao giờ giao",
        "thanh toán",
        "bảo hành",
        "ship",
        "ký hợp đồng",
        "đặt cọc",
        "xuống tiền",
        "mua luôn",
        "lấy cái",
    )

    def score(
        self,
        text: str,
        *,
        intents: dict[str, float],
        prior: float,
        window: ContextWindow,
    ) -> float:
        lowered = text.lower()
        cue_hits = sum(1 for c in self.BUY_CUES if c in lowered or c in window.joined_after)
        intent_boost = (
            intents.get("ready_to_buy", 0.0) * 0.55
            + intents.get("interested", 0.0) * 0.25
            + intents.get("negotiation", 0.0) * 0.10
        )
        score = 0.45 * prior + 0.35 * intent_boost + 0.05 * cue_hits
        if intents.get("real_refusal", 0.0) > 0.35 or intents.get("exit_imminent", 0.0) > 0.4:
            score *= 0.45
        return round(min(0.95, max(0.0, score)), 4)


class ExitRiskDetector:
    EXIT_CUES = (
        "gác máy",
        "cúp máy",
        "đừng gọi",
        "không cần nữa",
        "thôi nhé",
        "bận rồi",
        "stop",
    )

    def score(
        self,
        text: str,
        *,
        intents: dict[str, float],
        prior: float,
        window: ContextWindow,
    ) -> float:
        lowered = text.lower()
        cue_hits = sum(1 for c in self.EXIT_CUES if c in lowered or c in window.joined_after)
        intent_boost = (
            intents.get("exit_imminent", 0.0) * 0.5
            + intents.get("real_refusal", 0.0) * 0.25
            + intents.get("soft_rejection", 0.0) * 0.15
            + intents.get("fake_agreement", 0.0) * 0.12
        )
        score = 0.4 * prior + 0.4 * intent_boost + 0.06 * cue_hits
        if intents.get("ready_to_buy", 0.0) > 0.35:
            score *= 0.4
        return round(min(0.98, max(0.0, score)), 4)


class PragmaticClassifier:
    """Maps intent distribution → emotion probability."""

    EMOTION_MAP = {
        "interested": {"curious": 0.45, "positive": 0.35, "neutral": 0.20},
        "ready_to_buy": {"positive": 0.55, "eager": 0.30, "curious": 0.15},
        "price_concern": {"hesitant": 0.45, "concerned": 0.35, "neutral": 0.20},
        "trust_concern": {"concerned": 0.50, "hesitant": 0.30, "neutral": 0.20},
        "delay": {"hesitant": 0.40, "neutral": 0.40, "busy": 0.20},
        "soft_rejection": {"reluctant": 0.45, "neutral": 0.35, "negative": 0.20},
        "real_refusal": {"negative": 0.55, "reluctant": 0.30, "angry": 0.15},
        "fake_agreement": {"neutral": 0.40, "reluctant": 0.35, "dismissive": 0.25},
        "exit_imminent": {"dismissive": 0.40, "busy": 0.30, "negative": 0.30},
        "busy": {"busy": 0.60, "neutral": 0.25, "reluctant": 0.15},
        "decision_maker_missing": {"hesitant": 0.45, "neutral": 0.35, "curious": 0.20},
        "need_information": {"curious": 0.55, "neutral": 0.30, "hesitant": 0.15},
        "topic_shift": {"neutral": 0.50, "curious": 0.30, "hesitant": 0.20},
        "negotiation": {"assertive": 0.40, "interested": 0.35, "hesitant": 0.25},
        "soft_agreement": {"positive": 0.40, "neutral": 0.35, "curious": 0.25},
    }

    def emotions(self, intents: dict[str, float]) -> dict[str, float]:
        if not intents:
            return {}
        acc: dict[str, float] = {}
        for intent, weight in intents.items():
            mapping = self.EMOTION_MAP.get(intent) or {"neutral": 1.0}
            for emotion, share in mapping.items():
                acc[emotion] = acc.get(emotion, 0.0) + weight * share
        total = sum(acc.values()) or 1.0
        return {k: round(v / total, 4) for k, v in sorted(acc.items(), key=lambda x: -x[1])}
