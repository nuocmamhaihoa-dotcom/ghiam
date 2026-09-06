# Audio Intelligence Engine (AIE)

First layer of the AI Sales Operating System pipeline.

## Hard rule

**Do not score / analyze a call until audio quality passes the gate.**

Pipeline (no skipping):

Upload → Validation → Audio Repair → Noise Removal → Voice Separation → Speaker Diarization → Transcript → Evidence Extraction → Quality Score → AI Analysis

## Packages

- `backend/audio_engine/` — orchestrator, intelligence, store, types
- `backend/audio_pipeline/` — immutable stage runner
- `backend/audio_repair/` — repair + rollback
- `backend/speaker/` — separation + diarization (no guessing when uncertain)
- `backend/transcript/` — Vietnamese transcript + dialect normalization
- `frontend/upload/` + `enterprise-web/.../audio-intelligence`

## Quality gate thresholds

- audio_quality_min: 55
- transcript_confidence_min: 0.55
- speaker_confidence_min: 0.55
- timestamp_coverage_min: 0.90
- evidence_integrity_min: 0.90

If blocked → auto-repair path; scoring remains disabled until pass.

## APIs

`/v1/audio-intelligence/`

- upload, process, repair, repair/rollback
- transcript, diarization, quality, emotion, evidence
- export, batch, search, live, dashboard

OpenAPI tag: `audio-intelligence`

## Outputs

Transcript, Evidence, Emotion Timeline, Speaking Timeline, Buying Signals, Objection List, Audio Quality Report — JSON / PDF / Excel.
