"""New objection discovery."""
from __future__ import annotations

from research.lexicon import KNOWN_OBJECTIONS, normalize
from research.proposals_util import make_proposal
from self_learning.types import PatternHit, Proposal, ProposalKind


class ObjectionDiscovery:
    def discover(self, patterns: list[PatternHit]) -> list[Proposal]:
        out: list[Proposal] = []
        for p in patterns:
            if p.kind != "objection" and "objection" not in p.kind:
                # still catch ghost-month style phrases
                if not any(x in p.normalized for x in ("co hon", "thang bay", "tam linh", "de em")):
                    continue
            if p.normalized in KNOWN_OBJECTIONS and p.novelty < 0.45:
                continue
            meaning = self._meaning(p.normalized)
            handling = self._handling(p.normalized)
            out.append(make_proposal(
                kind=ProposalKind.OBJECTION,
                title=f"New Objection: {p.text[:60]}",
                summary=meaning,
                suggested_rule=f"OBJECTION::{normalize(p.text)[:48]}",
                suggested_coaching=handling,
                suggested_sop_update="Thêm objection + script xử lý vào Objection Playbook sau QA.",
                evidence=list(p.evidence),
                confidence=max(p.confidence, 0.7),
                novelty=max(p.novelty, 0.55),
                expected_impact="high",
                metadata={"pattern_id": p.pattern_id, "meaning": meaning, "handling": handling, "pending_qa": True},
            ))
        return out

    def _meaning(self, norm: str) -> str:
        if "co hon" in norm or "thang bay" in norm:
            return "Khách lấy lý do tâm linh/tháng cô hồn để trì hoãn quyết định mua."
        if any(x in norm for x in ("coi", "xem", "can nhac", "suy nghi")):
            return "Khách trì hoãn bằng cụm «để em coi/xem/cân nhắc» — soft no / need nurture."
        if any(x in norm for x in ("gia", "dat", "cao")):
            return "Phản đối giá — cần value stacking / ROI / so sánh gói."
        return "Phản đối mới — cần QA phân tích ngữ cảnh và chuẩn hóa cách xử lý."

    def _handling(self, norm: str) -> str:
        if "co hon" in norm or "thang bay" in norm:
            return "Đồng cảm → hỏi timeline sau ngày vía → giữ chỗ/ưu đãi hết hạn mềm → follow-up calendar."
        if any(x in norm for x in ("coi", "xem", "can nhac")):
            return "Không tranh luận. Hỏi tiêu chí quyết định + deadline. Đặt lịch gọi lại cụ thể."
        if any(x in norm for x in ("gia", "dat", "cao")):
            return "Tách giá khỏi giá trị. So sánh chi phí/ngày. Đưa case study + bảo chứng."
        return "Lắng nghe → xác nhận cảm xúc → hỏi nguyên nhân gốc → đề xuất next-step nhỏ."
