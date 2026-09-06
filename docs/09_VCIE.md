# 09 — Vietnamese Conversation Intelligence Engine (VCIE)

| Field | Value |
|-------|-------|
| Document ID | `DOC-ARCH-09-VCIE` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| Scale target | ≥ 5M scored calls / month; peak 2,000 concurrent ingest pipelines |
| Owners | ML Engineering, Backend Lead, QA Director |
| Related | Rulebook (DB), SOP Industries (`10_SOP_Industries.md`), Security (`14_Security.md`), Monitoring (`15_Monitoring.md`) |

---

## 1. Purpose

VCIE is the domain engine that turns Vietnamese telesales audio + transcript into **evidence-backed**, **rulebook-driven**, **explainable** conversation intelligence. It does **not** invent business rules. It extracts linguistic, acoustic, and conversational features and evaluates them against versioned Rulebook rows in PostgreSQL.

**Non-negotiable invariants**

1. No hallucination — conclusions require evidence spans with timestamps.
2. Rulebook only from DB (`rulebook.rules` + `rulebook.rule_versions`).
3. Missing signal → status `Insufficient Evidence`, never a fabricated pass/fail.
4. Every mutation (scores, appeals, recalibrations) writes an audit log.

---

## 2. Clean Architecture Placement

```
Interface (FastAPI / workers)
        ↓
Application (use-cases: AnalyzeCall, RecalibrateCall, AppealScore)
        ↓
Domain (VCIE services + ports)
        ↓
Infrastructure (STT adapters, Redis, Postgres repos, S3)
```

### 2.1 Domain modules (`ai-brain/vcie/`)

| Module | Responsibility | SOLID |
|--------|----------------|-------|
| `DialectDetector` | Classify Bắc / Trung / Nam; confidence + evidence | SRP |
| `IntentClassifier` | Per-turn intent taxonomy | SRP |
| `ObjectionDetector` | Objection type + handling quality features | SRP |
| `BuyingSignalDetector` | Soft/hard buying signals | SRP |
| `EmotionTimelineBuilder` | Time-series emotion / valence / arousal | SRP |
| `SilenceInterruptAnalyzer` | Silence, overlap, interrupt metrics | SRP |
| `ContextInferencer` | Product/stage/persona context from evidence only | SRP |
| `VcieOrchestrator` | Compose detectors; emit `VcieFeatures` | Facade + DI |
| `RuleEvaluationService` | Apply Rulebook versions to features | OCP via DB rules |

Ports (interfaces):

```python
# domain/ports/vcie_ports.py
class ITranscriptRepository(Protocol): ...
class IAudioFeatureRepository(Protocol): ...
class IRulebookRepository(Protocol): ...
class IVcieFeatureStore(Protocol): ...
class IAuditLogger(Protocol): ...
class ISttAdapter(Protocol): ...
class IProsodyAdapter(Protocol): ...
```

Composition root wires implementations in `backend/app/composition.py`.

---

## 3. Pipeline (millions-of-calls scale)

```
[Ingest API] → Redis Stream `call.ingest`
     → Worker: AudioNormalize → S3 put
     → Worker: STT+Diarization (GPU pool)
     → Worker: ProsodyExtract
     → Worker: VCIE Feature Extract
     → Worker: Rulebook Evaluate
     → Worker: Score Persist + Index
     → Event: `call.scored` → Analytics / Coaching / Alerts
```

### 3.1 Throughput design

| Stage | Horizontal unit | Target | Backpressure |
|-------|-----------------|--------|--------------|
| Ingest | FastAPI replicas | 5k RPS metadata | HTTP 429 + retry-after |
| STT | GPU workers (g5/L4) | 120h audio / hour / GPU | Redis consumer groups |
| Prosody | CPU workers | 300 concurrent | Queue lag alert > 5m |
| VCIE NLP | CPU/GPU hybrid | 2k calls / min | Shard by `tenant_id` |
| Rule eval | CPU | 10k calls / min | Stateless; cache rule versions in Redis |

Idempotency key: `(tenant_id, call_id, pipeline_version, rulebook_version)`.

---

## 4. Standard Analysis Response Envelope

Every scoring / VCIE analysis API **MUST** return this shape (OpenAPI component `AnalysisResult`):

