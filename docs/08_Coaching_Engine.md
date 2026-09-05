# 08 — Coaching Engine

## Mục tiêu

Sinh coaching **có bằng chứng**, gắn rule + root cause + golden call benchmark. Không đưa lời khuyên chung chung.

## Input

- Scoring JSON (`score`, `stage_scores`, `violations`, `evidence`)
- Root cause graph
- Agent Conversation DNA
- Golden calls cùng industry/stage
- Rulebook coaching fields từ DB

## Output chuẩn

```json
{
  "coaching": {
    "top_errors": [
      {
        "rank": 1,
        "rule_id": "R-0312",
        "title": "Báo giá trước khi đủ discovery",
        "evidence_quote": "Giá bên em chỉ 3 triệu thôi ạ",
        "root_cause_code": "RC-0042",
        "fix": "Hoàn tất tối thiểu 3 câu discovery trước khi nêu giá"
      }
    ],
    "top_strengths": [],
    "golden_contrast": {
      "golden_call_id": "GOLD-000123",
      "good_span": "...",
      "bad_span": "..."
    },
    "drills": [
      {
        "type": "roleplay",
        "scenario": "Khách nói đắt quá — ngành BĐS",
        "success_criteria": ["đồng cảm", "hỏi ngân sách", "value trước giá"]
      }
    ],
    "retest_plan": {
      "due_in_days": 7,
      "focus_rules": ["R-0312", "R-0144"]
    },
    "status": "ok"
  }
}
```

Thiếu evidence → `Insufficient Evidence` (không bịa drill).

## Liên kết module

- Rule Engine cung cấp coaching template theo `rule_id`.
- Root Cause Engine cung cấp primary/secondary causes.
- Revenue Leak AI dùng cùng violations để ước tính impact.
- QA Calibration dùng human scores để tinh chỉnh ngưỡng drill.

## Scale

- Template versioned trong PostgreSQL.
- Cá nhân hóa theo DNA nhưng mọi recommendation phải cite evidence span.
- Export JSON cho Employee Portal + LMS.
