# AI Digital Twin Salesperson

Module tạo **bản sao AI** của telesale xuất sắc, tích hợp vào AI Sales Operating System.

## Mục tiêu

- Học từ cuộc gọi **Golden / QA Approved / High Conversion** only
- Tái tạo phong cách bán hàng (không sao chép nguyên văn transcript)
- Cho phép nhân viên **roleplay** với Twin và nhận Similarity / Improvement / Coaching

## Hồ sơ Twin

Mỗi Digital Twin gồm:

| Field | Ý nghĩa |
|-------|---------|
| Twin ID | Định danh twin |
| Skill Profile | discovery, rapport, objection, closing, value, empathy, voice, question |
| Strength / Weakness Score | Điểm mạnh / điểm yếu suy ra từ profile |
| Voice Pattern | nhịp, pause, question rate, empathy markers |
| Conversation Style | warm / direct professional |
| Discovery / Closing Style | chiến lược hỏi & chốt |
| Objection Strategy | acknowledge → reframe → offer |
| Conversation DNA | question / closing / empathy / value sequences (đã trừu tượng hóa) |
| Confidence | độ tin cậy theo số call đủ điều kiện + QA score |

## Training Gate

**Allowed:** `golden`, `qa_approved`, `high_conversion`  
**Rejected:** failed / low-score / lost calls

Templates được **abstract** (PII redaction, rút gọn) — Twin **không** phát lại nguyên văn.

## Roleplay & Scoring

- AI đóng vai Twin
- Trainee luyện tập theo scenario
- Scores: **Similarity**, **Improvement**, **Coaching**, **Skill Gap**, **Top Differences**
- Near-verbatim copy của Twin bị **phạt** trên Similarity

## Dashboard

- Twin Similarity
- Skill Gap Index
- Progress
- Top Difference

## API

Prefix: `/v1/digital-twin`

- `POST /train`
- `GET /twins`, `GET /twins/{twin_id}`
- `POST /twins/{twin_id}/act`
- `POST /twins/{twin_id}/roleplay`
- `POST /twins/{twin_id}/similarity`
- `GET /dashboard`
- `GET /quality`

## Quality Gate

Checks:

1. Twin Accuracy
2. Style Consistency
3. Coaching Quality
4. Similarity Stability

`verbatim_cloning_blocked = true` là hard rule.

## Layout

```
backend/digital_twin/
models/digital_twin/
datasets/digital_twin/
docs/Digital_Twin.md
tests/digital_twin/
enterprise-web/src/app/digital-twin/
```
