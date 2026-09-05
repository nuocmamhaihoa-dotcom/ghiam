# AI Brain Manifest

Generated: 2026-09-05T14:33:25.375386+00:00

## Rulebook
- File: `ai-brain/rulebook/rulebook_1000.jsonl` (canonical schema)
- Alias: `ai-brain/rulebook/rules_1000.jsonl`
- Count: **1000** / unique IDs: **1000**
- Allocation: {"Opening": 100, "Rapport": 80, "Discovery": 150, "Qualification": 80, "Presentation": 120, "Pricing": 80, "Objection": 200, "Closing": 80, "Voice": 60, "Compliance": 50}
- Dedupe gates every 50: **PASS** (20 checks)

## VCIE
- Utterances: 20,000 (dialect {'nam': 8939, 'bac': 7034, 'trung': 4027})
- Intents: 500
- Objection handlers: 1,000
- Buying signals: 300
- Emotion timelines: 500
- Silence patterns: 300
- Interrupt patterns: 300
- Context inference: 200

## Root Cause Graphs
- `ai-brain/root_cause/root_cause_graphs_500.jsonl` — **500**, each linked to rule_ids

## SOP Industries
- `ai-brain/sop/sop_industries_50.jsonl` — **50**, each linked to rule_ids + compliance rules

## Invariants
- No business rules hardcoded in app code — Rulebook is source of truth for seed/DB.
- All VCIE/RCE/SOP records reference valid `R-xxxx` ids.
- Missing evidence at runtime → `Insufficient Evidence`.
