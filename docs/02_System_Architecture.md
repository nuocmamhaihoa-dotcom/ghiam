# 02 — System Architecture
## AI QA TELESALE ENTERPRISE

**Phiên bản:** 1.0.0  
**Liên kết:** [01_PRD.md](./01_PRD.md) · [03_Database.md](./03_Database.md) · [04_API_Spec.md](./04_API_Spec.md)  
**Nguyên tắc:** Clean Architecture · SOLID · Repository · Service · DI · Docker Compose · JWT/RBAC · OpenAPI · Observability

---

## 1. Mục tiêu kiến trúc

Thiết kế hệ thống **scalable tới hàng triệu cuộc gọi**, multi-tenant, evidence-first, không hardcode business rules. Domain logic độc lập framework; infra (PostgreSQL, Redis, S3, Whisper, LLM) Isolated sau interfaces.

### 1.1 Goals
- Pipeline cố định end-to-end với stage observability.
- Horizontal scale workers độc lập API.
- Fail-soft → `Insufficient Evidence` thay vì điểm giả.
- Mọi mutation audit được.
- Local = Docker Compose; prod = same images.

### 1.2 Non-goals
- Monolith nhúng STT trong API process.
- Shared DB schema giữa tenants không có `tenant_id`.
- Business rules trong Next.js hoặc prompt tĩnh.

---

## 2. Context Diagram (C4 L1)

```
[Dialer/CRM] ──audio+meta──► [AQATE API]
[Agents/QA]  ◄──HTTPS──────► [Next.js Web]
[AQATE API] ──jobs──► [Redis Queues] ──► [Pipeline Workers]
[Workers] ──R/W──► [PostgreSQL] [S3] [STT/Diarization] [LLM Gateway]
[All services] ──metrics/logs──► [Prometheus/Loki/OTel]
```

**Actors:** Agent, Team Lead, QA, Coach, Revenue Ops, Compliance, Tenant Admin, SRE, CRM/Dialer, Pipeline SA.

---

## 3. Clean Architecture Layers

```
Interface (FastAPI routers, Next.js BFF adapters)
        ↓
Application (Use-cases / Services: ScoreCall, PublishRulebook, GenerateCoaching)
        ↓
Domain (Entities: Call, Rule, EvidenceSpan, ScoreItem, RootCause, CoachingPlan)
        ↓
Infrastructure (Postgres repos, Redis, S3, Whisper client, LLM client, clock, uow)
```

**Dependency rule:** dependencies chỉ hướng vào trong. Domain **không** import FastAPI, SQLAlchemy, Redis, OpenAI SDK.

### 3.1 SOLID mapping
| Principle | Áp dụng |
|-----------|---------|
| SRP | Một worker stage = một responsibility; ScoreService ≠ CoachingService |
| OCP | Thêm `evaluator_type` mới qua strategy đăng ký DI, không sửa Rule Engine core |
| LSP | Mọi `StoragePort` (S3/MinIO) thay thế được |
| ISP | `TranscriptRepository` tách khỏi `AuditRepository` |
| DIP | Use-case phụ thuộc `RuleRepository` protocol, không ORM |

### 3.2 Composition Root
`backend/app/container.py` (hoặc tương đương) wire tất cả repositories/clients. Cấm singleton ẩn state; config qua settings object immutable.

---

## 4. Logical Components

| Component | Tech | Responsibility |
|-----------|------|----------------|
| `web` | Next.js + TS + Tailwind | UI Dashboard, Workbench, Rulebook admin (no business rules) |
| `api` | FastAPI | Auth, CRUD, query scorecards, enqueue jobs, OpenAPI |
| `worker-ingest` | Python | Validate media, store S3, create call rows |
| `worker-stt` | Python + GPU | Whisper transcription |
| `worker-diarization` | Python + GPU | Speaker labels |
| `worker-nlp` | Python | Normalization, segmentation, evidence extraction |
| `worker-score` | Python | Rule Engine + LLM Judge orchestration |
| `worker-insight` | Python | Root Cause + Coaching + Revenue Leak |
| `postgres` | PostgreSQL 16 | System of record |
| `redis` | Redis 7 | Queue, cache, rate limit, distributed locks |
| `minio` | S3-compatible | Audio, artifacts, JSON dumps |
| `llm-gateway` | FastAPI sidecar | Model routing, timeouts, token budget |
| `otel-collector` | OTel | Traces/metrics export |

---

## 5. Fixed Pipeline Architecture (bắt buộc)

