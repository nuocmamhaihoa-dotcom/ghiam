# VECD — Vietnamese Enterprise Conversation Dataset

> Canonical documentation for the enterprise conversation intelligence corpus.
> Generated/updated: `2026-09-05T15:27:12.500995+00:00`

## Purpose

VECD is the primary training, evaluation, and calibration asset for AI QA Telesale Enterprise.
It must never be random noise. All samples are produced by the Dataset Factory and must pass Quality Gates.

## Target volumes

| Asset | Target | Current |
|-------|-------:|--------:|
| Customer utterances | 100,000 | 100000 |
| Agent utterances | 50,000 | 50000 |
| Conversations | 10,000 | 10000 |
| Intents | 1,000 | 1000 |
| Objections | 5,000 | 5000 |
| Emotions | 500 | 500 |
| Buying signals | 500 | 500 |
| Root cause graphs | 500 | 500 |
| Golden calls | 500 | 500 |
| QA benchmark calls | 10,000 | 10000 |
| Rules | 1,000 | 1000 |
| SOPs | 50 | 50 |

## Directory layout

```
datasets/
  customers/
  agents/
  conversations/
  intents/
  objections/
  emotions/
  buying_signals/
  root_causes/
  golden_calls/
  qa_benchmark/
  synthetic/
  validation/
  scripts/
    generate_dataset.py   # Master entrypoint
    generate_vecd.py      # Factory implementation
    validate_dataset.py
    export_postgres.py
  reports/
docs/VECD.md              # this file
```

## Utterance schema (required)

- `id`
- `conversation_id`
- `speaker` (`customer` | `agent`)
- `text`
- `dialect` (`bac` | `trung` | `nam`)
- `industry`
- `stage`
- `intent`
- `emotion`
- `objection_type`
- `buying_signal`
- `confidence`
- `recommended_response`
- `root_cause_if_failed`

## Distribution constraints

- Dialect: Bắc 35% / Trung 20% / Nam 45% (±2%)
- Industries: 19 enterprise verticals (BĐS, Spa, Nha khoa, Giáo dục, Bảo hiểm, Ô tô, Mỹ phẩm, Gia dụng, Thực phẩm, Điện máy, Nội thất, Logistics, Du lịch, Tài chính, Fitness, Camera, Điện nước, Thiết bị y tế, Dịch vụ DN)
- Rulebook categories: Opening 100, Rapport 80, Discovery 150, Qualification 80, Presentation 120, Pricing 80, Objection 200, Closing 80, Voice 60, Compliance 50

## Quality Gates

After every 1,000 samples and at sprint end:

1. Duplicate Check
2. Intent Balance
3. Emotion Balance
4. Dialect Balance
5. Industry Balance
6. Rule Consistency
7. JSON Validation

Reports live in `datasets/reports/` (`qg_*.json`, `validation_report.json`, `vecd_summary.json`).

Latest validation: `all_ok=True`, files=None

## Commands

```bash
python datasets/scripts/generate_dataset.py
python datasets/scripts/validate_dataset.py
python datasets/scripts/export_postgres.py
```

## Governance

See `.cursor/rules/project-rules.mdc` (alwaysApply constitution).
