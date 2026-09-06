# Sprint 17 — Risk Report

| Risk | Mitigation | Status |
|------|------------|--------|
| Scoring low-quality audio | Hard AIE gate in pipeline + scoring service | Mitigated |
| Invented speaker labels | Diarization marks Unknown when uncertain | Mitigated |
| Lost original transcript meaning | Keep original_text + normalized_text | Mitigated |
| Keyword-only objections | Context window required around cues | Mitigated |
| Bulk upload failure | Queue + retries + resume batch | Mitigated |
| No rollback after repair | original_ref + rollback_repair | Mitigated |
| Real DSP/STT not wired in CI | Deterministic heuristic engines; ML adapters can replace later | Accepted (dev) |

Overall residual risk: **Low** for gate enforcement; medium for production ASR accuracy until model backends are connected.
