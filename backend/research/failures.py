"""Failure pattern discovery."""
from __future__ import annotations

from typing import Any

from research.proposals_util import make_proposal
from self_learning.types import Evidence, Proposal, ProposalKind

FAILURE_RULES = [
    ("early_price", ("bao gia", "gia la", "gia cua"), "Báo giá quá sớm", "Nhảy giá trước khi discovery đủ."),
    ("talk_over", ("ngat loi", "de toi noi", "nghe toi"), "Ngắt lời khách", "Agent nói đè — mất rapport."),
    ("drop_minute_2", ("cuoc goi ngan", "mat khach"), "Mất khách phút 2", "Không giữ được attention đầu cuộc gọi."),
    ("no_next_step", ("de em nghi", "goi lai sau"), "Không chốt next-step", "Kết thúc mơ hồ — không lịch follow-up."),
]


class FailurePatternDiscovery:
    def discover(self, calls: list[dict[str, Any]]) -> list[Proposal]:
        buckets: dict[str, list[dict[str, Any]]] = {k: [] for k, *_ in FAILURE_RULES}
        for call in calls:
            text = str(call.get("transcript") or call.get("summary") or "").lower()
            outcome = str(call.get("outcome") or "")
            duration = float(call.get("duration_sec") or call.get("duration") or 0)
            closed = bool(call.get("closed") or outcome in {"won", "closed", "chot"})
            if duration and duration < 120 and not closed:
                buckets["drop_minute_2"].append(call)
            for key, needles, *_ in FAILURE_RULES:
                if key == "drop_minute_2":
                    continue
                if any(n in text for n in needles) and not closed:
                    buckets[key].append(call)
            if "de em nghi" in text or "goi lai sau" in text:
                if not call.get("next_step"):
                    buckets["no_next_step"].append(call)
        out: list[Proposal] = []
        meta = {k: (title, root) for k, _, title, root in FAILURE_RULES}
        for key, calls_hit in buckets.items():
            if len(calls_hit) < 1:
                continue
            title, root = meta[key]
            evidence = [
                Evidence(
                    call_id=str(c.get("call_id") or c.get("id") or "unknown"),
                    quote=str(c.get("transcript") or c.get("summary") or key)[:180],
                )
                for c in calls_hit[:5]
            ]
            out.append(make_proposal(
                kind=ProposalKind.FAILURE_PATTERN,
                title=f"Failure Pattern: {title}",
                summary=f"Root cause: {root}. Xuất hiện trên {len(calls_hit)} cuộc gọi.",
                suggested_rule=f"FAILURE::{key.upper()}",
                suggested_coaching=f"Drill sửa lỗi «{title}»: {root}",
                suggested_sop_update=f"Thêm checkpoint chống «{title}» vào call flow sau QA.",
                evidence=evidence,
                confidence=min(0.95, 0.55 + 0.05 * len(calls_hit)),
                novelty=0.6,
                expected_impact="high",
                metadata={"failure_key": key, "count": len(calls_hit), "root_cause": root, "pending_qa": True},
            ))
        return out
