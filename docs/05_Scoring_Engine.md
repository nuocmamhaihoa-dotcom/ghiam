# 05 — Scoring Engine
## AI QA TELESALE ENTERPRISE

**Phiên bản:** 1.0.0  
**Vị trí pipeline:** Evidence Extraction → **Rule Engine** → **LLM Judge** → Root Cause  
**Hiến pháp:** Không suy diễn · Rulebook từ DB · Explainable · Insufficient Evidence

---

## 1. Mục tiêu

Scoring Engine chuyển evidence + Rulebook release thành scorecard giải thích được, có thể scale hàng triệu cuộc gọi, không hardcode business rules trong code/prompt.

### Goals
- Criterion-level verdict: `pass` | `fail` | `not_applicable` | `Insufficient Evidence`.
- Aggregate score có weight + auto-fail.
- Mọi item: `rule_id/version`, evidence spans (hoặc IE), `evaluated_at`, `confidence`, `scoring_path`.
- Deterministic-first; LLM Judge chỉ adjudicate khi rule yêu cầu và evidence pack hợp lệ.

### Non-goals
- LLM tự tạo tiêu chí mới.
- Điểm cảm tính không gắn rule.
- Silent default-to-pass khi thiếu data.

---

## 2. Actors

| Actor | Interaction |
|-------|-------------|
| `worker-score` | Chạy Rule Engine + LLM Judge |
| `RulebookRepository` | Load immutable release snapshot |
| `EvidenceRepository` | Load spans cho call |
| `LLM Gateway` | Judge evidence-bound |
| `QA Specialist` | Override qua Dispute API |
| `Calibration Job` | So khớp human labels |

---

## 3. Inputs / Outputs

### Input
```json
{
  "tenant_id": "tn_...",
  "call_id": "cl_...",
  "pipeline_run_id": "pr_...",
  "rulebook_release_id": "rr_...",
  "transcript_id": "tr_...",
  "evidence_span_ids": ["ev_..."],
  "call_metadata": {"sku": "SOAP-99K", "crm_outcome": "callback"},
  "ai_config": {
    "stt_min_avg_confidence": 0.65,
    "llm_judge_enabled": true
  }
}
```

### Output — Scorecard domain object
Xem `scorecards` + `score_items` (Database) và API analysis payload.

---

## 4. Architecture (Clean)

```
ScoreCallUseCase
  ├─ RulebookRepository.get_release_snapshot(release_id)
  ├─ EvidenceRepository.list_for_call(call_id)
  ├─ TranscriptRepository.get_stats(transcript_id)
  ├─ RuleEvaluatorRegistry (strategies by evaluator_type)
  ├─ LlmJudgePort (optional)
  ├─ ScoreAggregator
  ├─ ScorecardRepository.save(...)
  └─ AuditPort.record("score.generated", ...)
```

DI: evaluators đăng ký trong composition root; thêm type mới không sửa aggregator.

---

## 5. Scoring Pipeline Internals

```
1. Preflight gates
2. Load rule snapshot (N ≈ 1000)
3. Filter applicable rules (campaign, product, direction)
4. For each rule:
   a. Select candidate evidences by requirements
   b. If insufficient candidates → IE (unless N/A policy)
   c. Run evaluator_type strategy
   d. If needs_llm_adjudication → LLM Judge with bound evidence
   e. Emit ScoreItem
5. Aggregate + auto-fail
6. Persist + emit artifact JSON
```

### 5.1 Preflight gates
| Gate | Fail behavior |
|------|----------------|
| No release id / empty snapshot | Abort `RULEBOOK_MISSING` — **không** fallback |
| STT avg_confidence < config | Global degrade; required rules without high-conf evidence → IE |
| Zero turns | Call-level `Insufficient Evidence` |
| Redacted-only transcript | IE for content rules |

---

## 6. Rule Applicability

Từ `evaluator_config.applicability` trong DB:

```json
{
  "directions": ["outbound", "inbound"],
  "campaign_codes": ["SOAP-99K"],
  "product_lines": ["FMCG"],
  "require_metadata_keys": ["sku"]
}
```

Nếu không applicable → verdict `not_applicable` (không tính weight vào mẫu số, hoặc weight 0 — policy tenant trong config DB: `na_weight_mode`).