```json
{
  "meta": {
    "call_id": "uuid",
    "tenant_id": "uuid",
    "analyzed_at": "2026-09-05T13:00:00.000Z",
    "pipeline_version": "vcie-3.2.0",
    "rulebook_version": "rb-2026.09.01-a3f2",
    "sop_id": "sop.bds.primary.v12",
    "industry_code": "BDS",
    "status": "scored",
    "trace_id": "01J..."
  },
  "score": 78.5,
  "stage_scores": {
    "opening": 82.0,
    "discovery": 71.0,
    "pitch": 75.0,
    "objection": 68.0,
    "close": 80.0,
    "outro": 90.0
  },
  "violations": [
    {
      "rule_id": "rule.bds.price_disclosure.required",
      "severity": "major",
      "status": "fail",
      "message": "Không nêu rõ đơn giá / m² trong giai đoạn pitch",
      "evidence_refs": ["ev_01"],
      "deduction": 8.0
    }
  ],
  "evidence": [
    {
      "evidence_id": "ev_01",
      "type": "transcript_span",
      "speaker": "agent",
      "quote": "Em gửi chị căn 2PN view sông ạ",
      "audio_ts_start": 142.35,
      "audio_ts_end": 146.10,
      "turn_index": 18,
      "confidence": 0.91,
      "feature_keys": ["intent.pitch_property", "dialect.nam"]
    }
  ],
  "root_cause": {
    "primary_code": "RC_OBJECTION_PRICE_UNHANDLED",
    "label": "Từ chối giá chưa được xử lý bằng kỹ thuật đối chiếu giá trị",
    "confidence": 0.86,
    "contributing_factors": [
      "silence_after_price > 4.2s",
      "no_buying_signal_ack"
    ],
    "evidence_refs": ["ev_12", "ev_13"],
    "status": "ok"
  },
  "coaching": {
    "priority": "high",
    "tips": [
      {
        "tip_id": "tip.obj.price.reframe",
        "title": "Tái khung giá theo giá trị / m² và tiện ích",
        "script_suggestion": "Anh/chị so với căn cùng view thì mức này đang thấp hơn khoảng X vì...",
        "linked_rule_ids": ["rule.bds.objection.price"],
        "evidence_refs": ["ev_12"]
      }
    ],
    "drill_ids": ["drill.objection.price.bds"]
  },
  "revenue_leak": {
    "estimated_loss_vnd": 15000000,
    "leak_codes": ["LEAK_NO_ALTERNATIVE_OFFER", "LEAK_SOFT_CLOSE_MISSING"],
    "probability": 0.62,
    "explanation": "Khách có buying signal 'chốt tuần này' nhưng agent không đề xuất booking giữ chỗ.",
    "evidence_refs": ["ev_20"],
    "status": "ok"
  },
  "vcie": { }
}
```

### 4.1 Insufficient Evidence contract

When a detector cannot support a conclusion:

```json
{
  "root_cause": {
    "primary_code": null,
    "label": null,
    "confidence": null,
    "contributing_factors": [],
    "evidence_refs": [],
    "status": "Insufficient Evidence",
    "reason": "Prosody channel missing; emotion timeline unavailable"
  }
}
```

Partial scoring is allowed: stage scores present for stages with evidence; stages without evidence use `"status": "Insufficient Evidence"` inside `stage_scores_detail` (see §8). Overall `score` may be `null` with `meta.status = "partial"` when required rules cannot be evaluated.

---

## 5. Dialect Engine (Bắc / Trung / Nam)

### 5.1 Taxonomy

| Code | Region | Lexical markers (examples) | Phonetic cues |
|------|--------|----------------------------|---------------|
| `bac` | Bắc | `cháu`, `ạ`, `nhé`, `không ạ` | sắc/hỏi distinction; `/z/` vs `/j/` |
| `trung` | Trung | `mi`, `tau`, `răng`, `chi`, `nớ` | nặng/hỏi merge tendencies |
| `nam` | Nam | `dạ`, `hen`, `hông`, `rứa`, `đặng` | hỏi/ngã; final consonant softening |

Mixed calls: per-turn dialect + call-level majority with `dialect_mix_ratio`.

### 5.2 Schema `vcie.dialect_result`

