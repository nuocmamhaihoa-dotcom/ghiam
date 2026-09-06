# 20 — Dataset Builder (VECD Factory)
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Version | `1.0.0` |
| Status | Enterprise — implementable |
| Asset name | **VECD** — Vietnamese Enterprise Conversation Dataset |
| Constitution | Factory-only generation · Quality Gates · No random noise · Schema-complete |
| Scripts | `datasets/scripts/generate_dataset.py` · `validate_dataset.py` · `export_postgres.py` |

---

## 1. Purpose

Dataset Builder is the **governed factory** that produces VECD — the platform’s primary corpus for:

- Rule Engine evaluation fixtures
- AI Judge Ensemble regression
- Root Cause / Coaching / Revenue Leak offline tests
- Calibration holdouts & Golden seeds
- Dialect / industry balance research (VCIE)

**Forbidden:** hand-pasting ad-hoc JSON into production paths; generating utterances without schema validation; shipping unbalanced dialect mixes.

---

## 2. Target volumes (platform reference)

| Asset | Path | Target |
|-------|------|-------:|
| Customer utterances | `datasets/customers/` | 100,000 |
| Agent utterances | `datasets/agents/` | 50,000 |
| Conversations | `datasets/conversations/` | 10,000 |
| Objections | `datasets/objections/` | 5,000 |
| Intents | `datasets/intents/` | 1,000 |
| Buying signals | `datasets/buying_signals/` | 500 |
| Emotion patterns | `datasets/emotions/` | 500 |
| Root cause graphs | `datasets/root_causes/` | 500 |
| Golden calls | `datasets/golden_calls/` | 500 |
| QA benchmark calls | `datasets/qa_benchmark/` | 10,000 |
| Rules (seed) | `ai-brain/rulebook/` + DB | 1,000 |
| SOPs | `ai-brain/sop/` | 50 |

Dialect mix (utterances): **Bắc 35% / Trung 20% / Nam 45%** (±2 pp Quality Gate).

---

## 3. Directory layout (canonical)

```text
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
    schema.json
  scripts/
    generate_dataset.py      # Master entry
    generate_vecd.py         # Factory implementation
    validate_dataset.py
    export_postgres.py
  reports/
    qg_*.json
    validation_report.json
    vecd_summary.json
docs/
  VECD.md
  20_Dataset_Builder.md      # this file
```

---

## 4. Utterance schema (required — zero optional core fields)

Every utterance record **must** include:

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Stable ULID/UUID |
| `conversation_id` | string | FK logical |
| `speaker` | `customer` \| `agent` | |
| `text` | string | Vietnamese natural language |
| `dialect` | `bac` \| `trung` \| `nam` | |
| `industry` | enum | 19 industries |
| `stage` | enum | opening…compliance |
| `intent` | string | From intent library |
| `emotion` | string | From emotion library |
| `objection_type` | string \| null | |
| `buying_signal` | string \| null | |
| `confidence` | number 0–1 | Generator confidence, not model hallucination |
| `recommended_response` | string \| null | Agent-side coaching hint |
| `root_cause_if_failed` | string \| null | Link to RC code |

Validation fails hard if any required field missing.

### Industry enum (19)

`bds`, `spa`, `nha_khoa`, `giao_duc`, `bao_hiem`, `o_to`, `my_pham`, `gia_dung`, `thuc_pham`, `dien_may`, `noi_that`, `logistics`, `du_lich`, `tai_chinh`, `fitness`, `camera`, `dien_nuoc`, `thiet_bi_y_te`, `dich_vu_dn`

---

## 5. Factory architecture

```text
DatasetFactoryCLI
  ├─ SeedBank (dialect phrase banks, industry lexicons)  # data files, not business rules
  ├─ CombinatorialExpander (controlled templates + slots)
  ├─ ConversationComposer (8–30 turns, outcome kinds)
  ├─ LibraryBuilders (intent/emotion/objection/buying/root_cause)
  ├─ GoldenCurator (freeze + hash)
  ├─ QaBenchmarkSynthesizer (ai_score, human_score, difference, reviewer, reason)
  ├─ QualityGateRunner (every 1,000 rows + end of job)
  ├─ JsonlWriter
  └─ PostgresExporter (optional)
```

Clean Architecture mapping:

- **Domain:** schema entities, balance policies, gate rules (loaded from `validation/schema.json` + gate config JSON in DB/files — not hardcoded magic numbers in random places).
- **Application:** generate / validate / export use-cases.
- **Infrastructure:** filesystem JSONL, Postgres copy, hashing.
- **Interface:** CLI scripts.

Business scoring rules are **not** invented here; Rulebook seed is produced as data that must be imported into PostgreSQL before runtime use.

---

## 6. Generation principles

