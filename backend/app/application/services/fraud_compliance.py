"""Fraud & Compliance AI — evidence-bound risk detection."""

from __future__ import annotations

from typing import Any


_RISK_PATTERNS: list[dict[str, Any]] = [
    {
        "code": "FRAUD_FALSE_PROMISE",
        "cues": ("cam kết 100%", "chắc chắn khỏi", "không rủi ro gì", "bảo hiểm mọi trường hợp"),
        "severity": "critical",
        "label": "Hứa sai / overclaim",
    },
    {
        "code": "COMPLIANCE_WRONG_PRICE",
        "cues": ("giá bí mật", "báo giá miệng thôi", "không cần hóa đơn"),
        "severity": "critical",
        "label": "Báo giá / chứng từ sai quy trình",
    },
    {
        "code": "COMPLIANCE_MISSING_DISCLOSURE",
        "cues": ("không cần tư vấn", "ký trước đi", "phí ẩn không sao"),
        "severity": "major",
        "label": "Thiếu tư vấn / thiếu công bố",
    },
    {
        "code": "LEGAL_PRESSURE",
        "cues": ("không mua là mất quyền", "bắt buộc phải", "phạt nếu không"),
        "severity": "major",
        "label": "Áp lực có rủi ro pháp lý",
    },
]


class FraudComplianceService:
    def scan(self, turns: list[dict[str, Any]]) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        for idx, turn in enumerate(turns):
            speaker = str(turn.get("speaker") or turn.get("role") or "").lower()
            if speaker not in {"agent", "employee", "nhan_vien", "nhân viên", "sales"}:
                continue
            text = str(turn.get("text") or "")
            low = text.lower()
            for pattern in _RISK_PATTERNS:
                hits = [c for c in pattern["cues"] if c in low]
                if not hits:
                    continue
                findings.append(
                    {
                        "code": pattern["code"],
                        "label": pattern["label"],
                        "severity": pattern["severity"],
                        "evidence": {
                            "turn_index": idx,
                            "text": text,
                            "matched_cues": hits,
                            "timestamp_start": turn.get("start") or turn.get("ts_start"),
                            "timestamp_end": turn.get("end") or turn.get("ts_end"),
                        },
                    }
                )
        if not turns:
            return {
                "status": "Insufficient Evidence",
                "explanation": "Insufficient Evidence: no turns to scan.",
                "findings": [],
                "risk_level": "unknown",
            }
        if not findings:
            return {
                "status": "ok",
                "findings": [],
                "risk_level": "low",
                "explanation": "No fraud/compliance cues matched with evidence.",
            }
        severities = {f["severity"] for f in findings}
        risk = "critical" if "critical" in severities else "major" if "major" in severities else "low"
        return {"status": "ok", "findings": findings, "risk_level": risk, "count": len(findings)}
