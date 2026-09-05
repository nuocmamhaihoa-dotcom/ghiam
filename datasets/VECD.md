# VECD — Vietnamese Enterprise Conversation Dataset

Generated: 2026-09-05T14:23:54.979969+00:00

## Counts

```json
{
  "customers": 100000,
  "agents": 50000,
  "conversations": 10000,
  "intents": 1000,
  "objections": 5000,
  "emotions": 500,
  "buying_signals": 500,
  "root_causes": 500,
  "golden_calls": 500,
  "qa_benchmark": 10000,
  "rules": 1000,
  "sops": 50
}
```

## Schema

Utterance required fields: id, conversation_id, speaker, text, dialect, industry, stage, intent, emotion, objection_type, buying_signal, confidence, recommended_response, root_cause_if_failed

Dialect mix: Bắc 35% / Trung 20% / Nam 45%

## Quality Gates

See `datasets/reports/qg_*.json`.

## Rulebook

`ai-brain/rulebook/rules_1000.jsonl` — exactly 1000 rules linked to coaching/root cause.