Thiếu metadata key bắt buộc → **`Insufficient Evidence`** (không N/A im lặng nếu rule `required_when_missing_meta=false`; mặc định IE để tránh lách).

---

## 7. Evidence Binding

Rule `evidence_requirements` (DB):

```json
{
  "min_spans": 1,
  "max_spans": 5,
  "speakers_allowed": ["agent"],
  "stage_keys": ["close"],
  "slots": ["price_amount"],
  "min_confidence": 0.7,
  "must_precede_slots": ["product_name"]
}
```

**Algorithm**
1. Filter spans by speaker, stage, slot, confidence.
2. Order by confidence desc, then time.
3. If `count < min_spans` → IE.
4. Optional temporal constraints (`must_precede_slots`) checked via slot timeline; fail constraint without spans → fail hoặc IE nếu slot trước cũng IE (policy: `upstream_ie_propagates=true` → IE).

---

## 8. Evaluator Strategies

### 8.1 `regex`
- Patterns trong `evaluator_config.patterns` (DB).
- Dùng trên quote đã normalize.
- Match → pass candidate; no match with enough text searched → fail; empty search space → IE.

### 8.2 `span_classifier`
- Gọi model classifier với label set từ config.
- Output label + confidence; map theo `positive_labels` / `negative_labels`.
- Below `min_confidence` → IE (không đoán).

### 8.3 `slot_filler`
- Extract slot values (price, qty, address…).
- Validate với `slot_schema` trong config (type, currency, min/max nếu có).
- Missing slot → IE hoặc fail tùy `missing_slot_verdict` **trong rule config DB**.

### 8.4 `composite`
- Tree AND/OR/NOT của sub-evaluators khai báo trong config.
- Short-circuit; nếu child IE và operator không resolve → IE.

### 8.5 `llm_judge`
- Không chạy đơn độc không có evidence pack (trừ rule `allow_absence_judgment=true` — hiếm, compliance negative evidence).
- Xem §9.

---

## 9. LLM Judge (Evidence-Bound)

### 9.1 Contract
```json
{
  "rule_code": "R-OBJ-PRICE-014",
  "rule_text": "<from DB description>",
  "decision_rubric": "<from evaluator_config.rubric>",
  "evidence_spans": [
    {"id": "ev_1", "quote": "...", "audio_ts_start": 120.1, "audio_ts_end": 128.4, "speaker": "customer"}
  ],
  "question": "Given ONLY these evidences, does the agent satisfy the rule? Reply verdict + cited evidence ids."
}
```

### 9.2 Hard constraints (prompt system — không chứa business thresholds)
- Chỉ được dùng evidence trong list.
- Verdict ∈ {pass, fail, Insufficient Evidence}.
- Phải cite ≥1 evidence id nếu pass/fail.
- Nếu không chắc / evidence không đủ → `Insufficient Evidence`.
- Cấm suy diễn ý định khách ngoài quote.

### 9.3 Validation of LLM output
- Schema validate.
- Nếu cite id không thuộc pack → reject → retry once → IE.
- Nếu `llm_judge_enabled=false` → criteria cần judge → IE với explanation `llm_judge_disabled`.

### 9.4 Models
`judge_model` ghi vào score_item; version pinned in release metadata.

---

## 10. Aggregation

### 10.1 Formula
Let items with verdict in {pass, fail} contribute:

\[
S = \frac{\sum_i w_i \cdot s_i}{\sum_i w_i}
\]

where `s_i=1` pass, `0` fail.  
IE và N/A: excluded from denominator **by default** (`aggregate_exclude_ie=true` in tenant config).  
Nếu `required=true` và IE → scorecard.result có thể `Insufficient Evidence` hoặc `needs_review` (config).

### 10.2 Auto-fail
Nếu bất kỳ item `auto_fail=true` và `verdict=fail` → `result=fail`, `auto_fail_triggered=true`.  
Nếu auto-fail rule là IE → không auto-fail giả; `result=Insufficient Evidence` hoặc `needs_review`.

### 10.3 Overall result matrix
| Condition | result |
|-----------|--------|
| Any auto-fail fail | `fail` |
| Required IE ratio ≥ threshold | `Insufficient Evidence` |
| S ≥ pass_threshold (DB) | `pass` |
| else | `fail` |

`pass_threshold` từ `tenant_ai_configs` hoặc campaign settings table — không hardcode 80 trong code.

---