```
Audio
  → Whisper
  → Speaker Diarization
  → Transcript Normalization
  → Semantic Segmentation
  → Evidence Extraction
  → Rule Engine
  → LLM Judge
  → Root Cause Engine
  → Coaching Engine
  → Revenue Leak AI
  → Dashboard
  → JSON Output
```

### 5.1 Stage contracts

Mỗi stage implement `PipelineStage` protocol:

```python
class PipelineStage(Protocol):
    name: str
    async def run(self, ctx: PipelineContext) -> StageResult: ...
```

`PipelineContext` mang: `tenant_id`, `call_id`, `trace_id`, `artifact_refs`, `rulebook_release_id`, `config_snapshot_id`.

`StageResult`:
```json
{
  "stage": "whisper",
  "status": "succeeded|degraded_ok|failed|skipped",
  "started_at": "2026-09-05T10:00:00.000Z",
  "finished_at": "2026-09-05T10:01:12.000Z",
  "error_code": null,
  "metrics": {"realtime_factor": 0.42, "avg_confidence": 0.91},
  "output_uri": "s3://bucket/tenant/.../whisper.json"
}
```

### 5.2 Stage details

#### S1 — Audio Ingest (pre-Whisper)
- Validate MIME, duration, sample rate, checksum SHA-256.
- Store original + normalized WAV/FLAC in S3.
- Failure → call status `failed` / `insufficient_evidence` nếu file rỗng.

#### S2 — Whisper (STT)
- Input: normalized audio URI.
- Output: word/segment timestamps + confidence.
- Gate: nếu `avg_confidence < threshold` (từ `tenant_ai_configs`) → flag `stt_low_confidence`.

#### S3 — Speaker Diarization
- Map speakers → `agent` | `customer` | `unknown` dùng voice profile nếu có; else heuristic + CRM `agent_channel` hint.
- Low confidence spans → `unknown`.

#### S4 — Transcript Normalization
- Punctuation, number normalization (vi), filler handling, PII redact.
- Output: `normalized_turns[]` immutably versioned.

#### S5 — Semantic Segmentation
- Gán `stage_key`: `opening|discovery|pitch|objection|close|outro|other`.
- Không chấm điểm ở stage này — chỉ segment.

#### S6 — Evidence Extraction
- Rút candidates theo `evidence_requirements` của rules trong release (loaded from DB).
- Mỗi evidence: `quote`, `turn_index`, `audio_ts_start`, `audio_ts_end`, `confidence`, `extractor_id`.

#### S7 — Rule Engine
- Evaluate deterministic / ML extractors declared on rules.
- **Không** đọc hardcode list trong source; load `rulebook_releases.snapshot`.
- Emit `rule_hits[]` / `rule_misses[]` / `rule_ie[]`.

#### S8 — LLM Judge
- Chỉ adjudicate ambiguous items với evidence pack bị ràng buộc.
- System prompt **cấm** nhúng business thresholds; thresholds lấy từ rule payload.
- Output phải cite `evidence_id`; nếu không cite được → `Insufficient Evidence`.

#### S9 — Root Cause Engine
- Map fail/IE patterns → cause codes (DB taxonomy).
- Primary + contributing causes với confidence.

#### S10 — Coaching Engine
- Generate plan items từ causes + historical weak skills (DB).
- Templates từ DB (`coaching_templates`), không hardcode tip nghiệp vụ trong code.

#### S11 — Revenue Leak AI
- Ước lượng leak từ miss rules có `revenue_impact_model` trong DB.
- Nếu thiếu CRM value → leak = IE / `unestimated`.

#### S12 — Dashboard + JSON Output
- Persist `call_analyses` + artifact JSON.
- Web reads via API; realtime invalidate cache Redis.
- Emit webhook `analysis.completed`.

### 5.3 Orchestration
- Redis Streams / RQ / Celery-equivalent: queue per stage group.
- Orchestrator service updates `pipeline_runs` + `pipeline_stage_runs`.
- Idempotency key: `(tenant_id, call_id, stage, attempt_fingerprint)`.
- Poison messages → DLQ + alert.

---

## 6. Sequence — Happy Path

```
CRM  → API.POST /v1/calls
API  → S3 put audio, DB insert calls(received), Redis enqueue ingest
Worker-STT → whisper artifact
Worker-Diar → diarized transcript
Worker-NLP → normalized + segments + evidences
Worker-Score → load rulebook release → rule engine → llm judge → scorecard
Worker-Insight → root cause → coaching → leak
API  ← status scored; Webhook → CRM
Web  → GET /v1/calls/{id}/analysis
```

---

