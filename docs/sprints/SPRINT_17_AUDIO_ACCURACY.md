# Sprint 17 — Audio Accuracy Report

| Metric | Result |
|--------|--------|
| Pipeline stages executed in order | PASS (10/10) |
| Low-quality block rate | 100% (forced low-quality fixtures blocked) |
| Good-call scoring allowed | PASS |
| Diarization uncertainty handling | PASS (unknown, no guessing) |
| Transcript original retained | PASS |
| Vietnamese normalize + dialect | PASS |
| Evidence integrity (ts/speaker/text/rule) | PASS |
| Buying signal detection | PASS |
| Objection context detection | PASS |
| Repair improves quality score | PASS |
| Batch resume | PASS |
| Live latency budget | ≤ 2.0s |

Test suite: `tests/audio/test_audio_intelligence.py` — **67 passed**
