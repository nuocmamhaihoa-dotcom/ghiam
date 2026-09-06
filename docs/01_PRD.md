# 01 — Product Requirements Document (PRD)
## AI QA TELESALE ENTERPRISE

**Phiên bản:** 1.0.0  
**Trạng thái:** Approved for Architecture  
**Phạm vi:** Hệ thống QA/AI chấm điểm, phân tích nguyên nhân, coaching và phát hiện rò rỉ doanh thu cho telesale quy mô enterprise (hàng triệu cuộc gọi/năm).  
**Hiến pháp:** Tuân thủ tuyệt đối Project Constitution (Clean Architecture, evidence-first, Rulebook từ DB, Insufficient Evidence, Audit Log).

---

## 1. Tóm tắt điều hành (Executive Summary)

AI QA TELESALE ENTERPRISE (gọi tắt: **AQATE**) là nền tảng QA tự động cho cuộc gọi bán hàng qua điện thoại. Hệ thống nhận audio (hoặc transcript đã chuẩn hóa), chạy pipeline AI đa tầng, đánh giá theo Rulebook versioned trong PostgreSQL, sinh điểm giải thích được, root cause, coaching plan, và ước lượng revenue leak — tất cả gắn evidence + timestamp, không suy diễn.

**Giá trị cốt lõi**
- Thay thế / bổ sung QA thủ công với throughput hàng trăm nghìn cuộc gọi/ngày.
- Mọi điểm và kết luận đều truy xuất được tới span transcript/audio và `rule_id`.
- Rule nghiệp vụ **không hardcode** trong code; chỉ sống trong Rulebook DB có version + audit.
- Khi thiếu dữ liệu: trả về đúng chuỗi **`Insufficient Evidence`** — không đoán, không mặc định “đạt”.

---

## 2. Vấn đề nghiệp vụ (Problem Statement)

### 2.1 Hiện trạng
- QA thủ công mẫu 1–5% cuộc gọi → blind spot lớn.
- Tiêu chí chấm điểm nằm trong Excel / SOP PDF / “trong đầu coach” → không version, không audit.
- Sale nhận feedback muộn (ngày–tuần), không gắn evidence cụ thể.
- Không có hệ thống đo “rò rỉ doanh thu” (missed close, soft-yes không follow-up, objection mishandle).

### 2.2 Hậu quả
- Win-rate không cải thiện có hệ thống.
- Tranh chấp điểm giữa sale và QA không có bằng chứng.
- Compliance risk (không chào, không xác nhận giá/địa chỉ, nói sai cam kết).
- Không scale khi team tăng từ 50 → 5.000 agents.

### 2.3 Cơ hội
Tự động hóa QA end-to-end với explainability pháp lý/nội bộ, coaching cá nhân hóa, và dashboard realtime cho Team Lead / QA Director / Revenue Ops.

---

## 3. Mục tiêu (Goals)

| ID | Mục tiêu | KPI đo |
|----|----------|--------|
| G1 | Chấm điểm 100% cuộc gọi inbound/outbound đủ audio | Coverage ≥ 99% cuộc gọi có trạng thái `scored` hoặc `insufficient_evidence` trong SLA |
| G2 | Mọi điểm giải thích được | 100% score item có `rule_id`, `evidence_spans`, `confidence`, timestamps |
| G3 | Không suy diễn / không hardcode rule | Audit CI + runtime: zero hardcoded business rule trong prompt/code path scoring |
| G4 | Coaching actionable trong 24h | ≥ 90% agents có coaching plan mới sau mỗi ca |
| G5 | Root cause có cấu trúc | ≥ 95% cuộc `lost`/`callback` có primary root cause code hoặc `Insufficient Evidence` |
| G6 | Revenue Leak AI | Dashboard leak ước lượng theo rule miss; calibrated quarterly vs CRM close |
| G7 | Scale | 1M+ calls/month / tenant; multi-tenant isolation |
| G8 | Audit & compliance | Mọi mutation có audit log; retention theo policy tenant |

---

## 4. Phi mục tiêu (Non-Goals)

- **Không** thay thế hoàn toàn human QA cho 100% edge cases pháp lý phức tạp (human-in-the-loop vẫn bắt buộc cho dispute & auto-fail compliance).
- **Không** xây dựng voicebot gọi thay sale (out of scope; có thể tích hợp sau qua API).
- **Không** lưu nội dung thẻ thanh toán / số CVV (PCI); nếu phát hiện → redact + flag.
- **Không** dùng rule hardcode trong frontend Next.js hoặc prompt tĩnh làm nguồn sự thật.
- **Không** suy diễn cảm xúc khách hàng khi không có evidence prosody/transcript rõ.
- **Không** cam kết độ chính xác STT 100% trên mọi kênh; hệ thống phải degrade graceful → Insufficient Evidence.
- **Không** đồng bộ CRM write-back tự động trong v1 (chỉ read/webhook ingest; write-back là sprint sau).

