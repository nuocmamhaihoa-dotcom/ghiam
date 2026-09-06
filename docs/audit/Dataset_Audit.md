# Dataset / VECD Audit (Phase 6)

Generated: `2026-09-06T01:17:23.409312+00:00`  
Evidence: `phase_multi_evidence.json#datasets`

- Exists: `True`
- Subdirs: `['agents', 'audio_engine', 'autonomous', 'buying_signals', 'cltv', 'conversations', 'customers', 'digital_twin', 'emotions', 'golden_calls', 'intents', 'negotiation', 'objections', 'pragmatics', 'qa_benchmark', 'reports', 'root_causes', 'scripts', 'self_learning', 'sql', 'synthetic', 'validation', 'war_room']`
- Balance proxy:
```json
{
  "intents": {
    "file_count": 2,
    "sample": [
      "datasets/intents/intents_1000.csv",
      "datasets/intents/intents_1000.jsonl"
    ]
  },
  "emotions": {
    "file_count": 2,
    "sample": [
      "datasets/emotions/emotions_500.jsonl",
      "datasets/emotions/emotions_500.csv"
    ]
  },
  "conversations": {
    "file_count": 2,
    "sample": [
      "datasets/conversations/conversations_10000.jsonl",
      "datasets/conversations/conversations_10000.csv"
    ]
  },
  "synthetic": {
    "file_count": 2,
    "sample": [
      "datasets/synthetic/sop_50.jsonl",
      "datasets/synthetic/rules_1000.jsonl"
    ]
  },
  "qa_benchmark": {
    "file_count": 2,
    "sample": [
      "datasets/qa_benchmark/qa_benchmark_10000.jsonl",
      "datasets/qa_benchmark/qa_benchmark_10000.csv"
    ]
  },
  "golden_calls": {
    "file_count": 2,
    "sample": [
      "datasets/golden_calls/golden_calls_500.csv",
      "datasets/golden_calls/golden_calls_500.jsonl"
    ]
  }
}
```

Gap: no CI balance gate → ISS-012 Planned.

## Dataset Integrity Score: **74/100**
