# 11 — Enterprise Test Strategy & Test Cases

| Field | Value |
|-------|-------|
| Document ID | `DOC-QA-11-TEST` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| Scale | CI on every PR; nightly full suite; weekly chaos; monthly load @ 2× peak |
| Owners | QA Director, Backend Lead, ML Engineering, DevOps |
| Related | VCIE, SOP, Security, Monitoring |

---

## 1. Goals

Prove that AI QA Telesale Enterprise:

1. Scores correctly **only** from evidence + Rulebook DB.
2. Returns `Insufficient Evidence` instead of hallucinating.
3. Survives millions-of-calls throughput and partial failures.
4. Preserves JWT/RBAC, auditability, and the standard AnalysisResult envelope.

**Mandatory envelope fields in every contract test:**  
`score`, `stage_scores`, `violations`, `evidence`, `root_cause`, `coaching`, `revenue_leak`.

---

## 2. Test pyramid

| Layer | % runtime CI | Owner | Tools |
|-------|--------------|-------|-------|
| Unit | 50% | Eng | pytest, vitest, hypothesis |
| Integration | 25% | Eng | pytest + testcontainers (Postgres, Redis, MinIO) |
| Contract | 10% | QA+Eng | schemathesis / Dredd / OpenAPI |
| QA Calibration | 5% | QA+ML | gold sets, κ, F1 |
| Load | nightly | DevOps | k6 / Locust |
| Chaos | weekly | DevOps | Chaos Mesh / toxiproxy |
| Appeal / HITL | weekly | QA | Playwright + API |

---

## 3. Environments

| Env | Data | Rulebook | Audio |
|-----|------|----------|-------|
| `test` (CI) | synthetic fixtures | seeded migrations | wav snippets in repo |
| `staging` | anonymized prod sample | prod replica (read) | S3 staging bucket |
| `perf` | generator | frozen v | synthetic TTS+noise |
| `prod` | real | active versions | real S3 |

Never use production PII in CI. Hash/tokenizeize staging.

---

## 4. Unit tests

### 4.1 Domain — VCIE

| ID | Case | Expect |
|----|------|--------|
| `UT-VCIE-001` | Dialect Bắc markers only | `call_level_code=bac`, evidence quotes present |
| `UT-VCIE-002` | Dialect Nam markers only | `nam` |
| `UT-VCIE-003` | Mixed Bắc opener + Nam body | `mixed` + mix_ratio |
| `UT-VCIE-004` | Empty transcript | dialect `status=Insufficient Evidence` |
| `UT-VCIE-005` | Intent sequence happy path | ordered intents with ts |
| `UT-VCIE-006` | Objection PRICE phrase | `OBJ_PRICE` + customer quote |
| `UT-VCIE-007` | Buying signal address given | hard `BS_GIVE_ADDRESS` |
| `UT-VCIE-008` | Emotion without audio | `source=text_only`, confidence ≤ 0.65 |
| `UT-VCIE-009` | Silence > 4s after price | metric + evidence window |
| `UT-VCIE-010` | Agent overlap 30% | `agent_overlap_ratio≈0.30` |
| `UT-VCIE-011` | Context persona without evidence | `Insufficient Evidence` (no invent) |
| `UT-VCIE-012` | Feature hash stable | same input → same `feature_hash` |

### 4.2 Domain — Rule evaluation

| ID | Case | Expect |
|----|------|--------|
| `UT-RB-001` | Rule require ack missing | violation + evidence_refs |
| `UT-RB-002` | Feature required missing | rule status Insufficient Evidence |
| `UT-RB-003` | Rulebook empty | service error `RULEBOOK_NOT_CONFIGURED` (no default rules) |
| `UT-RB-004` | Weight override from SOP binding | stage score uses override |
| `UT-RB-005` | Deprecated rule version not applied | only active version |
| `UT-RB-006` | Revenue leak when hard BS & no schedule | `revenue_leak.leak_codes` contains code |
| `UT-RB-007` | Avg deal value missing | revenue_leak Insufficient Evidence |
| `UT-RB-008` | Coaching tips link rule_ids | every tip has linked_rule_ids |

### 4.3 Application / services

| ID | Case | Expect |
|----|------|--------|
| `UT-APP-001` | AnalyzeCall use-case writes audit | audit action `call.analyze` |
| `UT-APP-002` | Reanalyze changes rulebook_version | new analysis row; old retained |
| `UT-APP-003` | Idempotent analyze same key | no duplicate score rows |
| `UT-APP-004` | SopSelection tenant override | tenant SOP preferred |

### 4.4 Frontend unit

| ID | Case | Expect |
|----|------|--------|
| `UT-FE-001` | AnalysisResult renderer shows Insufficient Evidence badge | copy exact |
| `UT-FE-002` | No business threshold hardcoded in components | lint rule / eslint custom |
| `UT-FE-003` | Emotion timeline empty state | does not invent points |

---

## 5. Integration tests

Use Testcontainers: Postgres 16, Redis 7, MinIO.

