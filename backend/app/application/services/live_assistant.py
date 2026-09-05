"""Live Call Assistant — next-best actions under 2s target latency."""

from __future__ import annotations

import time
from typing import Any


class LiveCallAssistant:
    BUYING_CUES = ("giao khi nào", "bảo hành", "thanh toán", "bao nhiêu", "ship", "hóa đơn")
    OBJECTION_CUES = ("đắt", "mắc", "để sau", "đang bận", "bên kia", "không tin", "hỏi vợ", "hỏi chồng")
    EXIT_CUES = ("gác máy", "không cần", "thôi", "đừng gọi")

    def suggest(self, turns: list[dict[str, Any]], *, now_ts: float | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        if not turns:
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: no live transcript turns.",
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }

        last = turns[-1]
        last_text = str(last.get("text") or "").lower()
        speaker = str(last.get("speaker") or last.get("role") or "").lower()
        alerts: list[dict[str, Any]] = []
        next_question = None
        next_response = None
        closing_opportunity = False

        if any(c in last_text for c in self.BUYING_CUES):
            alerts.append({"type": "buying_signal", "severity": "high", "text": last.get("text")})
            next_response = "Dạ em ghi nhận. Em chốt giúp lịch giao / phương thức thanh toán luôn cho anh/chị nhé?"
            closing_opportunity = True
        if any(c in last_text for c in self.OBJECTION_CUES):
            alerts.append({"type": "objection", "severity": "high", "text": last.get("text")})
            next_response = next_response or (
                "Em hiểu điểm anh/chị đang cân nhắc. Cho em hỏi thêm nguyên nhân chính để em xử lý đúng chỗ ạ?"
            )
        if any(c in last_text for c in self.EXIT_CUES):
            alerts.append({"type": "exit_intent", "severity": "critical", "text": last.get("text")})

        # Silence alert if last turn ended > 4s ago and agent has not spoken
        end = float(last.get("end") or last.get("ts_end") or 0)
        ref = now_ts if now_ts is not None else end
        if ref - end >= 4 and speaker in {"customer", "client", "khach", "khách"}:
            alerts.append({"type": "silence", "severity": "medium", "seconds": round(ref - end, 2)})
            next_question = "Anh/chị đang nghe máy không ạ? Em xin phép hỏi thêm một ý để tư vấn sát hơn."

        if next_question is None:
            next_question = "Hiện anh/chị đang quan tâm nhất điều gì để em tư vấn đúng nhu cầu ạ?"
        if next_response is None and speaker in {"customer", "client", "khach", "khách"}:
            next_response = "Dạ em hiểu. Em tóm lại nhu cầu vừa rồi và đề xuất phương án phù hợp nhất nhé."

        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "ok",
            "next_best_question": next_question,
            "next_best_response": next_response,
            "alerts": alerts,
            "closing_opportunity": closing_opportunity,
            "latency_ms": latency_ms,
            "sla_ok": latency_ms < 2000,
            "evidence_turn_index": len(turns) - 1,
            "evidence_text": last.get("text"),
        }
