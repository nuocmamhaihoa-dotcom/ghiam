"""Objection Simulator — AI plays customer; scores role-play immediately."""

from __future__ import annotations

from typing import Any
import random


_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "price_too_high",
        "group": "price",
        "customer_line": "Đắt quá, bên kia rẻ hơn.",
        "hidden_meaning": "Chưa thấy giá trị tương xứng / đang so sánh.",
        "good_markers": ("giá trị", "so với", "mỗi ngày", "bảo hành", "hiệu quả"),
        "forbidden_markers": ("rẻ nhất", "tin em", "không đắt đâu"),
    },
    {
        "id": "trust_issue",
        "group": "trust",
        "customer_line": "Có lừa không? Công ty ở đâu?",
        "hidden_meaning": "Thiếu bằng chứng uy tín.",
        "good_markers": ("địa chỉ", "bảo hành", "hóa đơn", "hợp đồng", "fanpage"),
        "forbidden_markers": ("tin tưởng đi", "không sao đâu"),
    },
    {
        "id": "delay",
        "group": "delay",
        "customer_line": "Để hôm khác em xem lại.",
        "hidden_meaning": "Trì hoãn — có thể thiếu lý do mua ngay.",
        "good_markers": ("lịch", "giữ chỗ", "ưu đãi hết", "chốt giúp"),
        "forbidden_markers": ("thôi kệ", "gọi lại sau không cần"),
    },
    {
        "id": "decision_maker",
        "group": "authority",
        "customer_line": "Để hỏi vợ/chồng/sếp đã.",
        "hidden_meaning": "Thiếu decision maker trên cuộc gọi.",
        "good_markers": ("cùng nghe", "tóm tắt giúp", "nhóm quyết định"),
        "forbidden_markers": ("đừng hỏi", "tự quyết đi"),
    },
    {
        "id": "competitor",
        "group": "competitor",
        "customer_line": "Bên kia đang giảm mạnh hơn.",
        "hidden_meaning": "Competitor comparison — cần khác biệt hóa.",
        "good_markers": ("khác biệt", "gồm", "sau bán", "tổng chi phí"),
        "forbidden_markers": ("bên kia kém", "đừng mua bên đó"),
    },
]


class ObjectionSimulator:
    def list_scenarios(self, group: str | None = None) -> list[dict[str, Any]]:
        if not group:
            return list(_SCENARIOS)
        return [s for s in _SCENARIOS if s["group"] == group]

    def start(self, *, group: str | None = None, seed: int | None = None) -> dict[str, Any]:
        rng = random.Random(seed)
        pool = self.list_scenarios(group)
        if not pool:
            return {"status": "Insufficient Evidence", "explanation": f"No scenarios for group={group}"}
        scenario = rng.choice(pool)
        return {
            "status": "ok",
            "session_id": f"sim-{scenario['id']}-{seed if seed is not None else rng.randint(1, 10_000_000)}",
            "scenario": {
                "id": scenario["id"],
                "group": scenario["group"],
                "customer_line": scenario["customer_line"],
                "hidden_meaning": scenario["hidden_meaning"],
            },
            "instruction": "Hãy trả lời như tele-sales chuyên nghiệp. Tránh forbidden patterns.",
        }

    def grade(self, *, scenario_id: str, agent_reply: str) -> dict[str, Any]:
        scenario = next((s for s in _SCENARIOS if s["id"] == scenario_id), None)
        if scenario is None:
            return {"status": "Insufficient Evidence", "explanation": f"Unknown scenario_id={scenario_id}"}
        reply = (agent_reply or "").lower().strip()
        if not reply:
            return {
                "status": "fail",
                "score": 0,
                "explanation": "Empty reply — Insufficient practice evidence.",
                "forbidden_hit": [],
                "good_hit": [],
            }
        good_hit = [m for m in scenario["good_markers"] if m in reply]
        forbidden_hit = [m for m in scenario["forbidden_markers"] if m in reply]
        score = min(100, 40 + 15 * len(good_hit) - 25 * len(forbidden_hit))
        score = max(0, score)
        return {
            "status": "ok",
            "scenario_id": scenario_id,
            "score": score,
            "pass": score >= 70 and not forbidden_hit,
            "good_hit": good_hit,
            "forbidden_hit": forbidden_hit,
            "coaching": (
                "Tránh câu cấm và bổ sung value reframe + câu hỏi làm rõ."
                if forbidden_hit or score < 70
                else "Tốt — giữ cấu trúc: thừa nhận → làm rõ → giá trị → chốt nhẹ."
            ),
            "expected_good_markers": list(scenario["good_markers"]),
            "forbidden_markers": list(scenario["forbidden_markers"]),
        }
