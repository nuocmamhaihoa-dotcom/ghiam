# 16 — Revenue Leak AI
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Version | `1.0.0` |
| Status | Enterprise — implementable |
| Pipeline stage | After Coaching Engine → **Revenue Leak AI** → Dashboard |
| Constitution | Evidence + Timestamp · No inference · Rules from DB · Insufficient Evidence |
| Owners | Revenue Ops · ML Engineer · Backend Lead |

---

## 1. Purpose

Revenue Leak AI quantifies **recoverable lost revenue** attributable to measurable conversation failures (not vibes). Every leak estimate must bind to:

- scored call evidence + timestamps
- Rulebook `rule_id` / root-cause codes from DB
- a published **revenue impact model** version
- confidence + method path

If required inputs are missing → return **`Insufficient Evidence`** (never invent AOV, close probability, or “likely lost deals”).

### Goals

- Per-call leak estimate in VND (or tenant currency) with component breakdown.
- Aggregations: heatmap (hour×weekday), funnel stage drop, Pareto of leak causes, trend by team/SKU.
- Surfaces: Leader Gap, Product Bottleneck, Hourly Conversion, Top Recoverable Revenue.
- Deterministic math over published models; LLM may only **classify/label** when evidence pack is complete — never invent money.

### Non-goals

- Accounting-grade GL reconciliation (this is ops analytics, not finance close).
- Predicting future revenue without historical conversion priors in DB.
- Hardcoding AOV / conversion rates in application code.

---

## 2. Fixed pipeline position

```
… → AI Judge Ensemble → Root Cause Graph → Coaching AI
→ Revenue Leak AI → Dashboard → JSON Output
```

Revenue Leak AI **must not** run before Root Cause Graph completes (or explicitly marks `Insufficient Evidence`). Coaching may run in parallel **after** root cause, but leak estimation consumes root-cause + scorecard + CRM outcome fields when present.

---

## 3. Domain model

### 3.1 Entities

| Entity | Responsibility |
|--------|----------------|
| `RevenueImpactModel` | Versioned priors: AOV by SKU/industry, stage conversion, leak multipliers |
| `RevenueLeakEstimate` | Per-call estimate + components JSON |
| `RevenueLeakComponent` | One cause → amount, evidence refs, confidence |
| `RevenueLeakRollup` | Materialized aggregates for dashboards |

### 3.2 Canonical per-call output block

```json
{
  "revenue_leak": {
    "status": "ok",
    "currency": "VND",
    "estimated_total": 1250000,
    "model_id": "rim_01H...",
    "model_version": 3,
    "evaluated_at": "2026-09-05T14:22:01Z",
    "confidence": 0.78,
    "method": "root_cause_weighted_expected_value",
    "components": [
      {
        "cause_code": "RC_EARLY_PRICING",
        "rule_ids": ["R_PRC_012"],
        "label": "Báo giá sớm trước khi khám phá nhu cầu",
        "amount": 800000,
        "recoverable": true,
        "evidence_span_ids": ["ev_..."],
        "timestamps": [{"start_ms": 142000, "end_ms": 158000}],
        "confidence": 0.81
      }
    ],
    "crm_outcome": "lost",
    "notes": null
  }
}
```

Insufficient case:

```json
{
  "revenue_leak": {
    "status": "Insufficient Evidence",
    "currency": null,
    "estimated_total": null,
    "reason_codes": ["MISSING_AOV_PRIOR", "NO_ROOT_CAUSE"],
    "evaluated_at": "2026-09-05T14:22:01Z"
  }
}
```

---

## 4. Clean Architecture

```
EstimateRevenueLeakUseCase
  ├─ ScorecardRepository.get(call_id)
  ├─ RootCauseRepository.get(call_id)
  ├─ CallRepository.get_metadata(call_id)      # SKU, list_price, CRM outcome
  ├─ RevenueImpactModelRepository.get_active(tenant_id, industry, sku)
  ├─ LeakCalculator (pure domain service)
  ├─ RevenueLeakRepository.save(estimate)
  ├─ RollupProjector.enqueue(call_id)
  └─ AuditPort.record("revenue_leak.estimated", …)
```

- **Domain**: `LeakCalculator`, value objects `Money`, `LeakComponent`.
- **Application**: use-case orchestration.
- **Infrastructure**: Postgres repos, Redis rollup queue.
- **Interface**: FastAPI routers under `/v1/revenue-leak/*`.

DI: calculator strategies registered by `method` code from DB model, not `if/else` hardcodes in handlers.

---

## 5. Revenue Impact Model (Database-only)

Table sketch (see also `03_Database.md`):

```sql
CREATE TABLE revenue_impact_models (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  version         INT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('draft','published','retired')),
  currency        CHAR(3) NOT NULL DEFAULT 'VND',
  industry_code   TEXT,
  sku_code        TEXT,
  aov_cents       BIGINT NOT NULL,           -- prior average order value
  baseline_close_rate NUMERIC(6,5) NOT NULL, -- e.g. 0.12000
  stage_multipliers JSONB NOT NULL,         -- {"discovery":0.35,"pricing":0.55,...}
  cause_weights   JSONB NOT NULL,           -- {"RC_EARLY_PRICING":0.40,...}
  max_leak_cents  BIGINT,                   -- cap per call
  published_at    TIMESTAMPTZ,
  published_by    UUID,
  UNIQUE (tenant_id, version)
);
```

**Publishing rules**

- Only `published` models are readable by scoring workers.
- Publish requires RBAC `revenue:model:publish` + audit row with before/after snapshot.
- Application code **never** embeds AOV / close rates.

---

## 6. Estimation algorithm (deterministic)

### 6.1 Preconditions

Abort with `Insufficient Evidence` if any of:

