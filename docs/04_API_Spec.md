# 04 — API Specification
## AI QA TELESALE ENTERPRISE — FastAPI / OpenAPI

**Phiên bản:** 1.0.0  
**Base URL:** `https://{host}/v1`  
**Auth:** Bearer JWT · RBAC  
**Contract:** OpenAPI 3.1 sinh từ FastAPI; tài liệu này là normative design

---

## 1. Mục tiêu & Non-goals

### Goals
- API ổn định cho Web (Next.js), dialer ingest, workers nội bộ, webhook consumers.
- Mọi response liên quan chấm điểm mang evidence/timestamps hoặc `Insufficient Evidence`.
- Mutation có audit; đọc có phân quyền theo tenant + role.
- Idempotent ingest; pagination cursor cho list lớn (triệu calls).

### Non-goals
- GraphQL (v1).
- Public anonymous scoring.
- Nhúng business rule logic vào client SDK.

---

## 2. Conventions

| Item | Rule |
|------|------|
| Protocol | HTTPS only |
| Format | `application/json; charset=utf-8` |
| Time | ISO-8601 UTC |
| IDs | UUID string |
| Errors | RFC 7807-inspired envelope |
| Pagination | cursor (`next_cursor`) + `limit` (1–100, default 20) |
| Idempotency | Header `Idempotency-Key` on POST creates |
| Trace | `X-Request-Id` echoed; `traceparent` supported |
| Versioning | URL `/v1`; breaking → `/v2` |

### 2.1 Standard error
```json
{
  "type": "https://aqate.example/errors/validation",
  "title": "Validation Error",
  "status": 422,
  "detail": "duration_sec must be > 0",
  "instance": "/v1/calls",
  "request_id": "req_01H...",
  "errors": [{"field": "duration_sec", "code": "gt", "message": "must be > 0"}]
}
```

### 2.2 Auth headers
```http
Authorization: Bearer <access_token>
X-Tenant-Id: <uuid>   # optional if claim present; must match token
```

### 2.3 Verdict enum (canonical)
`pass` | `fail` | `not_applicable` | `Insufficient Evidence`

---

## 3. Security Schemes (OpenAPI)

```yaml
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
security:
  - bearerAuth: []
```

Permission codes: `calls:write`, `calls:read`, `scorecard:read`, `scorecard:override`, `rulebook:read`, `rulebook:write`, `rulebook:publish`, `coaching:read`, `coaching:manage`, `revenue:read`, `audit:read`, `admin:users`, `pipeline:internal`.

---

## 4. Health (no auth)

### `GET /health`
```json
{"status": "ok", "service": "aqate-api", "time": "2026-09-05T10:00:00Z"}
```

### `GET /ready`
```json
{
  "status": "ready",
  "checks": {
    "postgres": {"ok": true, "latency_ms": 3},
    "redis": {"ok": true, "latency_ms": 1},
    "s3": {"ok": true, "latency_ms": 12}
  }
}
```
503 nếu dependency fail.

---

## 5. Auth APIs

