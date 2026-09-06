"""Auto coaching generator from proposals/patterns."""
from __future__ import annotations

from typing import Any

from research.proposals_util import make_proposal
from self_learning.types import Evidence, Proposal, ProposalKind


class CoachingGenerator:
    def from_proposal(self, source: Proposal | dict[str, Any]) -> Proposal:
        if isinstance(source, Proposal):
            title = source.title
            summary = source.summary
            evidence = list(source.evidence)
            confidence = source.confidence
            novelty = source.novelty
            source_id = source.proposal_id
            kind = source.kind.value if hasattr(source.kind, "value") else str(source.kind)
        else:
            title = str(source.get("title") or "pattern")
            summary = str(source.get("summary") or "")
            evidence = []
            confidence = float(source.get("confidence") or 0.7)
            novelty = float(source.get("novelty") or 0.5)
            source_id = str(source.get("proposal_id") or "src")
            kind = str(source.get("kind") or "pattern")
        good = (
            f"[Tốt] «Em hiểu anh/chị đang cân nhắc về {title}. "
            f"Cho em 20 giây tóm lại đúng nhu cầu rồi mình chốt bước tiếp nhé?»"
        )
        bad = f"[Xấu] «Không sao đâu, giá này rẻ lắm, chốt luôn đi» (ép / bỏ qua {kind})."
        checklist = [
            "Xác nhận cảm xúc khách (1 câu)",
            "Làm rõ nhu cầu / timeline (1 câu hỏi)",
            "Đưa 1 lợi ích cụ thể có evidence",
            "Đề xuất next-step rõ ràng",
        ]
        roleplay = (
            f"Roleplay 3 phút:\n1) Khách nói tình huống liên quan tới «{title}».\n"
            f"2) Agent phản hồi theo checklist.\n3) Coach chấm empathy/clarity/next-step."
        )
        return make_proposal(
            kind=ProposalKind.COACHING,
            title=f"Coaching Pack: {title[:80]}",
            summary=f"Gói coaching tự sinh từ {kind}: {summary}",
            suggested_rule=f"COACHING_PACK::{source_id}",
            suggested_coaching=roleplay + "\n\nChecklist:\n" + "\n".join(f"- {c}" for c in checklist)
            + f"\n\nGood:\n{good}\n\nBad:\n{bad}",
            suggested_sop_update="Gắn coaching pack vào onboarding / weekly drill sau QA.",
            evidence=evidence or [Evidence(call_id="generated", quote=title)],
            confidence=min(0.95, confidence + 0.05),
            novelty=novelty,
            expected_impact="medium",
            metadata={
                "source_proposal_id": source_id,
                "checklist": checklist,
                "good_example": good,
                "bad_example": bad,
                "roleplay": roleplay,
                "pending_qa": True,
            },
        )

    def from_many(self, sources: list[Proposal | dict[str, Any]]) -> list[Proposal]:
        return [self.from_proposal(s) for s in sources]