## 11. Explainability Record

Mỗi `score_item.scoring_path` ví dụ:

```json
[
  {"step": "applicability", "at": "2026-09-05T10:08:20.001Z", "out": "applicable"},
  {"step": "evidence_select", "at": "2026-09-05T10:08:20.010Z", "candidates": 2},
  {"step": "slot_filler", "at": "2026-09-05T10:08:20.050Z", "slot": "price_amount", "value": "50000"},
  {"step": "llm_judge", "at": "2026-09-05T10:08:21.200Z", "verdict": "pass", "cited": ["ev_..."]}
]
```

UI timeline highlight từ evidence timestamps.

---

## 12. Idempotency & Versioning

- Re-score tạo `pipeline_run` mới; scorecard gắn run.
- Dashboard đọc latest finalized/non-superseded.
- Shadow scoring: parallel release_id ghi `scorecards` với `shadow=true` (column hoặc flag in JSON) — không ảnh hưởng agent-facing trừ khi publish.

---

## 13. Performance (scale millions)

| Technique | Detail |
|-----------|--------|
| Snapshot cache | Redis compressed release JSON |
| Batch evidence fetch | One query per call |
| Parallel rules | Chunk by category with async gather; CPU-bound regex in process pool |
| LLM batching | Only ambiguous rules; cap K per call (config) |
| Short-circuit | Auto-fail hard rules first |
| Time budget | Per-call judge budget; overflow → IE for remaining judge rules |

Target: p95 Rule Engine deterministic < 2s for 1000 rules; LLM add < 30s with K≤20.

---

## 14. Failure Modes

| Failure | Behavior |
|---------|----------|
| Snapshot cache corrupt | Reload PG; alert |
| Evaluator throws | Item IE + error breadcrumb in scoring_path; no crash whole call unless critical |
| LLM timeout | Retry 1; then IE |
| Clock skew | Use server UTC `evaluated_at` |
| Partial save | Transaction rollback scorecard |

---

## 15. Insufficient Evidence — Engine Rules

Produce IE when:
1. Evidence requirements unmet.
2. Classifier/slot confidence < min.
3. LLM refuses / invalid cite.
4. Upstream IE propagation.
5. Judge disabled but required.
6. Temporal prerequisite missing without decisive negative evidence.

**Never** map IE → mid score (0.5) unless tenant explicitly enables experimental mode (default off; audited).

---

## 16. Human Override Integration

Override creates:
- dispute resolution event
- new authoritative verdict on item (or revised scorecard version)
- audit before/after
- optional recalculate aggregate

Root Cause/Coaching may re-run (flag `recompute_insights=true`).

---

## 17. Calibration & Quality

- Table `calibration_labels` (human) vs engine verdicts.
- Metrics: precision/recall per rule_code, IE rate, Cohen's κ.
- Gate publish: no release if critical rules κ < tenant threshold.

---

## 18. Monitoring

- `score_items_total{verdict=}`
- `scoring_duration_seconds`
- `llm_judge_invocations`
- `llm_judge_reject_total`
- `rulebook_missing_total`
- Alert on sudden IE spike / auto-fail spike.

---

## 19. Audit

- `score.generated` (summary: release_id, overall, counts)
- `score.override`
- `score.shadow_completed`
Không cần audit từng 1000 items (volume); full items ở score_items + artifact S3.

---

## 20. Test Strategy

| Test | Assert |
|------|--------|
| Unit evaluators | Fixtures quotes → verdict |
| IE cases | Empty evidence → IE string exact |
| Auto-fail | Critical fail forces result |
| No hardcode | CI fails if rules/*.json imported in app code paths |
| Contract | ScoreItem schema |
| Load | 1000 rules × 10k calls soak |

---

## 21. Example End-to-End Item

**Rule** `R-PRICE-001` weight 1.4 auto_fail true  
**Evidence** quote `Thông tin bán 50k` @ 45.2–46.0s  
**Path** slot_filler → value 50000 → llm_judge confirm → pass  
**evaluated_at** `2026-09-05T10:08:21Z`  
**explanation** `Price confirmed before consent with evidence ev_…`

---

## 22. Document Control

Owner: ML Lead + Backend Lead · Depends: Rulebook, Database, API · Feeds: Root Cause, Coaching, Revenue Leak

**Hết Scoring Engine v1.0.0**
