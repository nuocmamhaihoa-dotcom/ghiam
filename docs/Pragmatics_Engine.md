# Vietnamese Pragmatics Engine 2.0 (VPE)

## Mục tiêu

Hiểu cách nói thật của người Việt trong telesale: nói giảm nói tránh, tiếng lóng, vùng miền, và ý nghĩa ẩn — **không kết luận theo keyword đơn lẻ**. Mỗi lượt thoại trả về phân phối xác suất multi-intent kèm evidence.

## Vị trí pipeline

```
Audio → Whisper → Diarization → Normalize → Segment
→ Evidence Extract → Evidence Verify → Rule Engine
→ Judge Ensemble → Pragmatics (VPE 2.0) → Root Cause
→ Coaching → Revenue Leak → Memory Graph → Dashboard → JSON
```

## Kiến trúc

```
backend/pragmatics/
  context_memory.py      # ±5..10 turns
  dialect.py             # north / central / south
  pattern_library.py     # priors đa intent
  intent_resolver.py     # blend pattern + context
  detectors.py           # hidden meaning / buying / exit / emotion
  engine.py              # VietnamesePragmaticsEngine
  factory.py             # dataset factory + quality gate mỗi 500 mẫu
models/pragmatics/       # vpe2_pattern_prior.json
datasets/pragmatics/     # 100k utterances + specials
tests/pragmatics/        # >2000 corpus tests
```

## Output bắt buộc

- Hidden Meaning
- Intent Probability (multi-intent)
- Emotion Probability
- Buying Probability
- Exit Risk
- Confidence Score
- Evidence (quote + pattern_id + context window)

Khi không match được pattern có evidence → `Insufficient Evidence`.

## API

`POST /v1/pragmatics/analyze`

```json
{
  "turns": [
    {"speaker": "agent", "text": "Em chào anh"},
    {"speaker": "customer", "text": "Để em coi đã"}
  ],
  "dialect_hint": "south"
}
```

Response gồm `status`, `dialect`, `summary`, `timeline`, `intent_evolution`, `emotion_evolution`, `turns[]`, `intents`, `objections`.

## Dataset factory

```bash
PYTHONPATH=backend python -m pragmatics.factory           # full
PYTHONPATH=backend python -m pragmatics.factory --quick   # smoke
```

Targets:

| Artifact | Count |
|----------|------:|
| Utterances | 100,000 |
| Situations | 5,000 |
| Fake agreement | 1,000 |
| Soft refusal | 1,000 |
| Topic shift | 500 |
| Exit imminent | 500 |

Quality gate **mỗi 500 mẫu**: duplicate check, dialect balance (≥20% north/central/south), intent balance, pragmatics accuracy (≥70% top-3), JSON validation.

## Dashboard

`/pragmatics` — Pragmatics Timeline, Hidden Meaning, Intent Evolution, Emotion Evolution.

## Tests

```bash
PYTHONPATH=backend pytest tests/pragmatics -q
```

>2000 case thực tế (fixtures_2100.jsonl) + factory gate tests.
