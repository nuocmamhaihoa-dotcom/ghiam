# VECD Master Status

**Updated:** 2026-09-05T17:27:48.573602+00:00
**Overall gates:** PASS

## Counts

| Asset | Count |
|-------|------:|
| customers | 9057 |
| agents | 50000 |
| conversations | 10000 |
| intents | 1000 |
| objections | 5000 |
| emotions | 500 |
| buying_signals | 500 |
| root_causes | 500 |
| qa_benchmark | 10000 |
| golden_calls | 500 |

## Commands

```bash
python datasets/scripts/generate_dataset.py --seed 20260905
python datasets/scripts/validate_dataset.py
python datasets/scripts/export_postgres.py
```