1. No published `RevenueImpactModel` for tenant (+ industry/SKU fallback chain exhausted).
2. Scorecard missing or all items `Insufficient Evidence` on revenue-critical rules.
3. Root cause graph status is `Insufficient Evidence` and CRM outcome is unknown.
4. Call marked `non_sales` / `wrong_number` by Rule Engine (N/A).

### 6.2 Expected value formula

For each root-cause node `c` with confidence ≥ tenant threshold:

```
leak_c = aov * baseline_close_rate * cause_weight[c] * stage_multiplier[stage(c)] * severity(c)
```

- `severity(c)` ∈ [0,1] from scorecard fail weights (DB), not LLM.
- Sum components, apply `max_leak_cents` cap.
- If CRM `won` with payment captured → leak forced to `0` with status `ok` and note `CRM_WON_OVERRIDE` (still audit).
- If CRM `lost` + strong buying-signal evidence ignored → allow uplift factor from model JSON (`ignored_buying_signal_uplift`), still DB-defined.

### 6.3 Recoverability flag

`recoverable=true` only when:

- cause is in model’s `recoverable_causes` set, AND
- call age ≤ `recovery_window_hours` (model), AND
- agent still employed / assigned (HR flag from metadata if present; else leave `recoverable=null` + reason).

---

## 7. Aggregations & dashboard contracts

| View | Definition | Refresh |
|------|------------|---------|
| Top Revenue Leak | Sum by `cause_code` last 7/30 days | Near-real-time (≤5 min) |
| Leader Gap | Team avg score vs leak / agent; gap vs team median | Hourly |
| Product Bottleneck | Leak by SKU × cause | Hourly |
| Hourly Conversion | Calls / scored / won / leak by hour-of-day | 15 min |
| Recoverable Revenue | Sum `recoverable=true` | 15 min |
| Heatmap | Hour × weekday leak intensity | Hourly |
| Funnel | Stage fail rates × leak contribution | Hourly |
| Pareto | 80/20 causes by amount | Hourly |
| Trend | Daily leak + conversion | Daily |

API:

- `GET /v1/revenue-leak/summary?from&to&team_id&sku`
- `GET /v1/calls/{call_id}/revenue-leak`
- `GET /v1/revenue-leak/heatmap`
- `GET /v1/revenue-leak/pareto`

All list endpoints: cursor pagination; tenant isolation mandatory.

---

## 8. Quality Gates

| Gate | Rule |
|------|------|
| QG-RL-01 | No estimate without `model_version` |
| QG-RL-02 | Every component has ≥1 evidence span OR explicit IE status |
| QG-RL-03 | `estimated_total == sum(components.amount)` (±1 cent) |
| QG-RL-04 | Currency matches model |
| QG-RL-05 | Unit tests cover CRM won→0, missing model→IE, cap enforcement |
| QG-RL-06 | Shadow compare: weekly sample vs human Revenue Ops labels (Calibration doc) |

---

## 9. SLOs

| Metric | Target |
|--------|--------|
| p95 estimate latency after root cause ready | ≤ 800 ms |
| Estimate error vs labeled set (MAPE) | ≤ 25% on recoverable subset |
| IE rate unexplained | ≤ 2% of scored sales calls |

---

## 10. Security & audit

- RBAC: `revenue:read`, `revenue:model:write`, `revenue:model:publish`.
- Audit every model publish and every manual override of leak amount.
- PII: amounts OK in analytics; transcript evidence remains ACL’d as call data.

---

## 11. Implementation checklist

- [ ] Migrations for models + estimates + rollups
- [ ] `EstimateRevenueLeakUseCase` + DI wiring
- [ ] Worker hook after root cause stage
- [ ] Dashboard widgets (Heatmap, Funnel, Pareto, Trend)
- [ ] Seed impact models per industry via admin UI (not code constants)
- [ ] OpenAPI schemas synced
- [ ] Sprint Review → Refactor → Quality Gate before merge


---

## Appendix A — Worker contract

Redis stream: `pipeline.revenue_leak`

Payload:

```json
{
  "tenant_id": "tn_...",
  "call_id": "cl_...",
  "pipeline_run_id": "pr_...",
  "scorecard_id": "sc_...",
  "root_cause_id": "rc_...",
  "attempt": 1
}
```

Idempotency key: `revenue_leak:{call_id}:{model_version}`.

Retries: exponential backoff 3x on transient DB/Redis; poison → `pipeline_dead_letters` with reason.

---

## Appendix B — Failure modes

| Mode | System behavior |
|------|-----------------|
| Model not published | `Insufficient Evidence` + reason `MISSING_AOV_PRIOR` |
| Root cause IE | Propagate IE; do not invent causes |
| Currency mismatch | Hard fail job; alert `revenue_leak.currency_mismatch` |
| Cap exceeded before cap | Apply cap; audit `capped=true` |
| CRM won | Force 0; keep components for coaching analytics with `amount_ignored=true` |

---

## Appendix C — OpenAPI fragment

```yaml
RevenueLeakResponse:
  type: object
  required: [status, evaluated_at]
  properties:
    status: { type: string, enum: [ok, Insufficient Evidence] }
    currency: { type: string, nullable: true }
    estimated_total: { type: number, nullable: true }
    model_version: { type: integer, nullable: true }
    confidence: { type: number, nullable: true }
    components:
      type: array
      items:
        type: object
        required: [cause_code, amount, confidence]
        properties:
          cause_code: { type: string }
          rule_ids: { type: array, items: { type: string } }
          amount: { type: number }
          evidence_span_ids: { type: array, items: { type: string } }
          timestamps:
            type: array
            items:
              type: object
              properties:
                start_ms: { type: integer }
                end_ms: { type: integer }
```