### `POST /v1/auth/login`
Request:
```json
{
  "tenant_code": "acme",
  "email": "lead@acme.vn",
  "password": "********"
}
```
Response 200:
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "11111111-1111-7111-8111-111111111111",
    "email": "lead@acme.vn",
    "full_name": "Nguyen Van A",
    "roles": ["team_lead"],
    "permissions": ["calls:read", "scorecard:read", "coaching:read"]
  }
}
```

### `POST /v1/auth/refresh`
### `POST /v1/auth/logout`
Audit: `auth.login`, `auth.logout`.

---

## 6. Calls — Ingest & Query

### `POST /v1/calls`
**Permission:** `calls:write`  
**Idempotency-Key:** required for dialer.

Request:
```json
{
  "external_call_id": "dialer-998877",
  "campaign_code": "SOAP-99K",
  "agent_email": "sale01@acme.vn",
  "direction": "outbound",
  "started_at": "2026-09-05T09:58:00Z",
  "ended_at": "2026-09-05T10:04:12Z",
  "duration_sec": 372.0,
  "customer_phone": "+84901234567",
  "crm_outcome": "callback",
  "crm_order_value": null,
  "currency": "VND",
  "audio": {
    "source": "upload",
    "content_type": "audio/mpeg",
    "sha256": "a".repeat ? "ab...64hex" : "ab12..."
  },
  "metadata": {"queue": "HN-1", "sku": "SOAP-99K"}
}
```
*(sha256: 64 hex chars)*

Flow: API returns `upload_url` (presigned) khi `source=upload`, hoặc accept `audio.s3_uri` khi `source=s3`.

Response 201:
```json
{
  "id": "cl_uuid",
  "status": "received",
  "upload": {
    "method": "PUT",
    "url": "https://minio/.../presigned",
    "headers": {"Content-Type": "audio/mpeg"},
    "expires_at": "2026-09-05T10:15:00Z"
  },
  "created_at": "2026-09-05T10:05:00Z"
}
```

### `POST /v1/calls/{call_id}/audio:complete`
Xác nhận upload xong → status `queued` → enqueue pipeline.

### `GET /v1/calls`
Query: `agent_id`, `team_id`, `campaign_id`, `status`, `from`, `to`, `cursor`, `limit`.

Response:
```json
{
  "data": [
    {
      "id": "cl_...",
      "external_call_id": "dialer-998877",
      "agent_user_id": "us_...",
      "status": "scored",
      "started_at": "2026-09-05T09:58:00Z",
      "duration_sec": 372.0,
      "crm_outcome": "callback",
      "overall_score": 72.5,
      "result": "fail"
    }
  ],
  "next_cursor": "eyJ...",
  "limit": 20
}
```

### `GET /v1/calls/{call_id}`
Chi tiết metadata + media refs + latest pipeline status.

### `GET /v1/calls/{call_id}/pipeline`
```json
{
  "pipeline_run_id": "pr_...",
  "status": "succeeded",
  "rulebook_release_id": "rr_...",
  "stages": [
    {
      "stage": "whisper",
      "status": "succeeded",
      "started_at": "2026-09-05T10:05:10Z",
      "finished_at": "2026-09-05T10:06:40Z",
      "error_code": null,
      "metrics": {"realtime_factor": 0.4}
    },
    {
      "stage": "speaker_diarization",
      "status": "succeeded",
      "started_at": "2026-09-05T10:06:41Z",
      "finished_at": "2026-09-05T10:07:10Z"
    },
    {
      "stage": "transcript_normalization",
      "status": "succeeded",
      "started_at": "2026-09-05T10:07:11Z",
      "finished_at": "2026-09-05T10:07:20Z"
    },
    {
      "stage": "semantic_segmentation",
      "status": "succeeded",
      "started_at": "2026-09-05T10:07:20Z",
      "finished_at": "2026-09-05T10:07:35Z"
    },
    {
      "stage": "evidence_extraction",
      "status": "succeeded",
      "started_at": "2026-09-05T10:07:35Z",
      "finished_at": "2026-09-05T10:08:00Z"
    },
    {
      "stage": "rule_engine",
      "status": "succeeded",
      "started_at": "2026-09-05T10:08:00Z",
      "finished_at": "2026-09-05T10:08:20Z"
    },
    {
      "stage": "llm_judge",
      "status": "succeeded",
      "started_at": "2026-09-05T10:08:20Z",
      "finished_at": "2026-09-05T10:08:50Z"
    },
    {
      "stage": "root_cause_engine",
      "status": "succeeded",
      "started_at": "2026-09-05T10:08:50Z",
      "finished_at": "2026-09-05T10:09:00Z"
    },
    {
      "stage": "coaching_engine",
      "status": "succeeded",
      "started_at": "2026-09-05T10:09:00Z",
      "finished_at": "2026-09-05T10:09:10Z"
    },
    {
      "stage": "revenue_leak_ai",
      "status": "degraded_ok",
      "started_at": "2026-09-05T10:09:10Z",
      "finished_at": "2026-09-05T10:09:12Z",
      "error_code": null,
      "metrics": {"note": "crm_order_value missing; unestimated components"}
    },
    {
      "stage": "json_output",
      "status": "succeeded",
      "started_at": "2026-09-05T10:09:12Z",
      "finished_at": "2026-09-05T10:09:13Z"
    }
  ]
}
```

Stage names **must** align with fixed pipeline (Dashboard maps `json_output` → UI).

---

## 7. Analysis & Scorecard

### `GET /v1/calls/{call_id}/analysis`
**Permission:** `scorecard:read`  
Returns canonical JSON Output (see Architecture §17) plus coaching/root_cause/leak blocks.

```json
{
  "schema_version": "1.0.0",
  "tenant_id": "tn_...",
  "call_id": "cl_...",
  "status": "scored",
  "rulebook_release_id": "rr_...",
  "pipeline_stages": [],
  "transcript_preview": {
    "language": "vi",
    "turn_count": 42,
    "avg_confidence": 0.91
  },
  "scorecard": {
    "id": "sc_...",
    "overall_score": 72.5,
    "result": "fail",
    "auto_fail_triggered": true,
    "explanation": "Auto-fail R-COMP-PRICE-002; 3 major misses.",
    "generated_at": "2026-09-05T10:09:13Z",
    "items": [
      {
        "rule_code": "R-OPEN-001",
        "title": "Chào khách hàng",
        "verdict": "pass",
        "score": 1.0,
        "weight": 1.35,
        "confidence": 0.93,
        "evaluated_at": "2026-09-05T10:08:21Z",
        "explanation": "Greeting span matched evidence requirements.",
        "evidence_spans": [
          {
            "id": "ev_...",
            "quote": "Hello chị ạ",
            "audio_ts_start": 0.8,
            "audio_ts_end": 1.6,
            "turn_index": 0,
            "confidence": 0.93
          }
        ],
        "scoring_path": ["rule_engine.regex", "llm_judge.confirm"]
      },
      {
        "rule_code": "R-ADDR-001",
        "title": "Chốt địa chỉ giao hàng",
        "verdict": "Insufficient Evidence",
        "score": null,
        "weight": 1.4,
        "confidence": 0.0,
        "evaluated_at": "2026-09-05T10:08:22Z",
        "explanation": "No address slot span with confidence >= configured min.",
        "evidence_spans": [],
        "scoring_path": ["evidence_extraction.none", "rule_engine.ie"]
      }
    ]
  },
  "root_causes": {
    "verdict": "identified",
    "primary_cause_code": "RC-CLOSE-NO-ASK",
    "causes": [],
    "generated_at": "2026-09-05T10:09:00Z"
  },
  "coaching": {
    "plan_id": null,
    "call_tips": [
      {
        "cause_code": "RC-CLOSE-NO-ASK",
        "title": "Nhắc chốt đơn rõ ràng",
        "action_markdown": "Sau khi xác nhận giá, hỏi: 'Em chốt 3 hộp giao hôm nay cho chị nhé?'",
        "template_code": "CT-CLOSE-ASK-01"
      }
    ]
  },
  "revenue_leak": {
    "verdict": "Insufficient Evidence",
    "estimated_amount": null,
    "currency": "VND",
    "explanation": "Missing crm_order_value and campaign ASP; cannot estimate.",
    "generated_at": "2026-09-05T10:09:12Z"
  },
  "generated_at": "2026-09-05T10:09:13Z"
}
```

### `GET /v1/calls/{call_id}/transcript`
Paginated turns with speaker + timestamps.

### `GET /v1/calls/{call_id}/audio-url`
Presigned GET; permission `calls:read`; audit `media.access`.

---

## 8. Disputes & Overrides

### `POST /v1/calls/{call_id}/disputes`
```json
{
  "score_item_id": "si_...",
  "reason_code": "evidence_misheard",
  "reason_text": "Khách đã nói địa chỉ ở phút 3:20"
}
```

### `POST /v1/disputes/{id}/resolve`
**Permission:** `scorecard:override`
```json
{
  "resolution": "overturned",
  "new_verdict": "pass",
  "note": "Verified after replay",
  "evidence_span_ids": ["ev_new_..."]
}
```
Rules:
- Override **must** include note + actor.
- Audit `score.override` with before/after.
- Không cho `pass` nếu caller không cung cấp evidence (trừ `not_applicable`).

---

## 9. Rulebook APIs

### `GET /v1/rulebook/rules`
Filters: `category`, `status`, `q`, cursor.

### `POST /v1/rulebook/rules`
**Permission:** `rulebook:write`
```json
{
  "rule_code": "R-OPEN-099",
  "category": "opening",
  "title": "Xưng tên agent",
  "description": "Agent phải xưng tên trong opening",
  "severity": "major",
  "weight": 1.2,
  "auto_fail": false,
  "evaluator_type": "span_classifier",
  "evidence_requirements": {
    "min_spans": 1,
    "speakers_allowed": ["agent"],
    "stage_keys": ["opening"],
    "slots": ["agent_name"],
    "min_confidence": 0.7
  },
  "evaluator_config": {
    "slot": "agent_name",
    "positive_labels": ["has_agent_name"]
  }
}
```
Tạo `rules` + `rule_versions` v1; audit.

### `POST /v1/rulebook/rules/{rule_code}/versions`
Tạo version mới (không tự publish).

### `POST /v1/rulebook/releases`
```json
{
  "campaign_id": "cp_...",
  "version_label": "2026.09.05-r3",
  "rule_selectors": {"status": "active", "include_codes": null}
}
```
Builds immutable snapshot.

### `POST /v1/rulebook/releases/{id}/publish`
**Permission:** `rulebook:publish`  
Sets campaign active pointer; invalidates Redis cache; audit; optional shadow flag.

### `POST /v1/rulebook/releases/{id}/rollback`
Point campaign về release trước; audit `rulebook.rollback`.

### `GET /v1/rulebook/releases/{id}`
Includes item count + hash + published_at.

**Invariant:** API **không** nhận “inline rules” trong scoring request để bypass DB.

---

## 10. Coaching APIs

### `GET /v1/coaching/plans/me`
Agent xem plan hiện tại.

### `GET /v1/coaching/plans?agent_id=&period_start=`
Lead/coach.

### `POST /v1/coaching/plans/generate`
**Permission:** `coaching:manage`
```json
{
  "agent_user_id": "us_...",
  "period_start": "2026-09-01",
  "period_end": "2026-09-07"
}
```
Enqueue insight worker; returns `202` + `job_id`.

### `PATCH /v1/coaching/plan-items/{id}`
Update status `open|done|dismissed` — audit.

---

## 11. Revenue Leak APIs

### `GET /v1/revenue-leak/summary`
Query: `from`, `to`, `campaign_id`, `team_id`.
```json
{
  "from": "2026-09-01",
  "to": "2026-09-05",
  "currency": "VND",
  "estimated_total": 125000000.0,
  "insufficient_evidence_calls": 340,
  "top_components": [
    {"cause_code": "RC-OBJ-PRICE-01", "amount": 52000000.0, "call_count": 128}
  ]
}
```

### `GET /v1/calls/{call_id}/revenue-leak`

---

## 12. Dashboard Aggregates

### `GET /v1/dashboard/overview`
```json
{
  "window": {"from": "2026-09-01T00:00:00Z", "to": "2026-09-05T23:59:59Z"},
  "calls_total": 120450,
  "scored": 118900,
  "insufficient_evidence": 1200,
  "failed": 350,
  "avg_score": 81.2,
  "auto_fail_rate": 0.074,
  "top_failed_rules": [{"rule_code": "R-ADDR-001", "fail_count": 4200}],
  "top_root_causes": [{"cause_code": "RC-CLOSE-NO-ASK", "count": 3100}]
}
```
Cached Redis 60s; permission `calls:read` scoped by team unless admin.

---

## 13. Admin & Audit

### `GET /v1/audit-logs`
**Permission:** `audit:read`  
Filters: `action`, `entity_type`, `actor_id`, `from`, `to`, cursor.

### `POST /v1/admin/users` / `PATCH /v1/admin/users/{id}` / `PUT /v1/admin/users/{id}/roles`
Audit tất cả.

### `GET /v1/admin/ai-config` / `PUT /v1/admin/ai-config`
Update thresholds trong `tenant_ai_configs` — **không** hardcode; audit `config.ai_update`.

### `GET /v1/sprints/changes` / `POST /v1/sprints/changes` / `PATCH /v1/sprints/changes/{id}`
Sprint workflow cho rule/SOP/model changes.

---

## 14. Internal Pipeline Hooks

### `POST /v1/internal/pipeline/callbacks`
**Permission:** `pipeline:internal`  
Worker báo stage completion (nếu không ghi DB trực tiếp).

```json
{
  "pipeline_run_id": "pr_...",
  "stage": "whisper",
  "status": "succeeded",
  "started_at": "...",
  "finished_at": "...",
  "output_uri": "s3://...",
  "metrics": {}
}
```

---

## 15. Webhooks (Outbound)

Tenant config endpoint; signed with HMAC SHA-256 header `X-AQATE-Signature`.

Event `analysis.completed`:
```json
{
  "event": "analysis.completed",
  "tenant_id": "tn_...",
  "call_id": "cl_...",
  "status": "scored",
  "overall_score": 72.5,
  "result": "fail",
  "occurred_at": "2026-09-05T10:09:13Z"
}
```

---

## 16. Rate Limits

| Route class | Limit |
|-------------|-------|
| Auth login | 10/min/IP |
| Ingest POST /calls | 100/sec/tenant (burst 500) |
| Read APIs | 200/sec/token |
| Rulebook publish | 30/hour/tenant |

429 + `Retry-After`.

---

## 17. OpenAPI Fragment (score item schema)

```yaml
ScoreItem:
  type: object
  required: [rule_code, verdict, weight, confidence, explanation, evaluated_at, evidence_spans]
  properties:
    rule_code: {type: string}
    verdict:
      type: string
      enum: [pass, fail, not_applicable, Insufficient Evidence]
    score:
      type: number
      nullable: true
    weight: {type: number}
    confidence: {type: number}
    explanation: {type: string}
    evaluated_at: {type: string, format: date-time}
    evidence_spans:
      type: array
      items: {$ref: '#/components/schemas/EvidenceSpan'}
