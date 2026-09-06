# Audio Intelligence Engine (AIE) V2

First layer of the **AI Sales Operating System** pipeline.

## Hard rule

**Do not score or analyze a call until audio quality passes the gate.**

If quality is below threshold → auto-repair path → re-check gate. Scoring stays blocked until pass.

## Pipeline (no stage skipping)

```
Upload
→ Validation
→ Audio Repair
→ Noise Removal
→ Voice Separation
→ Speaker Diarization
→ Transcript
→ Evidence Extraction
→ Quality Score
→ AI Analysis
```

Stages are defined in `PIPELINE_STAGES` (`backend/audio_engine/types.py`).

## Packages

| Path | Role |
|------|------|
| `backend/audio_engine/` | Orchestrator, intelligence, store, types |
| `backend/audio_pipeline/` | Immutable ordered stage runner |
| `backend/audio_repair/` | Repair + rollback (original kept) |
| `backend/speaker/` | Voice separation + diarization (no guessing) |
| `backend/transcript/` | Transcript + Vietnamese normalize/dialect |
| `frontend/upload/` | Bulk upload queue (ETA / retry) |
| `enterprise-web/.../audio-intelligence` | Console UI |
| `tests/audio/` | Contract + stability tests |
| `models/audio_intelligence/schema.json` | OpenAPI / JSON schema |

## Upload engine

Supported:

- Audio: MP3, WAV, M4A, AAC, OGG, FLAC
- Video: MP4, MOV, AVI, MKV, WebM
- Archives: ZIP, RAR

Capabilities:

- Drag-drop / multi-file / folder metadata
- Queue for 1000+ files
- Progress, ETA, retries

## Quality gate thresholds

| Check | Min |
|-------|-----|
| audio_quality_min | 55 |
| transcript_confidence_min | 0.55 |
| speaker_confidence_min | 0.55 |
| timestamp_coverage_min | 0.90 |
| evidence_integrity_min | 0.90 |

Quality analysis dimensions: noise, echo, distortion, clipping, silence, low volume, overlap.

## Repair engine

Actions: noise reduction, echo cancellation, volume normalization, clipping repair, silence optimization.

Stores `original_ref` + `repaired_ref`. Rollback restores original.

## Diarization rule

If confidence is below threshold → label **Unknown Speaker**. Never invent Agent/Customer roles.

## Transcript line contract

Each line includes:

- speaker
- start_sec / end_sec
- confidence
- original_text
- normalized_text
- dialect (north / central / south / unknown)

## Intelligence outputs

- Call stages (+ missing stages)
- Evidence (timestamp + speaker + transcript + rule_id) — no evidence → no deduction
- Emotion timeline (1s resolution, customer + agent)
- Buying signals (delivery / payment / warranty / invoice)
- Objections (context-based: price / trust / competitor / authority / delay)
- Interrupt classification (helpful / harmful / cooperative / cutting_off)
- Silence classification (thinking / searching / confusion / connection_issue) — not every pause is a fault
- Audio features (pitch/volume/tempo/WPM/pause/stress proxies)

## Batch / search / live

- Batch: 10 → 10,000 with resume
- Search: keyword, intent, emotion, objection, rule, agent, customer
- Live session: partial transcript + suggestion, latency budget ≤ 2s

## APIs (`/v1/audio-intelligence`)

- `POST /upload`
- `POST /process`
- `POST /repair` / `POST /repair/rollback`
- `POST /transcript` / `/diarization` / `/quality` / `/emotion` / `/evidence`
- `POST /export` (json / pdf / excel)
- `POST /batch` / `/search` / `/live`
- `GET /dashboard` / `GET /quality/snapshot`

OpenAPI tag: `audio-intelligence`

## Scoring integration

`scoring.py` calls `get_audio_engine().process(...)` and **aborts scoring** when `scoring_allowed` is false.

## Sprint mode

Sprint 17 owns AIE delivery. Quality gate must pass before advancing.

Reports:

- Sprint Report
- Audio Accuracy Report
- Risk Report
- Quality Report
- Next Sprint Plan

## V2 hardening (Sprint 17+)

- Enterprise search filters: keyword, intent, emotion, objection, rule, agent, customer
- Upload folder metadata (`folder_path` / relative folder enqueue)
- Emotion timeline with 1-second forward fill (`by_second`)
- Silence taxonomy with `risk_flag` (thinking ≠ automatic fault)
- Context-based objections (not keyword-only)
- Evidence integrity gate before scoring
- Payload aliases for batch/API clients (`file_info`, `turns`, `quality`)
- Bulk upload queue ETA/retry for 1000+ files

