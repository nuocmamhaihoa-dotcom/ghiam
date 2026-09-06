# Sprint 17 — Audio Accuracy Report

## Measured (deterministic CI engines)

| Area | Result | Notes |
|------|--------|-------|
| Upload detection | Pass | Ext / integrity / media type |
| Quality scoring | Pass | Blocks below 55 |
| Repair delta | Pass | quality_after > quality_before |
| Diarization uncertainty | Pass | Unknown when empty/low confidence |
| Transcript original+normalized | Pass | Abbreviations expanded, original kept |
| Dialect detection | Pass | north/central/south heuristics |
| Buying signals | Pass | payment/invoice patterns |
| Objections | Pass | Context required (not keyword-only) |
| Evidence integrity | Pass | timestamp+speaker+transcript+rule |
| Batch resume | Pass | resume_from respected |
| Live latency budget | Pass | ≤ 2.0s budget field |
| Emotion by_second | Pass | 1s forward-fill timeline |
| Silence risk_flag | Pass | thinking ≠ automatic fault |
| Enterprise search filters | Pass | intent/emotion/rule/agent/customer |
| Folder upload metadata | Pass | folder_path retained on queue |

## Gaps (production adapters)

- Real STT (Whisper / vendor) behind TranscriptEngine
- Real diarization (pyannote / cloud) behind speaker module
- Signal-level DSP for repair (current: policy engine + refs)