## 7. Multi-Tenancy

- **Shared DB, shared schema, mandatory `tenant_id`** trên mọi bảng nghiệp vụ.
- S3 prefix: `s3://{bucket}/{tenant_id}/calls/{call_id}/...`
- Redis keys: `t:{tenant_id}:...`
- JWT claim `tenant_id` + role; repository layer enforces tenant filter (unit-tested).
- Optional: schema-per-tenant cho enterprise contract (phase 2) — cùng interfaces.

---

## 8. AuthN / AuthZ

- **AuthN:** JWT Bearer (access TTL short) + refresh rotation; optional OIDC SSO.
- **AuthZ:** RBAC permissions dạng `resource:action` (`scorecard:read`, `rulebook:publish`, …).
- FastAPI dependencies inject `CurrentUser` + `PermissionChecker`.
- Service accounts cho workers: scoped JWT / mTLS internal.

---

## 9. Data Flow & Artifacts

| Artifact | Store | Format |
|----------|-------|--------|
| Original audio | S3 | source ext |
| Normalized audio | S3 | flac/wav |
| Whisper JSON | S3 + summary DB | json |
| Diarized transcript | S3 + `transcripts` | json |
| Evidence pack | S3 + `evidence_spans` | json/rows |
| Scorecard | `scorecards` + S3 | json |
| Analysis result | `call_analyses` | jsonb |
| Audit | `audit_logs` | row |

---

## 10. Caching Strategy

| Key | TTL | Purpose |
|-----|-----|---------|
| `t:{id}:rulebook:active` | until invalidate | Hot rule snapshot |
| `t:{id}:call:{id}:analysis` | 5–15 min | Dashboard read |
| `t:{id}:dash:team:{id}` | 60s | Aggregates |
| Rate limit buckets | sliding | API abuse protection |

Invalidate on publish rulebook / finalize override.

---

## 11. Deployment Topology

### 11.1 Docker Compose (local/dev/agent)
Services: `web`, `api`, `worker_all` (or split), `postgres`, `redis`, `minio`, `llm_gateway` (mock), `prometheus`, `grafana`.

Env: `CONVEX_AGENT_MODE` không áp dụng (stack là FastAPI/PG). Secrets qua `.env` không commit.

### 11.2 Production
- K8s hoặc VM ASG: API HPA by RPS; GPU node pool for STT/diarization.
- Managed Postgres + Redis; S3.
- Blue/green API; workers drain before deploy.
- Migrations via Alembic in init job — never from API hot path.

---

## 12. Observability Architecture

### 12.1 Logging
JSON stdout → collector:

```json
{
  "timestamp": "2026-09-05T10:01:02.123Z",
  "level": "INFO",
  "service": "worker-score",
  "trace_id": "1a2b...",
  "tenant_id": "tn_...",
  "actor_id": "svc_pipeline",
  "action": "rule_engine.evaluate",
  "call_id": "cl_...",
  "rulebook_release_id": "rr_...",
  "message": "evaluated 1042 rules"
}
```

### 12.2 Health
- `GET /health` — process up.
- `GET /ready` — Postgres ping, Redis ping, S3 head bucket, optional LLM ping.

### 12.3 Metrics (examples)
`pipeline_stage_duration_seconds{stage=}`  
`pipeline_stage_failures_total{stage=,error_code=}`  
`queue_lag_seconds{queue=}`  
`insufficient_evidence_ratio{tenant=}`  
`score_throughput_calls_per_minute`  
`audit_events_total{action=}`

### 12.4 Alerts
- Queue lag > 10 min (P1).
- IE ratio spike > 2× baseline (P2 — data quality).
- Ready failing (P1).
- S3 put error rate (P1).
- Rulebook missing on score attempt (P0).

---

## 13. Failure Modes & Degradation

| Failure | System behavior |
|---------|-----------------|
| Whisper down | Retry → DLQ; call `failed`; no fake transcript |
| Diarization fail | Proceed with single-speaker unknown → many IE |
| Redis down | API read-only degrade if PG up; enqueue paused; alert |
| PG primary fail | Failover; API 503 on writes |
| LLM gateway down | Deterministic rule results only; judge-required → IE |
| S3 slow | Timeout + retry; circuit breaker |
| Partial evidence | Per-rule IE; overall status `insufficient_evidence` if required auto-fail rules IE |

**Never:** invent transcript lines, invent prices, invent customer consent.

---

## 14. Security Architecture

