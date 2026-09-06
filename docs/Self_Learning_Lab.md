# AI Self-Learning Lab

**Status:** Production-ready research module (QA-gated)  
**Invariant:** AI may propose; only QA may promote to Production. Never auto-mutate Rulebook.

## Purpose

Turn every new call into learning signal — novel patterns, intents, objections, golden calls, failure modes, revenue leaks, and coaching — while keeping Production knowledge behind an explicit QA gate.

## Architecture

```
backend/self_learning/     Lab façade, store, types
backend/research/          Pattern / cluster / intent / objection /
                           golden / failure / revenue / coaching engines
backend/approval/          QualityGate + ApprovalCenter (versioning, promote, rollback)
datasets/self_learning/    Persistent lab datasets (raw, proposals, knowledge layers, metrics)
enterprise-web             /self-learning dashboard + /qa-approval center
tests/self_learning/       ≥5000 fixture-driven tests
```

## Learning pipeline

```
New Call
  → Transcript
  → Evidence Extraction
  → Rule Matching
  → Novel Pattern Detection
  → Cluster Analysis
  → Root Cause Discovery
  → Proposal Generator
  → QA Review (Approve / Reject / Merge / Edit)
  → Approved Knowledge
  → Quality Gate
  → Memory Graph link
  → Production
```

## Knowledge layers (cannot skip QA)

| Layer | Name | Contents |
|-------|------|----------|
| 1 | Raw Calls | Ingested transcripts & metadata |
| 2 | Verified Knowledge | Research proposals above quality threshold |
| 3 | Approved Rules | QA-approved proposals (not yet production) |
| 4 | Production Knowledge | Promoted only after Approve + QualityGate |

## Hard product rules

1. `ingest_call` always returns `pending_qa=true` and `auto_applied_to_production=false`.
2. `approve` moves to Approved layer — it does **not** promote.
3. `promote` requires `status=approved`, evidence, confidence, version, and passes QualityGate (no duplicate in production).
4. Rollback restores prior production version with reviewer + timestamp + diff.

## Discovery engines

| Engine | Output |
|--------|--------|
| PatternDetector | Novel phrases / buying signals / emotions |
| ClusterEngine | Synonym families + hidden meaning + confidence |
| IntentDiscovery | New intents + triggers + counter-examples + coaching |
| ObjectionDiscovery | New objections + handling draft |
| GoldenCallDiscovery | High-close / strong discovery / good duration |
| FailurePatternDiscovery | Early quote, drop-off, over-interrupt, … |
| RevenueLeakDiscovery | Weekly leak clusters + lost revenue estimate |
| CoachingGenerator | Coaching / role-play / checklist / good-bad examples |

## Proposal schema

Each proposal includes: `proposal_id`, `kind`, `title`, `summary`, `evidence`, `confidence`, `novelty`, `evidence_count`, `suggested_rule`, `suggested_coaching`, `suggested_sop_update`, `expected_impact`, `quality_score`, `status`, `version`, `diff`.

Quality score blends confidence, novelty, evidence count, and QA-agreement prediction. Queue visibility threshold: **0.55**.

## API (`/v1/self-learning`)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/ingest` | Learn from one call (proposals only) |
| GET | `/dashboard` | Learning widgets + layer counts |
| GET | `/qa-queue` | Pending QA items |
| POST | `/proposals/{id}/approve\|reject\|merge\|edit\|promote` | QA actions |
| POST | `/versions/{id}/rollback` | Restore prior production |
| POST | `/discover/golden\|failures\|revenue-leaks` | Batch discovery |
| POST | `/coaching/generate` | Auto coaching drafts |
| POST | `/self-evaluate` | Daily accuracy self-check |
| GET | `/dataset-growth` | Monthly growth stats |
| GET | `/quality` | Production leak / gate snapshot |

## UI

- `/self-learning` — Learning Dashboard (patterns, intents, objections, QA queue, velocity, revenue impact, confidence, knowledge growth)
- `/qa-approval` — QA Approval Center (approve / reject / edit / promote)

## Quality Gate checklist (pre-production)

- [ ] Not a duplicate of production knowledge
- [ ] Evidence present (`evidence_count ≥ 1`)
- [ ] Confidence ≥ 0.5 and quality_score ≥ 0.55
- [ ] Explicit QA approval
- [ ] Version assigned
- [ ] Rollback path available

## Sprint map (Self-Learning Lab)

| Sprint | Focus |
|--------|-------|
| 1 | Learning architecture + 4 layers |
| 2 | Novel pattern detection |
| 3 | Cluster engine |
| 4 | Proposal engine |
| 5 | QA Approval Center |
| 6 | Golden discovery |
| 7 | Revenue / failure discovery |
| 8 | Learning dashboard |
| 9 | ≥5000 tests |
| 10 | Final review + quality gate |

## Commands

```bash
PYTHONPATH=backend pytest tests/self_learning -q
PYTHONPATH=backend python scripts/quality_gate.py --sprint 11
```
