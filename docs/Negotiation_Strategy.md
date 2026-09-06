# Negotiation Strategy Engine

AI chiến lược đàm phán — dự đoán **3–5 bước tiếp theo** của khách và sinh nhiều phương án ứng xử.

## Mục tiêu

- Không chỉ trả lời câu hiện tại
- Dự đoán quỹ đạo đàm phán
- Sinh đa chiến lược kèm win/risk/script

## Dự đoán

Mỗi lần analyze, AI trả về:

| Field | Ý nghĩa |
|-------|---------|
| Next Question | Câu hỏi khách có thể hỏi tiếp |
| Next Objection | Phản đối kế tiếp |
| Next Emotion | Cảm xúc kế tiếp |
| Exit Risk | Rủi ro thoát cuộc gọi |
| Buy Probability | Xác suất mua |
| Horizon | 3–5 bước tiếp theo |

## Strategy Engine

Với utterance như `"Đắt quá"`, engine sinh tối thiểu:

1. Empathy
2. Value
3. Comparison
4. Urgency
5. Clarify

Mỗi chiến lược có:

- Win Probability
- Risk Score
- Recommended Script
- Forbidden Script
- Evidence

## Strategy Graph

**Không dùng decision tree tĩnh.**

Engine dựng **Strategy Graph** động:

- Node gốc = trạng thái objection hiện tại
- Mỗi strategy = nhánh
- Mỗi nhánh có outcome: accept / reobject / exit
- Evolution log theo thời gian

## Dashboard

- Win Probability
- Next Best Action
- Negotiation Timeline
- Strategy Evolution

## API

Prefix: `/v1/negotiation`

- `POST /analyze`
- `POST /predict`
- `POST /compare`
- `GET /dashboard`
- `GET /quality`

## Quality Gate

1. Prediction Accuracy
2. Strategy Consistency
3. Evidence Validation
4. Confidence Stability

Hard rule: `static_tree_forbidden = true`.

## Layout

```
backend/negotiation/
models/negotiation/
datasets/negotiation/
docs/Negotiation_Strategy.md
tests/negotiation/
enterprise-web/src/app/negotiation/
```

## Integration

Module gắn vào AI Sales Operating System — cung cấp next-best negotiation action cho live coaching / roleplay / autonomous agents.
