"""Customer Personality Engine — DISC, Big Five, buyer type, communication style."""

from __future__ import annotations

from typing import Any


_DISC_CUES: dict[str, tuple[str, ...]] = {
    "D": ("nhanh", "chốt", "giá", "ngay", "quyết", "không cần"),
    "I": ("thích", "hay", "vui", "chia sẻ", "mọi người", "review"),
    "S": ("yên tâm", "từ từ", "ổn định", "an toàn", "bảo hành", "lâu dài"),
    "C": ("chi tiết", "so sánh", "thông số", "hợp đồng", "chứng nhận", "rõ ràng"),
}

_BIG_FIVE_CUES: dict[str, tuple[str, ...]] = {
    "openness": ("mới", "thử", "công nghệ", "khác biệt"),
    "conscientiousness": ("kế hoạch", "checklist", "cam kết", "đúng hạn"),
    "extraversion": ("nói chuyện", "tư vấn thêm", "giới thiệu bạn"),
    "agreeableness": ("được", "cảm ơn", "ok", "đồng ý", "tin"),
    "neuroticism": ("lo", "sợ", "không chắc", "rủi ro", "lừa"),
}

_BUYER_TYPES = {
    "economic": "Buyer Type: Economic — nhạy giá, cần ROI rõ.",
    "relationship": "Buyer Type: Relationship — cần tin tưởng và social proof.",
    "technical": "Buyer Type: Technical — cần thông số và bằng chứng.",
    "status": "Buyer Type: Status — cần thương hiệu / uy tín.",
}


class PersonalityEngine:
    def analyze(self, turns: list[dict[str, Any]]) -> dict[str, Any]:
        customer_text = " ".join(
            str(t.get("text") or "")
            for t in turns
            if str(t.get("speaker") or t.get("role") or "").lower()
            in {"customer", "client", "khach", "khách"}
        ).lower()
        if not customer_text.strip():
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: no customer utterances for personality inference.",
                "personality_card": None,
            }

        disc_scores = {
            code: sum(1 for cue in cues if cue in customer_text) for code, cues in _DISC_CUES.items()
        }
        big_five = {
            trait: round(min(1.0, sum(1 for cue in cues if cue in customer_text) / max(len(cues), 1)), 3)
            for trait, cues in _BIG_FIVE_CUES.items()
        }
        dominant_disc = max(disc_scores, key=disc_scores.get) if any(disc_scores.values()) else None
        if dominant_disc is None:
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: personality cues below confidence threshold.",
                "personality_card": None,
                "disc_scores": disc_scores,
                "big_five": big_five,
            }

        if "giá" in customer_text or "rẻ" in customer_text:
            buyer = "economic"
        elif "bảo hành" in customer_text or "uy tín" in customer_text:
            buyer = "relationship"
        elif "thông số" in customer_text or "so sánh" in customer_text:
            buyer = "technical"
        else:
            buyer = "status" if dominant_disc == "I" else "relationship"

        style = {
            "D": "Trực tiếp, ngắn, dẫn về quyết định.",
            "I": "Ấm, kể chuyện, social proof.",
            "S": "Nhẹ nhàng, nhấn an toàn và đồng hành.",
            "C": "Chi tiết, có số liệu và điều khoản rõ.",
        }[dominant_disc]

        suggested = {
            "D": "Anh/chị ơi, để tiết kiệm thời gian em chốt đúng gói phù hợp nhu cầu chính trong 2 phút nhé.",
            "I": "Nhiều khách cũng thích vì dễ dùng và hay được khen — em gửi vài feedback thật cho anh/chị xem nhé.",
            "S": "Em hiểu anh/chị muốn chắc chắn. Bên em có bảo hành và hỗ trợ sau bán, anh/chị yên tâm dùng thử theo lộ trình.",
            "C": "Em gửi bảng so sánh thông số + điều khoản rõ ràng, anh/chị xem giúp em điểm nào còn cần làm rõ.",
        }[dominant_disc]

        forbidden = {
            "D": "Nói vòng vo, hỏi quá nhiều câu không liên quan quyết định.",
            "I": "Chỉ đọc thông số khô, không có câu chuyện / social proof.",
            "S": "Ép chốt gấp, tạo áp lực thời gian giả.",
            "C": "Hứa chung chung không có số liệu / điều khoản.",
        }[dominant_disc]

        confidence = round(min(0.95, 0.45 + 0.1 * disc_scores[dominant_disc]), 3)
        return {
            "status": "ok",
            "disc": {"scores": disc_scores, "dominant": dominant_disc},
            "big_five": big_five,
            "buyer_type": buyer,
            "buyer_type_note": _BUYER_TYPES[buyer],
            "communication_style": style,
            "confidence": confidence,
            "personality_card": {
                "disc": dominant_disc,
                "buyer_type": buyer,
                "style": style,
                "suggested_script": suggested,
                "forbidden_script": forbidden,
            },
            "evidence": {
                "customer_char_count": len(customer_text),
                "matched_disc_cues": disc_scores[dominant_disc],
            },
        }
