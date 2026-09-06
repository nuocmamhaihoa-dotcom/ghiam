# Sprint 17 — Audio Intelligence Engine

## Goal

Ship Audio Intelligence Engine as the first Sales OS pipeline layer with a hard quality gate before scoring.

## Delivered

- Upload / validation / repair / separation / diarization / transcript / evidence / quality / analysis pipeline
- Hard block when audio quality, transcript confidence, speaker confidence, or evidence integrity fail
- Vietnamese dialect + normalization (original text preserved)
- Buying signals, context objections, emotion timeline, interrupt & silence engines
- Batch resume, enterprise search, live suggestion (≤2s budget)
- API router + OpenAPI schema + enterprise UI + upload queue helper
- Scoring service gated by AIE

## Quality gate

`scripts/quality_gate.py --sprint 17` → must PASS.

## Exit criteria

- [x] Pipeline stages complete (10)
- [x] Low-quality audio blocks scoring
- [x] Good audio allows scoring + evidence
- [x] Diarization does not invent roles when uncertain
- [x] Tests in `tests/audio/` green

## V2 hardening

- Emotion `by_second` forward-fill, silence `risk_flag`, enterprise search filters
- Folder upload metadata, payload aliases, 1000+ upload queue ETA/retry
- Expanded contract tests (73+) and Sprint 17 gate PASS (5/5)
