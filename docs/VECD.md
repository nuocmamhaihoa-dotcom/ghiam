# VECD — Vietnamese Enterprise Conversation Dataset

> Canonical documentation for the enterprise conversation intelligence corpus.
> Updated: `2026-09-05`

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
    generate_dataset.py
    generate_vecd.py
    validate_dataset.py
    export_postgres.py
  reports/sprints/
  sql/
docs/VECD.md
scripts/                  # repo-root wrappers
  generate_dataset.py
  validate_dataset.py
  export_postgres.py
```

## Utterance schema (required)

```json
{
  "id": "VECD-C-000001",
  "conversation_id": "CONV-000001",
  "speaker": "customer",
  "text": "Để em coi đã.",
  "dialect": "south",
  "industry": "real_estate",
  "stage": "objection",
  "intent": "delay",
  "emotion": "hesitation",
  "objection_type": "need_time",
  "buying_signal": false,
  "confidence": 0.93,
  "recommended_response": "Dạ, điều anh còn phân vân nhất là giá hay tính năng ạ?",
  "root_cause_if_failed": "Agent did not explore delay."
}
```

## Distribution constraints

- Dialect: north 35% / central 20% / south 45% (±3%)
- Industries: **20** verticals — real_estate, spa, dental, insurance, education, automotive, cosmetics, home_appliances, food, electronics, furniture, logistics, travel, finance, fitness, plumbing, repair, camera, medical_devices, b2b_services
- Stages: opening → rapport → discovery → qualification → presentation → pricing → objection → closing → follow_up

## Dataset strategy

1. Factory generates deterministic corpora via `--seed`
2. Batch mode via `--batch-size` / `--only <collection>`
3. Dual output: JSONL + CSV per collection
4. SQL seed sample in `datasets/sql/vecd_seed.sql`
5. Sprint reports after each gate in `datasets/reports/sprints/`

## Validation strategy

After generation (and after every ~1000 utterance samples in QG):

1. Duplicate ID / text check
2. Required schema fields
3. Dialect balance
4. Industry coverage (≥20)
5. Intent / emotion / objection library integrity
6. Golden call immutability (`immutable=true` + checksum)
7. QA calibration fields (ai_score, human_score, difference)

```bash
python datasets/scripts/validate_dataset.py
# or
python scripts/validate_dataset.py
```

## Expansion strategy

- Grow utterance banks per dialect×stage before increasing N
- Keep salt tags `[industry/stage/n]` only as uniqueness fallback
- Re-run QG after each batch; never merge failing batches into canonical counts
- Prefer regenerating a full collection over patching partial files

## Import guide (PostgreSQL)

```bash
python datasets/scripts/export_postgres.py
# writes datasets/sql/vecd_seed.sql
# if DATABASE_URL is set, loads full JSONL into vecd_* tables
```

Tables: `vecd_utterances`, `vecd_conversations`, `vecd_intents`, `vecd_objections`, `vecd_emotions`, `vecd_buying_signals`, `vecd_root_causes`, `vecd_qa_scores`, `vecd_golden_calls`.

## Versioning

- File naming: `{collection}_{count}.jsonl` / `.csv`
- Loaders always pick the **largest line-count** file for a prefix
- Golden calls are immutable benchmarks — do not rewrite checksummed payloads

## Quality metrics

- Dialect ratios within ±3pp of targets
- Zero duplicate IDs
- Customer/agent text duplicate rate ≤ 0.5%
- All sprint gates PASS (`datasets/reports/sprints/SPRINT_0{1-8}_VECD.json`)

## Commands

```bash
python datasets/scripts/generate_dataset.py --seed 20260905
python datasets/scripts/generate_dataset.py --only customers --batch-size 1000 --industry real_estate --dialect south
python datasets/scripts/validate_dataset.py
python datasets/scripts/export_postgres.py
```

## Sprint map

| Sprint | Deliverable |
|-------:|-------------|
| 1 | Schema + scripts + validation |
| 2 | 100k customer utterances |
| 3 | 50k agent utterances |
| 4 | Intents + emotions + buying signals |
| 5 | Objections + root causes |
| 6 | 10k conversations |
| 7 | 10k QA benchmark |
| 8 | 500 golden calls |