```sql
CREATE TABLE vcie.dialect_results (
  id              UUID PRIMARY KEY,
  call_id         UUID NOT NULL REFERENCES calls.id,
  call_level_code VARCHAR(8) NOT NULL, -- bac|trung|nam|mixed|unknown
  confidence      NUMERIC(5,4),
  mix_ratio       JSONB NOT NULL DEFAULT '{}', -- {"bac":0.2,"nam":0.8}
  evidence        JSONB NOT NULL, -- array of evidence objects
  model_version   VARCHAR(64) NOT NULL,
  status          VARCHAR(32) NOT NULL, -- ok|Insufficient Evidence
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_dialect_call ON vcie.dialect_results(call_id);
```

### 5.3 Rulebook linkage

Rules may constrain dialect-aware scripts, e.g. `rule.*.greeting.honorific` evaluates differently when `dialect=nam` vs `bac` **only if** the Rulebook row declares `conditions.dialect_in`. Code must not hardcode honorific lists.

---

## 6. Intent Taxonomy

Canonical intents (extensible via DB table `vcie.intent_catalog`, not hardcoded forever in prompts):

| Intent code | Speaker | Description |
|-------------|---------|-------------|
| `agent.greeting` | agent | Opening / chào |
| `agent.permission` | agent | Xin phép tiếp tục |
| `agent.discovery_need` | agent | Hỏi nhu cầu |
| `agent.pitch` | agent | Giới thiệu SP |
| `agent.price_quote` | agent | Báo giá |
| `agent.objection_handle` | agent | Xử lý từ chối |
| `agent.close_ask` | agent | Chốt / CTA |
| `agent.confirm_info` | agent | Xác nhận PII / địa chỉ |
| `agent.schedule` | agent | Hẹn callback / booking |
| `customer.greeting` | customer | Đáp chào |
| `customer.interest` | customer | Quan tâm |
| `customer.objection` | customer | Từ chối / e ngại |
| `customer.question` | customer | Câu hỏi |
| `customer.buying_signal` | customer | Tín hiệu mua |
| `customer.refuse` | customer | Từ chối cứng |
| `customer.defer` | customer | Trì hoãn |
| `other` | either | Không phân loại được |

Per-turn output:

```json
{
  "turn_index": 12,
  "intent": "customer.objection",
  "confidence": 0.88,
  "audio_ts_start": 98.1,
  "audio_ts_end": 101.4,
  "evidence_quote": "Giá cao quá em ơi",
  "status": "ok"
}
```

---

## 7. Objections

### 7.1 Objection types (catalog DB)

| Code | Label VI | Typical evidence patterns |
|------|----------|---------------------------|
| `OBJ_PRICE` | Giá cao | `giá`, `đắt`, `cao quá`, `không đủ tiền` |
| `OBJ_TIME` | Không có thời gian | `bận`, `đang họp`, `gọi lại` |
| `OBJ_TRUST` | Thiếu tin tưởng | `lừa đảo`, `chưa nghe tên`, `review` |
| `OBJ_NEED` | Không cần | `không cần`, `đã có rồi` |
| `OBJ_SPOUSE` | Phải hỏi người nhà | `hỏi chồng`, `hỏi vợ`, `gia đình` |
| `OBJ_COMPARE` | So sánh đối thủ | `bên kia`, `rẻ hơn` |
| `OBJ_DELIVERY` | Giao hàng / vận chuyển | `ship`, `giao`, `bao lâu` |
| `OBJ_QUALITY` | Chất lượng / hiệu quả | `có hiệu quả không`, `bảo hành` |
| `OBJ_LEGAL` | Pháp lý / giấy tờ (BĐS, BH) | `sổ`, `pháp lý`, `hợp đồng` |
| `OBJ_OTHER` | Khác | requires human tag if confidence < 0.55 |

### 7.2 Handling quality features (not verdicts)

VCIE emits **features**; Rulebook decides pass/fail:

- `ack_present` — agent acknowledged objection
- `empathy_present`
- `reframe_present`
- `evidence_offer_present` (proof, case, warranty)
- `alternative_offer_present`
- `close_retry_present`
- `latency_to_response_sec`
- `talk_over_customer` boolean

---

## 8. Buying Signals