- Network: web → api public; workers/DB/redis private.
- JWT validation on all `/v1/*` except health/ready/openapi (openapi có thể restrict prod).
- Input validation Pydantic; max upload size; antivirus hook optional.
- PII redaction stage before LLM if policy `redact_before_llm=true`.
- Audit immutable append-only (update/delete denied at app layer).

---

## 15. Frontend Architecture (Next.js)

```
app/(auth)/login
app/(app)/dashboard
app/(app)/calls/[id]          # timeline + scorecard + evidence
app/(app)/coaching
app/(app)/rulebook            # qa_director only
app/(app)/revenue-leak
app/(app)/admin
```

- Data via typed OpenAPI client.
- **Cấm** nhúng regex/rule nghiệp vụ trong `src/`; chỉ presentation + form validation UX.
- Server components cho read; client cho interactive timeline.
- Tailwind design tokens; RBAC-gated nav.

---

## 16. Backend Package Layout (Clean Architecture)

```
backend/
  app/
    main.py                 # composition + FastAPI
    container.py            # DI
    interfaces/api/         # routers, schemas, deps
    application/            # use cases / services
    domain/                 # entities, value objects, ports
    infrastructure/
      db/                   # SQLAlchemy models, repositories
      redis/
      s3/
      stt/
      llm/
      logging/
  workers/
    pipeline/
      stages/
  tests/
```

---

## 17. JSON Output — Canonical Envelope

Persist + API return shape (rút gọn; chi tiết Scoring/API docs):

```json
{
  "schema_version": "1.0.0",
  "tenant_id": "tn_01H...",
  "call_id": "cl_01H...",
  "status": "scored",
  "rulebook_release_id": "rr_01H...",
  "pipeline_stages": [
    {
      "stage": "whisper",
      "status": "succeeded",
      "started_at": "2026-09-05T10:00:00Z",
      "finished_at": "2026-09-05T10:01:00Z"
    }
  ],
  "scorecard": {
    "overall_score": 86.5,
    "result": "pass",
    "items": [
      {
        "rule_code": "R-OPEN-001",
        "verdict": "pass",
        "score": 1.0,
        "confidence": 0.93,
        "evidence_spans": [
          {
            "quote": "Hello chị ạ",
            "audio_ts_start": 0.8,
            "audio_ts_end": 1.6,
            "turn_index": 0
          }
        ],
        "evaluated_at": "2026-09-05T10:05:01Z",
        "explanation": "Rule R-OPEN-001 matched greeting evidence."
      },
      {
        "rule_code": "R-ADDR-001",
        "verdict": "Insufficient Evidence",
        "score": null,
        "confidence": 0.0,
        "evidence_spans": [],
        "evaluated_at": "2026-09-05T10:05:02Z",
        "explanation": "No address confirmation span found."
      }
    ]
  },
  "root_causes": [],
  "coaching": {},
  "revenue_leak": {},
  "generated_at": "2026-09-05T10:06:00Z"
}
```

---

## 18. Sprint Alignment với Architecture

| Sprint theme | Architecture deliverable |
|--------------|--------------------------|
| Foundation | Compose, DI container, health, audit table |
| Ingest+STT | S1–S3 workers |
| NLP | S4–S6 |
| Scoring | S7–S8 + Rulebook API |
| Insights | S9–S11 |
| Web | Dashboard + call detail |
| Hardening | Calibration, DLQ, autoscaling, chaos |

Mỗi PR qua constitution checklist trước merge.

---

## 19. NFR Traceability

| NFR (PRD) | Architecture control |
|-----------|----------------------|
| 1M calls/mo | Queue partition + worker HPA |
| p95 pipeline < 8m | Stage SLOs + GPU pool + skip optional prosody |
| 99.9% API | Multi-instance + PG HA |
| Evidence-first | Stage S6 mandatory before score persist |
| No hardcoded rules | Rule repository + CI grep gates |
| Audit | Middleware + domain events |

---

## 20. ADRs (tóm tắt)

| ADR | Decision |
|-----|----------|
| ADR-001 | PostgreSQL system of record (not Convex) for enterprise QA |
| ADR-002 | Redis Streams for pipeline orchestration |
| ADR-003 | Rulebook immutable releases |
| ADR-004 | LLM Judge evidence-bound only |
| ADR-005 | IE string canonical across API/UI |
| ADR-006 | Next.js presentation-only for scoring |

---

## 21. Document Control

| Field | Value |
|-------|-------|
| Owner | Chief Software Architect |
| Consumed by | Backend, ML, DevOps, Frontend leads |
| Evidence | PRD goals G1–G8; Constitution §§1–5 |

**Hết System Architecture v1.0.0**