---

## 5. Personas & Actors

| Actor | Vai trò | Nhu cầu chính |
|-------|---------|----------------|
| **Agent (Sale)** | Nhân viên telesale | Xem điểm cuộc gọi, evidence, tip coaching ngắn |
| **Team Lead** | Quản lý nhóm | So sánh agents, coaching queue, trend win-rate |
| **QA Specialist** | Chấm / review | Dispute, override có audit, sample calibration |
| **QA Director** | Chủ Rulebook | Version rule, weight, auto-fail, SOP mapping |
| **Coach** | Huấn luyện | Coaching plans, drills, role-play scripts |
| **Revenue Ops** | Phân tích doanh thu | Revenue leak, funnel stage drop-off |
| **Compliance Officer** | Tuân thủ | Auto-fail rules, retention, export audit |
| **Tenant Admin** | IT tenant | Users, RBAC, SSO, storage quotas |
| **Platform SRE** | Vận hành | Health, lag, capacity, incident |
| **System (Pipeline Workers)** | Service accounts | Ingest, STT, score, publish events |
| **External CRM / Dialer** | Integration | Push call metadata + audio URI |

**RBAC roles (canonical):** `agent`, `team_lead`, `qa_specialist`, `qa_director`, `coach`, `revenue_ops`, `compliance`, `tenant_admin`, `platform_admin`, `service_pipeline`.

---

## 6. Phạm vi sản phẩm (Product Scope)

### 6.1 In-scope modules
1. **Ingest & Media** — upload / pull audio, S3 storage, checksum, virus scan hook.
2. **AI Pipeline** — fixed pipeline (xem §7).
3. **Rulebook Service** — CRUD versioned rules, publish, rollback.
4. **Scoring Engine** — rule evaluation + LLM Judge (evidence-bound).
5. **Root Cause Engine** — structured cause tree từ score misses.
6. **Coaching Engine** — plans, drills, micro-learning từ root causes.
7. **Revenue Leak AI** — ước lượng thiệt hại theo miss pattern.
8. **Dashboard & Workbench** — Next.js UI (scorecard, timeline, coaching).
9. **Dispute / Human Review** — override có audit.
10. **Sprint Workflow** — delivery cadence gắn change management rule/SOP.
11. **Observability** — logs, metrics, health, audit export.

### 6.2 Out-of-scope (v1)
- Predictive dialer, softphone.
- Full LMS / payroll.
- Multi-language ngoài vi-VN + en-US (architecture sẵn i18n keys).

---

## 7. Fixed AI Pipeline (bắt buộc)

Mọi cuộc gọi scored phải đi qua đúng chuỗi sau (có thể short-circuit sang `Insufficient Evidence` nếu stage fail):

