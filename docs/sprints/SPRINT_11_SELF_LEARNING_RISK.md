# Sprint 11 — Self-Learning Lab Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Accidental Rulebook auto-mutation | Critical | Hard invariant + QualityGate + tests asserting `auto_applied_to_production=false` |
| Low-evidence proposals flooding QA | Medium | Quality threshold 0.55; evidence_count + novelty blend |
| Duplicate production knowledge | Medium | Gate compares suggested_rule/title against layer 4 |
| Cluster over-merge | Low | Seed families + Jaccard; QA rename/merge tools |
| Rollback misuse | Low | Version records + reviewer audit trail |