```

---

## 18. Failure Modes (API)

| Condition | HTTP | Code |
|-----------|------|------|
| Invalid JWT | 401 | `auth.unauthorized` |
| Missing permission | 403 | `auth.forbidden` |
| Wrong tenant | 403 | `tenant.mismatch` |
| Call not found | 404 | `call.not_found` |
| Analysis not ready | 409 | `analysis.not_ready` |
| Rulebook missing on enqueue | 422 | `rulebook.missing` |
| Duplicate external_call_id without idem key | 409 | `call.duplicate` |
| Dependency down on write | 503 | `dependency.unavailable` |

Khi analysis degraded: **200** với `status=insufficient_evidence` và items IE — không dùng 5xx.

---

## 19. Audit Requirements

| API | Audit action |
|-----|--------------|
| login/logout | `auth.*` |
| create call | `call.create` |
| audio access | `media.access` |
| dispute/resolve | `dispute.*` / `score.override` |
| rule CRUD/publish/rollback | `rulebook.*` |
| ai-config | `config.ai_update` |
| role changes | `rbac.assign` |

---

## 20. Frontend Consumption Rules

- Next.js chỉ gọi `/v1/*` typed client.
- Không compute pass/fail ở UI.
- Hiển thị đúng chuỗi `Insufficient Evidence`.
- Timeline seek dùng `audio_ts_start/end`.

---

## 21. Document Control

| Field | Value |
|-------|-------|
| Owner | Backend Lead |
| Sync | FastAPI routers phải match doc mỗi sprint |
| Tests | Contract tests từ OpenAPI |

**Hết API Spec v1.0.0**