1. **Deterministic seed** (`SEED` env / flag) for reproducible corpora.
2. **Dialect authenticity:** separate phrase banks for Bắc / Trung / Nam; no “Southern words randomly sprinkled”.
3. **Industry slots:** product nouns, price patterns, compliance phrases per industry SOP.
4. **Conversation kinds:** success, fail, hot lead, cold lead, early pricing, strong discovery, weak discovery, angry customer, friendly customer.
5. **Objection packs:** price, time, trust, decision-maker, competitor — with hidden meaning, root cause, good/forbidden responses, practice exercise.
6. **No PII:** synthetic names/phones only from faker pools marked synthetic.

---

## 7. Quality Gates (mandatory)

Run after every **1,000** new samples and at job end:

| Gate | Check |
|------|-------|
| Duplicate Check | Exact + near-duplicate (normalized hash / MinHash) |
| Intent Balance | No intent > max_share (config) |
| Emotion Balance | Coverage of all emotion labels |
| Dialect Balance | 35/20/45 ±2 pp |
| Industry Balance | Chi-square / max-min ratio within SLO |
| Rule Consistency | Seed rules category counts match 100/80/150/80/120/80/200/80/60/50 |
| JSON Validation | JSON Schema draft against `validation/schema.json` |

On failure: **auto-repair** (regenerate deficit cells, drop duplicates) → re-gate. Job exits non-zero if still failing.

Reports: `datasets/reports/qg_<asset>.json`.

---

## 8. Export to PostgreSQL

`export_postgres.py`:

1. Upsert libraries → `intents`, `emotions`, `buying_signals`, `objection_types`, `root_cause_nodes`.
2. Insert conversations + utterances into staging tables.
3. Optionally promote Golden / QA Benchmark into calibration tables (`18_Calibration.md`, `17_Golden_Call.md`).
4. Import Rulebook seed via Rulebook admin import API (versioned), never overwrite `published` silently.
5. Write audit rows: `dataset_export` with corpus hash + counts.

Idempotent: content_hash short-circuits unchanged files.

---

## 9. CLI contracts

```bash
# Generate (or regenerate) full VECD
python datasets/scripts/generate_dataset.py

# Validate only
python datasets/scripts/validate_dataset.py

# Export
DATABASE_URL=postgres://... python datasets/scripts/export_postgres.py
```

Exit codes: `0` pass · `1` gate fail · `2` IO/config error.

---

## 10. Integration with AI pipeline

| Pipeline stage | Uses VECD for |
|----------------|---------------|
| Semantic Segmentation | Stage-labeled turns |
| Evidence Extraction | Span fixtures |
| Rule Engine | Rule hit unit tests |
| AI Judge Ensemble | Adjudication fixtures |
| Root Cause Graph | Graph templates |
| Coaching AI | Good/bad response pairs |
| Revenue Leak AI | Outcome + cause labeled calls |
| Calibration | QA benchmark 10k |
| Golden Call | Frozen 500 |

---

## 11. Security

- Synthetic data only in public git; real customer audio never committed.
- Export credentials via env / secret manager.
- RBAC `dataset:generate`, `dataset:export`, `dataset:publish_golden`.

---

## 12. Quality Gates for this module (engineering)

| ID | Gate |
|----|------|
| QG-DB-01 | `validate_dataset.py` all_ok true in CI |
| QG-DB-02 | Dialect ratios within tolerance |
| QG-DB-03 | Schema fields complete on sample audit (1% scan) |
| QG-DB-04 | No TODO/placeholder strings in generated text (`lorem`, `TODO`, `xxx`) |
| QG-DB-05 | Docs `docs/VECD.md` counts match `vecd_summary.json` |

---

## 13. Sprint mapping

| Sprint | Builder scope |
|--------|----------------|
| 1 | Schema + scripts + validation |
| 2 | Customers 100k |
| 3 | Agents 50k |
| 4 | Intent / Emotion / Buying |
| 5 | Objection / Root Cause |
| 6 | Conversations 10k |
| 7 | QA Benchmark 10k |
| 8 | Golden 500 |
| 9 | Wire export → Revenue Leak / Coaching / DNA fixtures |

Each sprint ends with Review → Refactor → Quality Gate (Constitution §5).

---

## 14. Implementation checklist

- [x] Directory layout
- [x] Generator + validator + export scripts
- [x] Volume targets materialized (see `datasets/reports/vecd_summary.json`)
- [ ] CI job: validate on PR when `datasets/**` changes
- [ ] Admin UI: trigger export + view gate reports
- [ ] Prometheus metrics: `vecd_gate_fail_total`, `vecd_rows_generated`

---

## 15. References

- Constitution: `.cursor/rules/project-rules.mdc`
- `docs/VECD.md`
- `docs/09_VCIE.md`
- `docs/06_Rulebook_1000.md`
- `docs/17_Golden_Call.md`
- `docs/18_Calibration.md`