| ID | Case | Expect |
|----|------|--------|
| `IT-001` | Ingest audio → S3 object exists | key `s3://calls/{tenant}/{call}.ogg` |
| `IT-002` | STT stub → features persisted | `vcie.features` row |
| `IT-003` | Full analyze path | `analyses` row with 7 fields |
| `IT-004` | Redis stream consumer retry | poison message → DLQ |
| `IT-005` | Publish SOP | old deprecated; audit present |
| `IT-006` | JWT valid role `qa_analyst` can GET analysis | 200 |
| `IT-007` | Role `employee` cannot publish SOP | 403 |
| `IT-008` | `/ready` fails when Postgres down | 503 |
| `IT-009` | Migration up/down smoke | alembic upgrade/downgrade |
| `IT-010` | Multi-tenant isolation | tenant A cannot read B analysis |

---

## 6. Contract tests (OpenAPI)

- Source of truth: FastAPI `/openapi.json`.
- Schemathesis fuzz all `/api/v1/**` with auth fixtures.
- Assert every `POST/GET *analysis*` response validates against `AnalysisResult`.
- Negative: missing fields → CI fail.
- Consumer contracts: Next.js BFF types generated from OpenAPI (`openapi-typescript`); typecheck in CI.

| ID | Endpoint | Assert |
|----|----------|--------|
| `CT-001` | `POST /api/v1/calls/{id}/analyze` | 200 schema + envelope |
| `CT-002` | `GET /api/v1/calls/{id}/analysis` | 200 / 404 |
| `CT-003` | `POST /api/v1/sop/{id}/publish` | 401/403 without role |
| `CT-004` | Error model `ProblemDetails` | `type,title,status,trace_id` |
| `CT-005` | Insufficient Evidence example fixture | status strings exact |

---

## 7. QA calibration tests

### 7.1 Gold sets

| Set | Size | Labels |
|-----|------|--------|
| `gold.dialect.v3` | 5,000 turns | bac/trung/nam/mixed |
| `gold.intent.v4` | 8,000 turns | intent catalog |
| `gold.objection.v4` | 3,000 events | objection type + handle features |
| `gold.score.bds.v2` | 1,000 calls | human stage scores |
| `gold.score.ins.v2` | 1,000 calls | human stage scores |
| `gold.compliance.food_supp.v1` | 500 calls | disease-claim violations |

### 7.2 Gates (release-blocking)

| Metric | Gate |
|--------|------|
| Dialect accuracy | ≥ 0.90 |
| Intent macro-F1 | ≥ 0.83 |
| Objection type macro-F1 | ≥ 0.85 |
| Score MAE vs human (stage) | ≤ 8 points |
| False-positive compliance violation rate | ≤ 2% |
| Hallucination probe (no-evidence conclusions) | **0** |

### 7.3 Cases

| ID | Case | Expect |
|----|------|--------|
| `CAL-001` | Run gold dialect | gate pass |
| `CAL-002` | Strip evidence from fixture; force evaluate | Insufficient Evidence, no fail |
| `CAL-003` | Inter-annotator κ < 0.6 label set | quarantine; do not train |
| `CAL-004` | Shadow mode new model vs prod | report drift; no auto-promote |

---

## 8. Load tests

### 8.1 Profiles

| Profile | Target | Duration |
|---------|--------|----------|
| `ingest_peak` | 5,000 metadata RPS | 30 min |
| `analyze_cached` | 2,000 analyze/min | 60 min |
| `stt_pipeline` | 120 audio-hours/hour equivalent | 60 min |
| `dashboard_read` | 1,000 RPS reads | 30 min |
| `soak` | 50% peak | 24 h |

### 8.2 SLOs under load

| SLO | Threshold |
|-----|-----------|
| Analyze API p95 (features ready) | ≤ 2.5 s |
| Analyze API p99 | ≤ 5 s |
| Error rate 5xx | < 0.1% |
| Queue lag STT | < 5 min |
| Postgres CPU | < 75% avg |

### 8.3 k6 sketch

```javascript
// tests/load/analyze.js
import http from 'k6/http';
import { check } from 'k6';
export const options = { stages: [{ duration: '10m', target: 200 }, { duration: '50m', target: 200 }] };
export default function () {
  const res = http.get(`${__ENV.BASE}/api/v1/calls/${__ENV.CALL_ID}/analysis`, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
  });
  check(res, {
    '200': (r) => r.status === 200,
    'has score': (r) => r.json('score') !== undefined,
    'has stage_scores': (r) => r.json('stage_scores') !== undefined,
    'has violations': (r) => r.json('violations') !== undefined,
    'has evidence': (r) => r.json('evidence') !== undefined,
    'has root_cause': (r) => r.json('root_cause') !== undefined,
    'has coaching': (r) => r.json('coaching') !== undefined,
    'has revenue_leak': (r) => r.json('revenue_leak') !== undefined,
  });
}
```

| ID | Case | Expect |
|----|------|--------|
| `LT-001` | ingest_peak | SLO hold |
| `LT-002` | analyze_cached | SLO hold |
| `LT-003` | soak 24h | no memory leak > 15% |
| `LT-004` | fan-out 50 industries enable | rule cache hit > 95% |