```
Audio
  → Whisper (STT)
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

**Quy tắc pipeline**
- Mỗi stage ghi `stage_run` với `started_at`, `finished_at`, `status`, `error_code`, `input_ref`, `output_ref`.
- Stage sau chỉ chạy khi stage trước `succeeded` hoặc `degraded_ok` (policy).
- Nếu Evidence Extraction không đủ span cho rule bắt buộc → score item = `Insufficient Evidence`.
- LLM Judge **không được** invent rule; chỉ adjudicate trong khung rule đã hit/miss từ Rule Engine + evidence đã extract.
- JSON Output là contract canonical (`CallAnalysisResult`) phục vụ API + Dashboard.

---

## 8. User Stories (ưu tiên P0–P2)

### P0 — Must have
- **US-01** Là Agent, tôi muốn xem scorecard cuộc gọi của mình với quote evidence và timestamp để hiểu vì sao bị trừ điểm.
- **US-02** Là QA Director, tôi muốn publish Rulebook version mới và rollback nếu calibration xấu, mọi thay đổi có audit.
- **US-03** Là hệ thống, khi audio hỏng / STT confidence thấp, tôi trả `Insufficient Evidence` thay vì điểm giả.
- **US-04** Là Team Lead, tôi muốn dashboard nhóm: avg score, auto-fail rate, top root causes theo tuần.
- **US-05** Là Coach, tôi muốn coaching plan tự sinh từ root causes của agent trong 7 ngày gần nhất.
- **US-06** Là Compliance, tôi muốn filter mọi cuộc auto-fail và export CSV/JSON có audit trail.
- **US-07** Là service pipeline, tôi muốn idempotent ingest theo `external_call_id` + tenant.

### P1
- **US-10** Dispute workflow: Agent khiếu nại → QA Specialist review → override có reason code.
- **US-11** Calibration set: bộ cuộc gọi vàng để đo drift khi đổi model/rule.
- **US-12** Revenue Leak board theo campaign/product.
- **US-13** Webhook outbound khi `analysis.completed`.

### P2
- **US-20** Multi-product rule packs per campaign.
- **US-21** A/B Rulebook shadow scoring.
- **US-22** Agent self-serve practice drills từ objection patterns.

---

## 9. Functional Requirements

### 9.1 Call lifecycle
| State | Ý nghĩa |
|-------|---------|
| `received` | Metadata + audio URI đã lưu |
| `queued` | Chờ worker |
| `processing` | Đang chạy pipeline |
| `scored` | Có JSON output đầy đủ |
| `insufficient_evidence` | Không đủ data để kết luận một phần/toàn phần |
| `failed` | Lỗi hệ thống (retryable/non-retryable) |
| `disputed` | Đang human review |
| `finalized` | Khóa sau review / SLA |

### 9.2 Scoring
- Score theo tiêu chí từ Rulebook active version của campaign/tenant.
- Mỗi criterion: `pass` | `fail` | `not_applicable` | `insufficient_evidence`.
- Weighted aggregate + auto-fail short-circuit.
- Explainability block bắt buộc.

### 9.3 Rulebook
- Rules lưu DB: `rules`, `rule_versions`, `rulebook_releases`.
- Fields tối thiểu: `rule_code`, `category`, `severity`, `weight`, `auto_fail`, `evidence_requirements`, `evaluator_type`, `active`.
- Publish tạo immutable release snapshot.

### 9.4 Audit
Mọi thay đổi: users, roles, rules, scores (override), configs, model versions → `audit_logs`.

### 9.5 Sprint workflow
- Change requests (rule/SOP/model) đi qua Sprint: `proposed` → `in_sprint` → `reviewed` → `released` → `monitored`.
- Không hot-edit production rule ngoài quy trình (trừ `emergency_fix` có dual-approval + audit).

---

## 10. Non-Functional Requirements (NFRs)

### 10.1 Scale
| Metric | Target |
|--------|--------|
| Calls ingested | 50M+/year platform-wide; 1M+/month large tenant |
| Concurrent pipeline workers | Horizontal scale via Redis queue |
| Peak ingest | 500 calls/sec burst (buffered) |
| Rulebook size | ≥ 1000 rules/tenant pack |
| Dashboard concurrent users | 10k |

### 10.2 Latency
| Path | SLO |
|------|-----|
| API read scorecard (cached) | p95 < 200ms |
| API auth + list calls | p95 < 400ms |
| End-to-end pipeline (≤10 min audio) | p95 < 8 min; p99 < 15 min |
| Whisper stage alone | p95 < 2× realtime |
| Coaching plan generate (batch) | < 5 min / agent / day |

### 10.3 Availability
- API + Dashboard: **99.9%** monthly.
- Pipeline: **99.5%** (backlog drain sau incident < 4h cho P1).
- Multi-AZ PostgreSQL + Redis; S3 durable 11 nines (provider).

### 10.4 Security
- JWT access + refresh; RBAC enforcement ở API gateway/service.
- Encryption in transit (TLS 1.2+); at rest (DB + S3 SSE).
- Tenant isolation: `tenant_id` trên mọi row; row-level checks trong repository.
- PII minimization + redact patterns (CC, CVV, SSN-like).
- Secrets chỉ env / secret manager — cấm commit.

### 10.5 Observability
- Structured JSON logs: `timestamp`, `level`, `service`, `trace_id`, `actor_id`, `action`, `tenant_id`, `call_id`.
- Metrics: ingest_rate, stage_latency, stage_error_rate, queue_lag, score_throughput, ie_rate (`insufficient_evidence` ratio), storage_fail.
- Health: `GET /health`, `GET /ready`.
- Tracing: OpenTelemetry across stages.

### 10.6 Data retention
- Audio: tenant policy (default 180 days hot, 2 years cold).
- Transcripts + scores: default 3 years.
- Audit logs: ≥ 5 years (compliance configurable).

---

## 11. Success Metrics & Calibration

| Metric | Định nghĩa | Target năm 1 |
|--------|------------|--------------|
| QA coverage | % calls scored hoặc IE | ≥ 99% |
| Human agreement | Agreement trên calibration set | ≥ 85% criterion-level |
| IE precision | IE đúng là thiếu data (không phải model lazy) | Spot-check ≥ 90% |
| Time-to-coaching | Call finalized → plan visible | < 24h |
| Dispute rate | Disputes / scored calls | < 3% sau ổn định |
| Leak attribution | Correlation leak score vs CRM lost-revenue tags | Review quarterly |

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| STT lỗi tiếng Việt / overlap | Sai điểm | Confidence gates → IE; human review queue |
| LLM hallucination | Điểm giả | Evidence-bound prompts; rule-only adjudication; reject free-form criteria |
| Rule drift | Điểm không công bằng | Versioned releases + calibration regression gates |
| Queue backlog | SLA vỡ | Autoscale workers; priority lanes (compliance first) |
| Audio provider outage | Ingest fail | Retry + DLQ; status `failed` có alert |
| Multi-tenant leak | Security incident | Tenant_id mandatory; integration tests; audit |

---

## 13. Failure Modes (sản phẩm)

1. **Audio missing / corrupt** → `insufficient_evidence` (global) + stage error `AUDIO_INVALID`.
2. **Diarization low confidence** → speaker=`unknown` spans; rules cần speaker rõ → IE.
3. **Rulebook not published** → pipeline halt `RULEBOOK_MISSING` (không fallback hardcode).
4. **LLM timeout** → retry; nếu fail: rule-engine-only scores + flag `llm_judge_skipped`; criteria cần judge → IE.
5. **Partial stage success** → JSON output vẫn emit với `degraded: true` và per-criterion IE.
6. **Override conflict** → last finalized override wins; full audit chain retained.

---

## 14. Insufficient Evidence — Product Contract

Chuỗi hiển thị và API enum/value chuẩn:

```text
Insufficient Evidence
```

Áp dụng khi:
- Không có span thỏa `evidence_requirements` của rule.
- STT/diarization confidence < threshold cấu hình (DB, không hardcode magic trong UI).
- Metadata bắt buộc thiếu (ví dụ rule cần `product_sku` mà call không có).
- Audio segment tương ứng bị redact / silence.

**Cấm:** map IE thành `pass`, `fail`, hoặc điểm số trung bình giả.

---

## 15. Audit Requirements (sản phẩm)

Mọi sự kiện sau **bắt buộc** audit:
- Rule create/update/publish/rollback/weight change.
- Score override / dispute resolve.
- Role/permission change.
- Model/prompt template version change (template không chứa business rule).
- Retention/policy change.
- Emergency rule fix.

Audit record tối thiểu: `actor_id`, `action`, `entity_type`, `entity_id`, `before`, `after`, `reason`, `request_id`, `created_at`, `tenant_id`.

---

## 16. Delivery — Sprint Workflow

- Sprint 2 tuần; mỗi sprint có goal gắn epic (Ingest, Scoring, Rulebook, Coaching, …).
- Definition of Done theo constitution checklist (architecture, no hardcoded rules, evidence, IE, audit, OpenAPI, health, tests).
- Feature flags cho shadow scoring trước khi cut-over Rulebook release.

---

## 17. Dependencies

- Whisper-compatible STT workers (GPU pool).
- Diarization model service.
- PostgreSQL 16+, Redis 7+, S3-compatible (MinIO/AWS).
- FastAPI backend services + Next.js frontend.
- Optional: CRM webhooks (Salesforce/HubSpot/custom dialer).

---

## 18. Acceptance Criteria (PRD-level)

1. Pipeline cố định §7 xuất hiện trong architecture, API job status, và JSON output `pipeline_stages[]`.
2. Rulebook ≥ 1000 rules schema-ready; seed có thể nhập theo pack.
3. Scorecard API trả evidence + timestamps hoặc `Insufficient Evidence`.
4. Dashboard không chứa business rule logic — chỉ render API results.
5. `/health` + `/ready` + structured logs + audit trên mutations.
6. Docker Compose đưa được full stack local (API, workers, DB, Redis, MinIO, web).

---

## 19. Glossary

| Term | Meaning |
|------|---------|
| Rulebook | Tập rule versioned trong DB dùng để chấm |
| Evidence span | Đoạn transcript/audio chứng minh kết luận |
| Auto-fail | Rule làm fail toàn scorecard khi fail |
| IE | Insufficient Evidence |
| Revenue leak | Ước lượng mất mát do miss behavior có evidence |
| Release | Snapshot immutable của rulebook đã publish |

---

## 20. Document Control

| Field | Value |
|-------|-------|
| Owner | QA Director + Chief Architect |
| Reviewers | Backend Lead, ML Lead, Frontend Lead, DevOps, Compliance |
| Next docs | `02_System_Architecture.md` … `08_Coaching_Engine.md` |
| Change process | Sprint CR + audit |

**Hết PRD v1.0.0**
