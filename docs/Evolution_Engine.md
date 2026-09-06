# Evolution Engine

Autonomous Evolution Engine V1 for the AI Sales Operating System.

## Hard rules

1. **Never auto-change Rulebook or Production** without QA Approval.
2. **Approve ≠ Promote** — promotion is an explicit second step.
3. **Shadow Mode never impacts users** (`user_impact=False` always).
4. **Experiment winners are never auto-promoted** (`auto_promoted=False`).
5. Every production-bound change is **versioned** for rollback.

## Lifecycle

```
New data
  → Detect improvement opportunities
  → Propose changes (PENDING_QA)
  → Shadow Mode dual-run
  → Experiment A/B evaluation
  → KPI review
  → QA Approval
  → Explicit Promote to Production
  → Post-deploy monitoring
  → Rollback if needed
```

## Package layout

| Path | Role |
|------|------|
| `backend/evolution/` | Orchestrator, proposals, quality gate, store, types |
| `backend/shadow_mode/` | Dual pipeline compare (prod vs candidate) |
| `backend/experiments/` | A/B experiment engine |
| `backend/rollback/` | Version snapshots + rollback |
| `backend/metrics/` | Scorecard, drift, failure replay, performance, observability |
| `tests/evolution/` | Regression suite |
| `docs/evolution/` | Generated reports |

## Modules

### 1. Shadow Mode

Runs Pipeline A (production) and Pipeline B (candidate) in parallel.

Compares: score, root_cause, emotion, buying_signal, coaching, revenue_leak.

If deltas exceed thresholds → significant report. Users always see Pipeline A only.

### 2. Experiment Engine

Create A/B arms, record KPI samples, conclude winner.

KPIs: accuracy, qa_agreement, revenue_impact, coaching_effectiveness, conversion_improvement.

Winner requires QA before any production change.

### 3. Model Scorecard

Tracks Evidence AI, Psychology AI, Sales Expert AI, Pragmatics Engine:

accuracy, precision, recall, F1, drift, latency_ms.

### 4. Drift Detection

Signals: data, language, industry, customer. Alerts when score ≥ threshold.

### 5. Failure Replay

Stores audio_ref, transcript, rule, AI output, error for QA replay.

### 6. Auto Improvement Proposals (weekly)

Generates Rule / Coaching / SOP / Memory proposals with status `pending_qa`.

**Never auto-merge.**

### 7. Rollback Engine

Supports rule, model, prompt, dataset, coaching, sop versioning + rollback.

### 8. Performance Optimizer

Suggests query / cache / queue / memory / CPU / GPU fixes.

### 9. Observability Center

Tracks error_rate, ai_latency_ms, queue_depth, upload_speed_mbps, transcript_speed_x, dashboard_p95_ms.

### 10. Executive Dashboard

`EvolutionEngine.executive_dashboard()` — system health, AI accuracy, drift, shadow, experiments, revenue impact, critical issues, rollback status.

## Quality Gate

Cycle cannot close if:

- critical/high alerts open
- unresolved drift
- auto_merge proposals present
- experiment auto_promoted
- broken pipeline error_rate

## API (`/v1/evolution`)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/dashboard` | CTO executive view |
| GET | `/quality-gate` | Gate status |
| POST | `/shadow/compare` | Dual-run compare |
| POST/GET | `/experiments` | Create / list |
| POST | `/experiments/{id}/record` | Record arm metrics |
| POST | `/experiments/{id}/conclude` | Conclude (no auto-promote) |
| GET/POST | `/scorecard` | Read / update |
| POST | `/drift/detect` | Detect drift |
| GET | `/drift/report` | Drift report |
| POST | `/failures/capture` | Capture failure |
| POST | `/failures/{id}/replay` | Replay for QA |
| POST | `/performance/analyze` | Perf suggestions |
| POST/GET | `/observability` | Record / snapshot |
| POST | `/weekly-cycle` | Generate PENDING_QA proposals |
| GET | `/proposals/pending` | QA queue |
| POST | `/proposals/{id}/approve` | Approve only |
| POST | `/proposals/{id}/reject` | Reject |
| POST | `/proposals/{id}/promote` | Explicit production promote |
| POST | `/rollback/snapshot` | Version artifact |
| POST | `/rollback` | Rollback |
| GET | `/rollback/readiness` | Readiness |
| POST | `/reports/write` | Write markdown reports |

## Integration

- Reuses the same QA discipline as `backend/self_learning/` (no silent production mutation).
- Wired in `backend/app/main.py` via `evolution` router.
- Service façade: `app.application.services.evolution.EvolutionService`.

## Reports

Generated under `docs/evolution/`:

- Evolution_Report.md
- AI_Scorecard.md
- Drift_Report.md
- Experiment_Report.md
- Performance_Report.md
- Rollback_Readiness.md
- Top_100_Improvements.md
- Roadmap_Next.md