| Strength | Codes | Examples |
|----------|-------|----------|
| Soft | `BS_ASK_PRICE`, `BS_ASK_PROMO`, `BS_ASK_SHIP`, `BS_REPEAT_FEATURE` | Hỏi chi tiết / khuyến mãi |
| Hard | `BS_ASK_PAYMENT`, `BS_GIVE_ADDRESS`, `BS_ASK_CONTRACT`, `BS_BOOK_SLOT` | Địa chỉ, thanh toán, đặt lịch |
| Negative | `BS_HARD_NO`, `BS_DO_NOT_CALL` | Cấm gọi / từ chối tuyệt đối |

Missed buying signal → feeds `revenue_leak` via Rulebook rules such as `rule.*.buying_signal.ack_required`.

---

## 9. Emotion Timeline

### 9.1 Sources

1. Text valence lexicon + classifier (always when transcript exists).
2. Prosody: pitch mean/var, energy, speaking rate (when audio available).
3. Fusion model outputs `valence ∈ [-1,1]`, `arousal ∈ [0,1]`, `label ∈ {calm, positive, frustrated, angry, confused, anxious, excited}`.

### 9.2 Series schema

```json
{
  "window_sec": 5,
  "points": [
    {
      "t": 0,
      "valence": 0.1,
      "arousal": 0.3,
      "label": "calm",
      "speaker": "customer",
      "confidence": 0.7,
      "source": "text+prosody"
    }
  ],
  "status": "ok"
}
```

If audio absent: `source=text_only`, confidence capped at `0.65`. If neither usable: `status: Insufficient Evidence`.

UI consumes this for heatmaps (see `12_UI_UX.md`).

---

## 10. Silence & Interrupt Analysis

| Metric | Definition | Use |
|--------|------------|-----|
| `silence_total_sec` | Non-speech gaps > 0.4s | Dead air coaching |
| `max_silence_sec` | Longest gap | Post-price silence risk |
| `silence_after_price_sec` | Gap after price quote | Objection / leak |
| `agent_overlap_ratio` | Overlap time / call | Interrupt violations |
| `customer_interrupt_count` | Customer cuts agent | Frustration signal |
| `agent_interrupt_count` | Agent cuts customer | Soft-skill rules |
| `avg_response_latency_sec` | Customer end → agent start | Responsiveness KPI |

Evidence for each extreme event includes `audio_ts_start/end`.

---

## 11. Context Inference

Allowed inferences (evidence-gated):

| Context key | Evidence requirement |
|-------------|----------------------|
| `product_mentioned` | Explicit product name span |
| `campaign_code` | CRM metadata or explicit code in speech |
| `customer_persona` | Only if stated (`mẹ bầu`, `chủ nhà`…) else Insufficient Evidence |
| `call_stage_map` | Derived from intent sequence + time |
| `prior_call_ref` | CRM link or verbal reference with quote |

Forbidden: inventing customer income, marital status, or intent to buy without spans.

---

## 12. Link to Rulebook

### 12.1 Evaluation algorithm

```
features = VcieOrchestrator.extract(call)
rules = RulebookRepository.get_active(tenant_id, industry_code, sop_id, at=analyzed_at)
for rule in rules:
  if not rule.applies(features.context):
    continue
  result = rule.evaluate(features)  # pure function from DB DSL
  if result.needs_evidence and not result.evidence:
    emit Insufficient Evidence for that rule
  else:
    attach evidence_refs, score delta, violation if any
aggregate → AnalysisResult envelope
audit: rule_version, feature_hash, score
```

### 12.2 Rule DSL (stored in DB)

```json
{
  "rule_id": "rule.bds.objection.price.handle",
  "when": {
    "all": [
      {"feature": "objections.types", "contains": "OBJ_PRICE"},
      {"feature": "stages.objection.present", "eq": true}
    ]
  },
  "require": {
    "all": [
      {"feature": "objections.handling.ack_present", "eq": true},
      {"feature": "objections.handling.reframe_present", "eq": true}
    ]
  },
  "on_fail": {"severity": "major", "deduction": 10},
  "on_missing_feature": "Insufficient Evidence"
}
```

### 12.3 API paths

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/calls/{call_id}/analyze` | Full VCIE + rule eval |
| `GET` | `/api/v1/calls/{call_id}/analysis` | Latest AnalysisResult |
| `GET` | `/api/v1/calls/{call_id}/vcie/timeline` | Emotion + silence timeline |
| `GET` | `/api/v1/calls/{call_id}/vcie/intents` | Intent sequence |
| `POST` | `/api/v1/calls/{call_id}/reanalyze` | Re-run with new rulebook version |
| `GET` | `/api/v1/rulebook/versions/{version}/diff` | Audit-friendly rule diff |

All secured with JWT + RBAC (`qa_analyst`, `qa_manager`, `admin`, `ml_ops`).

---

## 13. Persistence model (core tables)

```sql
CREATE SCHEMA vcie;

