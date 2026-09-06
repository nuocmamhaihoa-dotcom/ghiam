# Rulebook Canonical Source

- **Canonical:** `rulebook_1000.jsonl` (sha256 `71473be6abd4cc7c4ec26b31ab60725ab9263a4141bdca100c8293f9ccd05d38`)
- **Compatibility projection:** `rules_1000.jsonl` (same 1000 rule_ids + legacy aliases: id/name/pass/fail/evidence/timestamp_required)
- Do not edit `rules_1000.jsonl` by hand — regenerate from canonical via `scripts/audit/sync_rulebook_projection.py` (or this audit patch).

Audit finding: duplicate rule IDs across two files with divergent schemas (HIGH). Resolved by making rules_1000 a deterministic projection.