---

## 9. Chaos tests

| ID | Injection | Expect |
|----|-----------|--------|
| `CH-001` | Kill one API replica | traffic shifts; error blip < 1s |
| `CH-002` | Redis latency +2s | queue lag alert; no silent drop |
| `CH-003` | Postgres primary failover | readiness flap then recover; no corrupt scores |
| `CH-004` | MinIO 503 for 5 min | retries; calls marked `storage_pending`; no fake scores |
| `CH-005` | STT worker OOM | consumer rebalance; DLQ after max retries |
| `CH-006` | Clock skew ±5s on worker | timestamps still monotonic in evidence |
| `CH-007` | Drop 30% network to OpenTelemetry collector | app continues; local buffer |
| `CH-008` | Rulebook Redis cache flush mid-flight | reload from DB; versions consistent |

---

## 10. Appeal mode tests

Appeal allows contesting an AI score with human counter-evidence; all audited.

### 10.1 State machine

`scored → appeal_open → under_review → (upheld | overturned | partial) → closed`

### 10.2 Cases

| ID | Case | Expect |
|----|------|--------|
| `AP-001` | Employee opens appeal with note | status `appeal_open`; audit |
| `AP-002` | Employee without evidence span | 422 `EVIDENCE_REQUIRED` |
| `AP-003` | QA analyst upholds | score unchanged; reason stored |
| `AP-004` | QA manager overturns stage_score | new analysis revision; `meta.appealed=true` |
| `AP-005` | Overturn without rule_id reference | 422 |
| `AP-006` | Reanalyze during open appeal | blocked unless `qa_manager` force |
| `AP-007` | Appeal SLA > 48h | monitoring alert `appeal_sla_breach` |
| `AP-008` | Envelope after overturn | still contains 7 mandatory fields |
| `AP-009` | RBAC: employee cannot overturn | 403 |
| `AP-010` | UI Playwright: appeal timeline visible | before/after scores |

### 10.3 API

| Method | Path |
|--------|------|
| `POST` | `/api/v1/calls/{id}/appeals` |
| `POST` | `/api/v1/appeals/{id}/decide` |
| `GET` | `/api/v1/appeals/{id}` |

Decision body must include `evidence[]` with timestamps and `rule_id` impacts.

---

## 11. Security test cases (summary; detail in 14)

| ID | Case | Expect |
|----|------|--------|
| `SEC-001` | Expired JWT | 401 |
| `SEC-002` | Tampered role claim | 401/403 |
| `SEC-003` | IDOR call_id | 404/403 |
| `SEC-004` | SQL injection on search | parameterized; 400 |
| `SEC-005` | Export PII without `pii.export` | 403 |
| `SEC-006` | Audit immutable (no UPDATE API) | only append |

---

## 12. Anti-hallucination probes (release blockers)

| ID | Probe | Expect |
|----|------|--------|
| `AH-001` | Transcript lacks price; rule needs price | Insufficient Evidence, not fail/pass invent |
| `AH-002` | Prompt injection in customer speech | ignored for rule authorship |
| `AH-003` | Model returns claim without span | validator drops; status Insufficient Evidence |
| `AH-004` | Empty objections array; root_cause price | forbidden; root_cause Insufficient Evidence |

CI job `tests/anti_hallucination/` must be green to merge to `main`.

---

## 13. Test data fixtures layout

```
tests/
  fixtures/
    calls/
      bds_won_001.json
      ins_objection_price_001.json
      food_supp_illegal_claim_001.json
      insufficient_evidence_001.json
    rulebook/
      seed_bds_v12.sql
    audio/
      silence_after_price.wav
  unit/
  integration/
  contract/
  calibration/
  load/
  chaos/
  appeal/
  e2e/
```

Each call fixture includes expected `AnalysisResult` subset for assertions.

---

## 14. CI pipeline stages

```yaml
# .github/workflows/ci.yml (logical stages)
stages:
  - lint_typecheck
  - unit
  - integration_testcontainers
  - contract_openapi
  - anti_hallucination
  - build_images
  - (nightly) load
  - (weekly) chaos
  - (nightly) calibration_gold
```

PR merge requires: unit + integration + contract + anti_hallucination green.

---

## 15. Defect severity for scoring bugs

| Sev | Definition | Example |
|-----|------------|---------|
| S0 | Hallucinated violation/pass | Fail without evidence |
| S1 | Wrong tenant data leak / authz bypass | IDOR |
| S2 | Material score error vs gold > 15 pts | Stage mis-score |
| S3 | UX / coaching copy issues | Tip wording |
| S4 | Telemetry noise | Label typo in metrics |

S0/S1 block release.

---

## 16. Acceptance criteria for this strategy

1. Documented cases above implemented as automated tests with matching IDs in names.
2. AnalysisResult envelope asserted in contract + load checks.
3. Appeal mode covered by API + E2E.
4. Calibration gates enforced in release pipeline.
5. Chaos schedule exists with runbooks linked from `15_Monitoring.md`.
