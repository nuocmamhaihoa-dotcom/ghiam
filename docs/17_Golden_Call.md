# 17 — Golden Call
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Version | `1.0.0` |
| Status | Enterprise — implementable |
| Pipeline role | Benchmark corpus + scoring calibration anchor |
| Constitution | Immutable golden set · Evidence-bound · Rules from DB |
| Volume target | **500** golden calls (frozen per release) |

---

## 1. Purpose

Golden Calls are **human-approved, versioned reference conversations** used to:

1. Benchmark agent performance (similarity + gap analysis).
2. Calibrate AI Judge Ensemble vs human QA.
3. Anchor Coaching Engine “ideal path” examples.
4. Guard Rulebook regressions (score drift on frozen set).

Golden Calls are **immutable** after publish. Corrections require a new golden release (`golden_set_version`), never in-place mutation of published rows.

---

## 2. Definition of Done for a Golden Call

A call may enter `candidate` status only if:

| Requirement | Detail |
|-------------|--------|
| Full pipeline artifacts | Audio object key, diarized transcript, segments, evidence spans |
| Human QA dual review | Primary + secondary QA agree within Δscore ≤ 3 points, or adjudicator resolved |
| Rulebook pin | Explicit `rulebook_release_id` used during labeling |
| Industry + dialect tags | From controlled vocabularies |
| Outcome known | `won` / `lost` / `callback` with CRM id when applicable |
| No PII policy violation | Masked per Security doc |
| Coaching exemplar | At least one annotated “best practice” span set |

Promotion `candidate → published` requires role `golden:publish` + audit.

---

## 3. Domain model

```text
GoldenSet (tenant, version, status, rulebook_release_id, published_at)
  └─ GoldenCall (call_id or synthetic_id, industry, dialect, outcome, human_score, labels)
       ├─ GoldenAnnotation (rule_id, verdict, evidence_span_ids[], notes)
       ├─ IdealPathStep (stage, utterance_ref, coaching_tip_code)
       └─ EmbeddingRef (model_id, vector_id)  # optional for similarity search
```

### 3.1 JSON shape (published)

```json
{
  "golden_call_id": "gc_01H...",
  "golden_set_version": 7,
  "rulebook_release_id": "rr_01H...",
  "industry": "insurance",
  "dialect": "nam",
  "outcome": "won",
  "human_overall_score": 92,
  "stage_scores": {
    "opening": 95,
    "discovery": 90,
    "presentation": 88,
    "objection": 94,
    "closing": 93
  },
  "annotations": [
    {
      "rule_id": "R_OPN_001",
      "verdict": "pass",
      "evidence_span_ids": ["ev_..."],
      "timestamps": [{"start_ms": 0, "end_ms": 12000}],
      "note": "Chào đúng thương hiệu + xác nhận người nghe"
    }
  ],
  "ideal_path": [
    {"stage": "opening", "order": 1, "tip_code": "TIP_GREET_BRAND"},
    {"stage": "discovery", "order": 2, "tip_code": "TIP_SPIN_SITUATION"}
  ],
  "frozen_at": "2026-09-01T00:00:00Z",
  "content_hash": "sha256:..."
}
```

---

## 4. Lifecycle

```
ingest/select → annotate → dual_review → adjudicate?
  → candidate → publish_into_set → frozen
                 ↘ reject / quarantine
```

- **Quarantine**: audio quality fail, dialect mis-tag, rulebook mismatch.
- **Reject**: not exemplary / compliance fail.
- **Frozen**: content_hash locked; storage objects write-once.

---

## 5. Benchmark usage

### 5.1 Agent vs Golden similarity

```
similarity = cosine(embed(agent_call_segments), embed(golden_call_segments))
gap = golden.human_overall_score - agent.ai_overall_score
```

Report:

- Top-3 mismatched stages
- Missing ideal_path tip codes
- Evidence-linked coaching suggestions (Coaching Engine)

### 5.2 Regression suite

Nightly job scores all published golden calls with current Rulebook + Judge models:

| Signal | Action |
|--------|--------|
| Mean |Δ AI − Human| > threshold | Block Rulebook publish |
| Per-rule flip rate > 5% | Alert ML + QA Director |
| IE rate spike on golden | Halt scoring deploy |

See `18_Calibration.md`.

---

## 6. APIs

| Method | Path | RBAC |
|--------|------|------|
| POST | `/v1/golden-calls/candidates` | `golden:write` |
| POST | `/v1/golden-calls/{id}/annotations` | `golden:write` |
| POST | `/v1/golden-calls/{id}/publish` | `golden:publish` |
| GET | `/v1/golden-sets/{version}` | `golden:read` |
| POST | `/v1/calls/{id}/compare-golden` | `coaching:read` |
| GET | `/v1/golden-sets/{version}/regression-report` | `qa:admin` |

Compare response must include evidence diffs; never return similarity-only without stage breakdown.

---

## 7. Storage

- Audio/transcript: S3 (WORM / object lock when published).
- Metadata: PostgreSQL.
- Vectors: pgvector or external vector store keyed by `golden_call_id` + `embedding_model_id`.

---

## 8. Quality Gates

| ID | Gate |
|----|------|
| QG-GC-01 | Exactly versioned set; no silent edit |
| QG-GC-02 | Dual human review recorded |
| QG-GC-03 | content_hash verified on read for scoring jobs |
| QG-GC-04 | Dialect mix within ±5pp of VCIE targets inside set |
| QG-GC-05 | Industry coverage ≥ N industries configured for tenant |
| QG-GC-06 | 500 published calls for platform reference set (multi-tenant seed) |

---

## 9. Target corpus (platform seed)

| Attribute | Target |
|-----------|--------|
| Total golden | 500 |
| Won / Lost / Callback mix | 50% / 30% / 20% (configurable) |
| Dialect Bắc / Trung / Nam | 35% / 20% / 45% |
| Industries | All 19 SOP industries with ≥ 1 golden each; excess to top revenue industries |

---

## 10. Implementation checklist

- [ ] Schema: `golden_sets`, `golden_calls`, `golden_annotations`
- [ ] Admin UI: annotate + dual review workflow
- [ ] Compare-golden API + Coaching integration
- [ ] Nightly regression job + publish blocker
- [ ] Object-lock policy on S3 prefix `golden/`
- [ ] Sprint Review → Refactor → Quality Gate


---

## Appendix A — Selection SQL sketch

```sql
-- Candidate pool: high human score, dual-reviewed, diverse industry/dialect
SELECT c.id
FROM calls c
JOIN scorecards s ON s.call_id = c.id
JOIN human_reviews h ON h.call_id = c.id
WHERE h.consensus_score >= 88
  AND h.dual_review_state = 'agreed'
  AND c.dialect IS NOT NULL
  AND c.industry_code IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM golden_calls g WHERE g.source_call_id = c.id
  );
```

---

## Appendix B — Compare API response

```json
{
  "call_id": "cl_...",
  "golden_call_id": "gc_...",
  "similarity": 0.81,
  "score_gap": -12.5,
  "stage_gaps": [
    {"stage": "discovery", "call": 62, "golden": 90, "missing_tip_codes": ["TIP_SPIN_SITUATION"]}
  ],
  "evidence_mismatches": [
    {"rule_id": "R_DIS_044", "call_verdict": "fail", "golden_verdict": "pass", "golden_span_ids": ["ev_..."]}
  ]
}
```

If either side lacks evidence for a stage → stage entry status `Insufficient Evidence` (do not fabricate similarity for that stage).
