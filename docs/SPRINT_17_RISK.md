# Sprint 17 — Risk Report

| Risk | Severity | Mitigation |
|------|----------|------------|
| Scoring on bad audio | High | Hard quality gate in AIE + scoring hook |
| Invented speakers | High | Uncertain → Unknown Speaker |
| Evidence-less deductions | High | Evidence integrity threshold |
| Over-penalizing silence | Medium | Silence taxonomy (thinking ≠ fault) |
| Keyword-only objections | Medium | Context window required |
| Live latency overrun | Medium | Explicit 2s budget; stream stubs |
| Bulk upload failures | Medium | Queue retries + resume |
| PII in transcripts | Medium | Store under datasets/; redact in exports later |

## Residual

Production ASR/diarization accuracy depends on vendor adapters not yet wired.
