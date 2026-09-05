# 18 — Calibration
## AI QA TELESALE ENTERPRISE

| Field | Value |
|-------|-------|
| Version | `1.0.0` |
| Status | Enterprise — implementable |
| Role | Align AI scores with human QA; detect drift; gate releases |
| Constitution | Evidence-first · Insufficient Evidence · No silent score fudge |
| Corpus | **10,000** QA Benchmark calls (+ 500 Golden) |

---

## 1. Purpose

Calibration ensures the AI Judge Ensemble and Rule Engine remain trustworthy vs human QA:

- Measure agreement, bias, and rule-level confusion.
- Tune **decision thresholds / weights in Rulebook DB** (never ad-hoc constants in code).
- Gate model, prompt, and Rulebook publishes when drift exceeds SLO.
- Produce explainable calibration reports for QA Directors.

Calibration **does not** “boost scores” to look good. It changes versioned Rulebook parameters or model routing via audited publishes.

---

## 2. Data contracts

### 2.1 QA Benchmark row

```json
{
  "benchmark_id": "qb_...",
  "call_id": "cl_...",
  "rulebook_release_id": "rr_...",
  "ai_score": 78.5,
  "human_score": 82.0,
  "difference": -3.5,
  "ai_verdicts": [{"rule_id": "R_OBJ_014", "verdict": "fail"}],
  "human_verdicts": [{"rule_id": "R_OBJ_014", "verdict": "pass"}],
  "reviewer_id": "us_...",
  "review_reason": "Evidence đủ nhưng AI miss soft commitment",
  "ai_run_id": "pr_...",
  "labeled_at": "2026-09-04T09:00:00Z"
}
```

### 2.2 Required fields

`ai_score`, `human_score`, `difference`, `reviewer`, `reason`, rule-level verdicts, `rulebook_release_id`, timestamps.

Missing any → row invalid for calibration math (exclude + flag).

---

## 3. Metrics

| Metric | Definition | Release gate example |
|--------|------------|----------------------|
| MAE | mean(\|AI − Human\|) | ≤ 6.0 points |
| Bias | mean(AI − Human) | \|bias\| ≤ 2.0 |
| Pearson / Spearman | score correlation | ≥ 0.75 |
| Exact rule agreement | % rules same verdict | ≥ 85% |
| Critical rule agreement | compliance / pricing rules | ≥ 95% |
| IE precision/recall | AI IE vs human “cannot score” | F1 ≥ 0.80 |
| Golden MAE | MAE on golden set | ≤ 4.0 |

All gates stored in DB table `calibration_slos` (tenant-overridable), not hardcoded.

---

## 4. Process

```
Sample calls (stratified: industry, dialect, outcome, team)
  → Blind human label (dual review)
  → Lock labels
  → Re-score with candidate Rulebook/Model
  → Compute metrics + confusion matrices
  → Propose Rulebook weight/threshold deltas (diff only)
  → QA Director approve publish
  → Shadow traffic → promote
```

### Stratified sampling rules

- Min N per industry dialect cell (configurable; default 30).
- Oversample auto-fail / compliance rules.
- Exclude calls already in active golden set from “tuning” samples (use only for holdout).

---

## 5. Architecture

```
RunCalibrationUseCase
  ├─ BenchmarkRepository.list(filter)
  ├─ ScoringPort.rescore(call_ids, rulebook_release_id, model_route)
  ├─ MetricCalculator
  ├─ ConfusionMatrixBuilder (per rule_id)
  ├─ ProposalGenerator → RulebookChangeSet (draft)
  ├─ CalibrationReportRepository.save
  └─ AuditPort.record
```

LLM may suggest wording for coaching on disagreements; it **must not** auto-write Rulebook weights without human publish.

---

## 6. APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/calibration/runs` | Start calibration run |
| GET | `/v1/calibration/runs/{id}` | Status + metrics |
| GET | `/v1/calibration/runs/{id}/confusion` | Per-rule matrix |
| POST | `/v1/calibration/runs/{id}/propose-rulebook` | Draft change set |
| POST | `/v1/rulebook/releases/{id}/publish` | Existing publish API; requires calibration report link when `require_calibration=true` |
| GET | `/v1/qa-benchmark` | List labeled rows |

---

## 7. UI (QA Director)

- Calibration Run wizard (sample plan → assign reviewers → lock).
- Scatter AI vs Human with click-through to evidence.
- Rule confusion heatmap.
- “Propose weight changes” preview diff against Rulebook.
- Gate dashboard: green/red vs `calibration_slos`.

---

## 8. Quality Gates

| ID | Gate |
|----|------|
| QG-CAL-01 | Holdout golden MAE within SLO before Rulebook publish |
| QG-CAL-02 | Dual-review κ ≥ 0.70 on new human labels |
| QG-CAL-03 | No publish if critical-rule agreement below SLO |
| QG-CAL-04 | All proposals reference calibration_run_id in audit |
| QG-CAL-05 | Benchmark corpus ≥ 10,000 for platform reference |

---

## 9. Anti-patterns (forbidden)

- Multiplying all scores by a constant to “fix MAE”.
- Prompt text like “be more generous this week”.
- Dropping IE cases from MAE without reporting IE metrics.
- Calibrating on the same calls used as Golden without holdout split.

---

## 10. Implementation checklist

- [ ] Tables: `qa_benchmark_labels`, `calibration_runs`, `calibration_slos`
- [ ] Stratified sampler service
- [ ] Metric + confusion jobs
- [ ] Rulebook draft proposal from disagreements
- [ ] Publish gate integration
- [ ] Sprint Review → Refactor → Quality Gate


---

## Appendix A — Confusion matrix storage

```sql
CREATE TABLE calibration_rule_confusion (
  run_id UUID NOT NULL REFERENCES calibration_runs(id),
  rule_id TEXT NOT NULL,
  ai_verdict TEXT NOT NULL,
  human_verdict TEXT NOT NULL,
  count INT NOT NULL,
  PRIMARY KEY (run_id, rule_id, ai_verdict, human_verdict)
);
```

---

## Appendix B — Release gate pseudocode

```python
def can_publish_rulebook(report, slos) -> tuple[bool, list[str]]:
    reasons = []
    if report.mae > slos.max_mae:
        reasons.append("MAE_EXCEEDED")
    if abs(report.bias) > slos.max_abs_bias:
        reasons.append("BIAS_EXCEEDED")
    if report.critical_agreement < slos.min_critical_agreement:
        reasons.append("CRITICAL_RULE_AGREEMENT")
    if report.golden_mae > slos.max_golden_mae:
        reasons.append("GOLDEN_MAE_EXCEEDED")
    return (len(reasons) == 0), reasons
```

All thresholds read from `calibration_slos` table.