CREATE TABLE vcie.features (
  call_id UUID PRIMARY KEY REFERENCES calls.id,
  dialect JSONB NOT NULL,
  intents JSONB NOT NULL,
  objections JSONB NOT NULL,
  buying_signals JSONB NOT NULL,
  emotion_timeline JSONB NOT NULL,
  silence_interrupt JSONB NOT NULL,
  context JSONB NOT NULL,
  feature_hash CHAR(64) NOT NULL,
  extractor_version VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analyses (
  id UUID PRIMARY KEY,
  call_id UUID NOT NULL REFERENCES calls.id,
  tenant_id UUID NOT NULL,
  score NUMERIC(5,2),
  stage_scores JSONB NOT NULL,
  violations JSONB NOT NULL,
  evidence JSONB NOT NULL,
  root_cause JSONB NOT NULL,
  coaching JSONB NOT NULL,
  revenue_leak JSONB NOT NULL,
  vcie_snapshot JSONB NOT NULL,
  rulebook_version VARCHAR(64) NOT NULL,
  pipeline_version VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_analyses_tenant_created ON analyses(tenant_id, created_at DESC);
CREATE INDEX idx_analyses_call ON analyses(call_id);
```

---

## 14. Service interfaces (Python)

```python
class VcieOrchestrator:
    def __init__(
        self,
        dialect: DialectDetector,
        intents: IntentClassifier,
        objections: ObjectionDetector,
        buying: BuyingSignalDetector,
        emotion: EmotionTimelineBuilder,
        silence: SilenceInterruptAnalyzer,
        context: ContextInferencer,
        audit: IAuditLogger,
    ): ...

    async def extract(self, call: CallBundle) -> VcieFeatures:
        """Never mutates Rulebook. Emits features + evidence only."""
        ...

class RuleEvaluationService:
    def __init__(self, rules: IRulebookRepository, audit: IAuditLogger): ...

    async def evaluate(
        self, features: VcieFeatures, sop_id: str, rulebook_version: str | None
    ) -> AnalysisResult:
        ...
```

---

## 15. Calibration & Human-in-the-loop

- Sample 2–5% of calls / tenant / week for dual annotation (QA humans).
- Cohen’s κ tracked per intent / objection / dialect.
- Drift alert when production vs gold F1 drops > 5 points.
- Appeal mode (`11_Test_Cases.md`) can freeze a score and attach counter-evidence; re-eval requires `qa_manager`.

---

## 16. Failure modes

| Failure | Behavior |
|---------|----------|
| STT empty | `meta.status=failed_input`; no score |
| Partial diarization | Intents with `speaker=unknown`; rules needing speaker → Insufficient Evidence |
| Rulebook empty for industry | HTTP 422 `RULEBOOK_NOT_CONFIGURED`; no invented rules |
| Redis down | Degrade: sync path for priority tenants; else 503 ready=false |
| Model timeout | Retry 2x; then partial features + Insufficient Evidence for missing blocks |

---

## 17. Acceptance criteria

1. AnalysisResult always includes `score`, `stage_scores`, `violations`, `evidence`, `root_cause`, `coaching`, `revenue_leak`.
2. Every `fail` violation has ≥1 evidence ref with timestamps.
3. Dialect accuracy ≥ 90% on gold set (≥2k labeled turns).
4. Objection type macro-F1 ≥ 0.85 on gold set.
5. Zero hard-coded industry pass/fail thresholds in application code.
6. p95 analyze latency (features cached) ≤ 2.5s; end-to-end audio≤10min call ≤ 90s p95.
7. Audit log present for every analyze / reanalyze / appeal decision.

---

## 18. OpenAPI component reference

Component names (FastAPI models): `AnalysisResult`, `EvidenceSpan`, `Violation`, `RootCause`, `CoachingPlan`, `RevenueLeak`, `VcieFeatures`, `InsufficientEvidenceStatus`.

Exported at `/openapi.json` and published to the developer portal on each release.
