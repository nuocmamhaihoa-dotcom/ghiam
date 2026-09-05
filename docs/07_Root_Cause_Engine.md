# 07 — Root Cause Engine

## Mục tiêu

Xác định **nguyên nhân gốc** thất bại/điểm thấp của cuộc gọi telesale từ evidence + rule hits, không suy diễn.

## Input

- `score`, `stage_scores`, `violations[]` (rule_id, evidence spans, timestamps)
- Transcript đã normalize + speaker tags
- Conversation DNA (lịch sử agent)
- Rulebook version + SOP industry

## Output chuẩn

```json
{
  "root_cause": {
    "primary_code": "RC-0042",
    "primary_label": "Poor Discovery → Early Pricing",
    "confidence": 0.81,
    "graph": [
      {"from": "No Sale", "to": "Weak Value"},
      {"from": "Weak Value", "to": "Early Pricing"},
      {"from": "Early Pricing", "to": "Poor Discovery"}
    ],
    "evidence": [
      {
        "rule_id": "R-0312",
        "quote": "Giá bên em chỉ 3 triệu thôi ạ",
        "audio_ts_start": 42.1,
        "audio_ts_end": 45.0
      }
    ],
    "secondary_causes": ["Ignored Objection", "No Closing Attempt"],
    "status": "ok"
  }
}
```

Khi thiếu evidence: `"status": "Insufficient Evidence"`.

## Thuật toán

1. Lọc violations có evidence hợp lệ (timestamp + quote + speaker).
2. Map rule_id → root_cause nodes (từ DB, không hardcode trong service).
3. Xây graph hướng (Bayesian/heuristic weights versioned).
4. Chọn primary path có confidence ≥ ngưỡng; nếu không → Insufficient Evidence.
5. Ghi audit: rule_version, graph_version, evaluated_at.

## Scale

- Graph catalog ≥ 500 nodes/paths (VECD root_causes).
- Batch scoring hàng triệu calls qua Redis queue workers.
- Index PostgreSQL: `(agent_id, primary_code, evaluated_at)`.
