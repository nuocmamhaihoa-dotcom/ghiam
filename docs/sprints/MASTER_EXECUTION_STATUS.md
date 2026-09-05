# Master Execution Status

**Updated:** 2026-09-05T15:27:12.500995+00:00  
**Constitution:** `.cursor/rules/project-rules.mdc` (alwaysApply=true)  
**VECD docs:** `docs/VECD.md`  
**Validation:** all_ok=True files=None

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

## Sprint Mode

All Sprint 1–9 reports generated under `docs/sprints/`.

## Commands

```bash
python datasets/scripts/generate_dataset.py
python datasets/scripts/validate_dataset.py
python datasets/scripts/export_postgres.py
```
