# Vietnamese Pragmatics Engine (VPE)

## Purpose

Interpret Vietnamese telesale utterances beyond keywords:

- Soft refusal vs delay vs real refusal
- Dialect variants (north / central / south)
- Hidden meaning with evidence quotes
- Buying probability and exit risk

## Pipeline position

```
Transcript → Semantic Segmentation → Evidence Extraction
→ VCIE → Pragmatics Engine → Rule Engine → Judge Ensemble
→ Root Cause → Coaching → Revenue Leak
```

## API

`POST /v1/pragmatics/analyze`

Request:

```json
{
  "turns": [
    {"speaker": "customer", "text": "Để em xem đã"},
    {"speaker": "customer", "text": "Bao giờ giao?"}
  ],
  "dialect_hint": "south"
}
```

Response includes:

- `status` (`ok` | `Insufficient Evidence`)
- `dialect`
- `summary` (top intent, avg buying/exit)
- `timeline`
- `turns[]` with intent/emotion probabilities, hidden meanings, evidence

## Evidence rule

If no pattern matches customer turns, return **Insufficient Evidence**. Do not invent intent.

## Dataset

`datasets/pragmatics/`

- `patterns.jsonl` — extensible pattern library
- `situations_500.jsonl` — labeled situations
- `generate_pragmatics.py` — batch factory (`--batch-size`, `--seed`, `--output`)

## Quality gate

After each batch:

1. Duplicate text check
2. Dialect balance
3. Intent balance
4. JSON schema validation
5. Engine smoke on 20 samples
