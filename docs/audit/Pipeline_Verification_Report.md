# Pipeline Verification Report (Phase 3)

Generated: `2026-09-06T01:17:23.409312+00:00`  
Evidence: `reports/audit/phase_pipeline_imports.json`

### AIE pipeline smoke

```json
{
  "good_scoring_allowed": true,
  "bad_blocked": true,
  "evidence_fields_ok": true,
  "stages_present": true,
  "job_id": "aie_f3d9402543e1",
  "pipeline_order": [
    "upload",
    "validation",
    "audio_repair",
    "noise_removal",
    "voice_separation",
    "speaker_diarization",
    "transcript",
    "evidence_extraction",
    "quality_score",
    "ai_analysis"
  ]
}
```

| Check | Result |
|-------|--------|
| Good audio allows scoring | True |
| Bad audio blocks scoring | True |
| Evidence fields OK | True |
| Stages present | True |
| Scoring gate imports audio | True |
| Scoring gate blocks on fail | True |
| Pipeline order | `['upload', 'validation', 'audio_repair', 'noise_removal', 'voice_separation', 'speaker_diarization', 'transcript', 'evidence_extraction', 'quality_score', 'ai_analysis']` |

### Module import smoke

| Module | OK |
|--------|----|
| `audio_engine.engine` | True |
| `audio_pipeline.pipeline` | True |
| `audio_repair.repair` | True |
| `speaker.diarization` | True |
| `speaker.separation` | True |
| `transcript.engine` | True |
| `transcript.normalize` | True |
| `pragmatics` | True |
| `memory_graph` | True |
| `self_learning` | True |
| `digital_twin` | True |
| `negotiation` | True |
| `cltv` | True |
| `war_room` | True |
| `autonomous` | True |
| `sales_os` | True |

## Pipeline Integrity Score: **90/100**
