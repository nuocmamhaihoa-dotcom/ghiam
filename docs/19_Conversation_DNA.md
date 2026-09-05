# 19 — Conversation DNA
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Version | `1.0.0` |
| Status | Enterprise — implementable |
| Role | Longitudinal agent skill profile (“DNA”) |
| Constitution | Derived only from scored evidence · No personality stereotypes |

---

## 1. Purpose

Conversation DNA is a **versioned, evidence-backed skill genome** for each agent (and optionally team):

- Strengths / weaknesses by stage & rule family
- Repeat mistakes (recurrence)
- Progress over time
- Radar chart dimensions for coaching UI
- Emotion / objection handling profiles (from labeled spans)

DNA is computed from historical scorecards + root causes + coaching completions — **never** from demographic guesses or manager free-text alone.

If an agent has fewer than `min_calls_for_dna` scored calls with evidence → return **`Insufficient Evidence`**.

---

## 2. Dimensions (default radar)

Stored in DB `dna_dimension_defs` (tenant-customizable labels/weights):

| Code | Description | Source signals |
|------|-------------|----------------|
| `opening_quality` | Greeting, permission, brand | Opening rules pass rate |
| `rapport` | Empathy, mirroring | Rapport rules + emotion recovery |
| `discovery_depth` | Question quality / coverage | Discovery rules + segment coverage |
| `qualification` | BANT/need fit | Qualification rules |
| `value_framing` | Benefit vs feature | Presentation rules |
| `pricing_discipline` | Timing & clarity of price | Pricing rules + RC_EARLY_PRICING inverse |
| `objection_mastery` | Handle objections | Objection rules + root causes |
| `closing_assertiveness` | Ask for sale / next step | Closing rules |
| `compliance` | Mandatory disclosures | Compliance auto-fail inverse |
| `voice_clarity` | Pace/filler (when audio features exist) | Voice rules; else IE for dimension |

Each dimension score ∈ [0, 100] with CI and sample_n.

---

## 3. Domain model

```text
ConversationDnaProfile
  agent_user_id
  window (e.g. last_30d / last_90d / career)
  computed_at
  rulebook_release_id_max   # highest release included
  dimensions[] { code, score, ci_low, ci_high, sample_n, evidence_call_ids[] }
  top_strengths[3]
  top_weaknesses[3]
  repeat_mistakes[] { cause_code, count, last_seen_at, example_call_id, evidence_span_id }
  progress[] { period, dimension_code, delta }
  emotion_profile { label → rate }
  status: ok | Insufficient Evidence
```

### Example JSON

```json
{
  "agent_user_id": "us_...",
  "window": "last_30d",
  "status": "ok",
  "computed_at": "2026-09-05T12:00:00Z",
  "dimensions": [
    {"code": "discovery_depth", "score": 71, "sample_n": 48, "ci_low": 66, "ci_high": 76}
  ],
  "top_strengths": ["compliance", "opening_quality", "rapport"],
  "top_weaknesses": ["pricing_discipline", "closing_assertiveness", "objection_mastery"],
  "repeat_mistakes": [
    {
      "cause_code": "RC_EARLY_PRICING",
      "count": 11,
      "last_seen_at": "2026-09-04T10:00:00Z",
      "example_call_id": "cl_...",
      "evidence_span_id": "ev_..."
    }
  ],
  "progress": [
    {"period": "2026-W34", "dimension_code": "objection_mastery", "delta": 4.2}
  ]
}
```

---

## 4. Computation algorithm

1. Select calls: agent + window + `scorecard.status=ok`.
2. If `count < min_calls_for_dna` → IE.
3. For each dimension def: aggregate weighted pass rates of mapped `rule_ids` (mapping in DB).
4. Wilson score interval for CI when binary pass/fail; else bootstrap on continuous stage scores.
5. Repeat mistakes: root-cause codes with count ≥ `repeat_threshold` and occurrence in ≥2 distinct weeks.
6. Progress: compare trailing windows (W vs W-4).
7. Persist snapshot; do not mutate previous snapshots (time-series).

Emotion profile: only from emotion labels attached to evidence spans; if ASR emotion model disabled → omit or IE for that subsection.

---

## 5. Architecture

```
BuildConversationDnaUseCase
  ├─ ScorecardRepository.list_by_agent
  ├─ RootCauseRepository.aggregate
  ├─ DnaDimensionCatalogRepository
  ├─ DnaMath (pure)
  ├─ ConversationDnaRepository.insert_snapshot
  └─ AuditPort (on forced rebuild)
```

Scheduler: nightly + on-demand after coaching plan completion.

---

## 6. APIs & UI

| Method | Path |
|--------|------|
| GET | `/v1/agents/{id}/conversation-dna?window=last_30d` |
| POST | `/v1/agents/{id}/conversation-dna/rebuild` |
| GET | `/v1/teams/{id}/conversation-dna/compare` |

UI:

- Radar chart (10 dimensions)
- Strength / Weakness chips linking to example calls with evidence player
- Repeat mistake timeline
- Progress sparklines
- CTA → generate coaching plan targeting top weaknesses

---

## 7. Quality Gates

| ID | Gate |
|----|------|
| QG-DNA-01 | No DNA without min sample_n |
| QG-DNA-02 | Every weakness links to ≥1 evidence example |
| QG-DNA-03 | Dimension defs only from DB |
| QG-DNA-04 | Snapshots immutable |
| QG-DNA-05 | Unit tests for IE path and Wilson CI |

---

## 8. Privacy

- Managers see DNA for agents in their team scope only (RBAC).
- Agents see own DNA.
- Export requires `dna:export` + audit.

---

## 9. Implementation checklist

- [ ] `dna_dimension_defs`, `conversation_dna_snapshots` migrations
- [ ] Builder job + API
- [ ] Frontend radar + evidence deep links
- [ ] Wire Coaching Engine to consume weaknesses
- [ ] Sprint Review → Refactor → Quality Gate


---

## Appendix A — Dimension mapping table

```sql
CREATE TABLE dna_dimension_rule_map (
  tenant_id UUID NOT NULL,
  dimension_code TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  weight NUMERIC(6,4) NOT NULL DEFAULT 1.0,
  PRIMARY KEY (tenant_id, dimension_code, rule_id)
);
```

Weights are configuration data — never compile into binaries.

---

## Appendix B — Insufficient Evidence response

```json
{
  "agent_user_id": "us_...",
  "window": "last_30d",
  "status": "Insufficient Evidence",
  "reason_codes": ["MIN_CALLS_NOT_MET"],
  "sample_n": 7,
  "min_calls_for_dna": 20,
  "computed_at": "2026-09-05T12:00:00Z"
}
```
