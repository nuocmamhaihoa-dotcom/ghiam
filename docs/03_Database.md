# 03 — Database Design
## AI QA TELESALE ENTERPRISE — PostgreSQL

**Phiên bản:** 1.0.0  
**Engine:** PostgreSQL 16+  
**Patterns:** Relational flat schema · tenant_id everywhere · versioned Rulebook · append-only audit · evidence with timestamps

---

## 1. Mục tiêu & Non-goals

### Goals
- System of record cho calls, transcripts, rulebook, scores, root causes, coaching, revenue leak, audit.
- Scale hàng chục triệu `calls` rows / tenant lớn qua partitioning + indexes.
- Rulebook versioned; scoring luôn gắn `rulebook_release_id`.
- Mọi kết luận lưu evidence + timestamps; thiếu data → verdict `Insufficient Evidence`.

### Non-goals
- Không lưu raw GPU tensors.
- Không hardcode rule content trong migration ngoài seed có kiểm soát.
- Không xóa audit vật lý (chỉ freeze/archive).

---

## 2. Conventions

| Convention | Rule |
|------------|------|
| PK | `id` ULID/UUIDv7 text `VARCHAR(26)` or `UUID` — chọn **UUIDv7** `UUID` |
| Tenant | `tenant_id UUID NOT NULL` trên mọi bảng nghiệp vụ |
| Timestamps | `created_at`, `updated_at` timestamptz; analysis dùng `evaluated_at` |
| Soft delete | `deleted_at` nullable cho users/rules drafts; releases immutable |
| JSON | `JSONB` cho snapshot/artifacts summary |
| Enums | PostgreSQL ENUM hoặc text + CHECK — dùng **text + CHECK** để migrate dễ |
| Money | `NUMERIC(14,2)` + `currency CHAR(3)` |
| Naming | `snake_case` tables plural |

---

## 3. ER Overview (logical)

```
tenants ─┬─ users ─ user_roles ─ roles ─ role_permissions ─ permissions
         ├─ teams ─ team_members
         ├─ campaigns
         ├─ calls ─┬─ call_media
         │         ├─ pipeline_runs ─ pipeline_stage_runs
         │         ├─ transcripts ─ transcript_turns
         │         ├─ evidence_spans
         │         ├─ scorecards ─ score_items
         │         ├─ root_cause_results
         │         ├─ coaching_plans
         │         └─ revenue_leak_estimates
         ├─ rules ─ rule_versions
         ├─ rulebook_releases ─ rulebook_release_items
         ├─ cause_taxonomy
         ├─ coaching_templates
         ├─ revenue_impact_models
         ├─ disputes ─ dispute_events
         ├─ sprint_changes
         └─ audit_logs
```

---

## 4. Core Tables

### 4.1 `tenants`
```sql
CREATE TABLE tenants (
  id              UUID PRIMARY KEY,
  code            TEXT NOT NULL UNIQUE,
  name            TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('active','suspended','churned')),
  timezone        TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
  retention_audio_days INT NOT NULL DEFAULT 180,
  retention_score_days INT NOT NULL DEFAULT 1095,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.2 `users`
```sql
CREATE TABLE users (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  email           CITEXT NOT NULL,
  full_name       TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('active','invited','disabled')),
  password_hash   TEXT, -- null if SSO-only
  last_login_at   TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,
  UNIQUE (tenant_id, email)
);
CREATE INDEX idx_users_tenant ON users(tenant_id);
```

### 4.3 `roles` / `permissions` / `user_roles` / `role_permissions`
```sql
CREATE TABLE permissions (
  id          UUID PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE, -- e.g. rulebook:publish
  description TEXT NOT NULL
);

CREATE TABLE roles (
  id          UUID PRIMARY KEY,
  tenant_id   UUID REFERENCES tenants(id), -- null = system role template
  code        TEXT NOT NULL, -- agent, qa_director, ...
  name        TEXT NOT NULL,
  UNIQUE (tenant_id, code)
);

CREATE TABLE role_permissions (
  role_id         UUID NOT NULL REFERENCES roles(id),
  permission_id   UUID NOT NULL REFERENCES permissions(id),
  PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE user_roles (
  user_id     UUID NOT NULL REFERENCES users(id),
  role_id     UUID NOT NULL REFERENCES roles(id),
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  assigned_by UUID REFERENCES users(id),
  PRIMARY KEY (user_id, role_id)
);
```

### 4.4 `teams` / `team_members`
```sql
CREATE TABLE teams (
  id          UUID PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenants(id),
  name        TEXT NOT NULL,
  lead_user_id UUID REFERENCES users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE TABLE team_members (
  team_id     UUID NOT NULL REFERENCES teams(id),
  user_id     UUID NOT NULL REFERENCES users(id),
  joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, user_id)
);
```

### 4.5 `campaigns`
```sql
CREATE TABLE campaigns (
  id                    UUID PRIMARY KEY,
  tenant_id             UUID NOT NULL REFERENCES tenants(id),
  code                  TEXT NOT NULL,
  name                  TEXT NOT NULL,
  product_line          TEXT,
  active_rulebook_release_id UUID, -- FK added after rulebook_releases
  status                TEXT NOT NULL CHECK (status IN ('draft','active','paused','archived')),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code)
);
```

---

## 5. Call & Media

### 5.1 `calls`
```sql
CREATE TABLE calls (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL REFERENCES tenants(id),
  external_call_id  TEXT NOT NULL,
  campaign_id       UUID REFERENCES campaigns(id),
  team_id           UUID REFERENCES teams(id),
  agent_user_id     UUID REFERENCES users(id),
  direction         TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  started_at        TIMESTAMPTZ,
  ended_at          TIMESTAMPTZ,
  duration_sec      NUMERIC(10,2),
  customer_phone_hash TEXT, -- store hash, not raw if policy requires
  crm_outcome       TEXT, -- won|lost|callback|unknown
  crm_order_value   NUMERIC(14,2),
  currency          CHAR(3),
  status            TEXT NOT NULL CHECK (status IN (
                      'received','queued','processing','scored',
                      'insufficient_evidence','failed','disputed','finalized'
                    )),
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, external_call_id)
) PARTITION BY RANGE (created_at);

-- monthly partitions recommended for scale
CREATE INDEX idx_calls_tenant_agent_started ON calls(tenant_id, agent_user_id, started_at DESC);
CREATE INDEX idx_calls_tenant_status ON calls(tenant_id, status);
CREATE INDEX idx_calls_campaign ON calls(tenant_id, campaign_id, started_at DESC);
```

### 5.2 `call_media`
```sql
CREATE TABLE call_media (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  call_id         UUID NOT NULL, -- logical FK to calls.id
  media_type      TEXT NOT NULL CHECK (media_type IN ('original_audio','normalized_audio')),
  s3_uri          TEXT NOT NULL,
  sha256          CHAR(64) NOT NULL,
  byte_size       BIGINT NOT NULL,
  duration_sec    NUMERIC(10,2),
  sample_rate_hz  INT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_call_media_call ON call_media(tenant_id, call_id);
```

---

## 6. Pipeline Tables

### 6.1 `pipeline_runs`
```sql
CREATE TABLE pipeline_runs (
  id                    UUID PRIMARY KEY,
  tenant_id             UUID NOT NULL,
  call_id               UUID NOT NULL,
  rulebook_release_id   UUID,
  status                TEXT NOT NULL CHECK (status IN (
                          'running','succeeded','degraded','failed','cancelled'
                        )),
  attempt               INT NOT NULL DEFAULT 1,
  schema_version        TEXT NOT NULL DEFAULT '1.0.0',
  started_at            TIMESTAMPTZ NOT NULL,
  finished_at           TIMESTAMPTZ,
  error_code            TEXT,
  trace_id              TEXT NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pipeline_runs_call ON pipeline_runs(tenant_id, call_id, created_at DESC);
```

### 6.2 `pipeline_stage_runs`
```sql
CREATE TABLE pipeline_stage_runs (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs(id),
  stage           TEXT NOT NULL CHECK (stage IN (
                    'audio_ingest','whisper','speaker_diarization',
                    'transcript_normalization','semantic_segmentation',
                    'evidence_extraction','rule_engine','llm_judge',
                    'root_cause_engine','coaching_engine','revenue_leak_ai',
                    'json_output'
                  )),
  status          TEXT NOT NULL CHECK (status IN (
                    'pending','running','succeeded','degraded_ok','failed','skipped'
                  )),
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  error_code      TEXT,
  metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_uri      TEXT,
  UNIQUE (pipeline_run_id, stage, attempt_no),
  attempt_no      INT NOT NULL DEFAULT 1
);
```

---

## 7. Transcript & Evidence

### 7.1 `transcripts`
```sql
CREATE TABLE transcripts (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL,
  call_id           UUID NOT NULL,
  pipeline_run_id   UUID NOT NULL,
  language          TEXT NOT NULL DEFAULT 'vi',
  stt_model         TEXT NOT NULL,
  diarization_model TEXT,
  avg_confidence    NUMERIC(5,4),
  s3_uri            TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_transcripts_call ON transcripts(tenant_id, call_id);
```

### 7.2 `transcript_turns`
```sql
CREATE TABLE transcript_turns (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  transcript_id   UUID NOT NULL REFERENCES transcripts(id),
  turn_index      INT NOT NULL,
  speaker         TEXT NOT NULL CHECK (speaker IN ('agent','customer','unknown')),
  text            TEXT NOT NULL,
  audio_ts_start  NUMERIC(12,3) NOT NULL,
  audio_ts_end    NUMERIC(12,3) NOT NULL,
  confidence      NUMERIC(5,4),
  stage_key       TEXT CHECK (stage_key IN (
                    'opening','discovery','pitch','objection','close','outro','other'
                  )),
  UNIQUE (transcript_id, turn_index)
);
CREATE INDEX idx_turns_time ON transcript_turns(transcript_id, audio_ts_start);
```

### 7.3 `evidence_spans`
```sql
CREATE TABLE evidence_spans (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL,
  call_id           UUID NOT NULL,
  transcript_id     UUID NOT NULL,
  turn_index        INT,
  quote             TEXT NOT NULL,
  audio_ts_start    NUMERIC(12,3) NOT NULL,
  audio_ts_end      NUMERIC(12,3) NOT NULL,
  confidence        NUMERIC(5,4) NOT NULL,
  extractor_id      TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_evidence_call ON evidence_spans(tenant_id, call_id);
```

---

## 8. Rulebook (source of business truth)

### 8.1 `rules`
```sql
CREATE TABLE rules (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  rule_code       TEXT NOT NULL, -- R-OPEN-001
  category        TEXT NOT NULL,
  title           TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('draft','active','deprecated')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, rule_code)
);
```

### 8.2 `rule_versions`
```sql
CREATE TABLE rule_versions (
  id                    UUID PRIMARY KEY,
  tenant_id             UUID NOT NULL,
  rule_id               UUID NOT NULL REFERENCES rules(id),
  version               INT NOT NULL,
  description           TEXT NOT NULL,
  severity              TEXT NOT NULL CHECK (severity IN ('info','minor','major','critical')),
  weight                NUMERIC(8,4) NOT NULL,
  auto_fail             BOOLEAN NOT NULL DEFAULT FALSE,
  evaluator_type        TEXT NOT NULL CHECK (evaluator_type IN (
                          'regex','span_classifier','slot_filler','llm_judge','composite'
                        )),
  evidence_requirements JSONB NOT NULL,
  evaluator_config      JSONB NOT NULL, -- thresholds, slots — NOT in app code
  revenue_impact_code   TEXT,
  change_note           TEXT,
  created_by            UUID REFERENCES users(id),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (rule_id, version)
);
```

**Example `evidence_requirements`:**
```json
{
  "min_spans": 1,
  "speakers_allowed": ["agent"],
  "stage_keys": ["opening"],
  "slots": ["greeting_phrase"],
  "min_confidence": 0.7
}
```

### 8.3 `rulebook_releases` / `rulebook_release_items`
```sql
CREATE TABLE rulebook_releases (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  campaign_id     UUID REFERENCES campaigns(id), -- null = tenant default
  version_label   TEXT NOT NULL, -- 2026.09.05-r3
  status          TEXT NOT NULL CHECK (status IN ('pending','published','retired')),
  snapshot_hash   CHAR(64) NOT NULL,
  published_by    UUID REFERENCES users(id),
  published_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, campaign_id, version_label)
);

CREATE TABLE rulebook_release_items (
  release_id        UUID NOT NULL REFERENCES rulebook_releases(id),
  rule_version_id   UUID NOT NULL REFERENCES rule_versions(id),
  rule_code         TEXT NOT NULL,
  PRIMARY KEY (release_id, rule_version_id)
);
```

`campaigns.active_rulebook_release_id` FK → `rulebook_releases(id)`.

---

## 9. Scoring

### 9.1 `scorecards`
```sql
CREATE TABLE scorecards (
  id                    UUID PRIMARY KEY,
  tenant_id             UUID NOT NULL,
  call_id               UUID NOT NULL,
  pipeline_run_id       UUID NOT NULL,
  rulebook_release_id   UUID NOT NULL,
  overall_score         NUMERIC(8,4), -- null if fully IE
  result                TEXT NOT NULL CHECK (result IN (
                          'pass','fail','Insufficient Evidence','needs_review'
                        )),
  auto_fail_triggered   BOOLEAN NOT NULL DEFAULT FALSE,
  explanation           TEXT NOT NULL,
  generated_at          TIMESTAMPTZ NOT NULL,
  finalized_at          TIMESTAMPTZ,
  UNIQUE (call_id, pipeline_run_id)
);
CREATE INDEX idx_scorecards_tenant_gen ON scorecards(tenant_id, generated_at DESC);
```

### 9.2 `score_items`
```sql
CREATE TABLE score_items (
  id                  UUID PRIMARY KEY,
  tenant_id           UUID NOT NULL,
  scorecard_id        UUID NOT NULL REFERENCES scorecards(id),
  rule_code           TEXT NOT NULL,
  rule_version_id     UUID NOT NULL,
  verdict             TEXT NOT NULL CHECK (verdict IN (
                        'pass','fail','not_applicable','Insufficient Evidence'
                      )),
  score               NUMERIC(8,4), -- null when IE
  weight              NUMERIC(8,4) NOT NULL,
  confidence          NUMERIC(5,4) NOT NULL,
  explanation         TEXT NOT NULL,
  evaluated_at        TIMESTAMPTZ NOT NULL,
  judge_model         TEXT,
  evidence_span_ids   UUID[] NOT NULL DEFAULT '{}',
  scoring_path        JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX idx_score_items_scorecard ON score_items(scorecard_id);
CREATE INDEX idx_score_items_rule ON score_items(tenant_id, rule_code);
```

---

## 10. Root Cause, Coaching, Revenue Leak

### 10.1 `cause_taxonomy`
```sql
CREATE TABLE cause_taxonomy (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  cause_code      TEXT NOT NULL, -- RC-OBJ-PRICE-01
  parent_code     TEXT,
  title           TEXT NOT NULL,
  description     TEXT NOT NULL,
  UNIQUE (tenant_id, cause_code)
);
```

### 10.2 `root_cause_results`
```sql
CREATE TABLE root_cause_results (
  id                  UUID PRIMARY KEY,
  tenant_id           UUID NOT NULL,
  call_id             UUID NOT NULL,
  pipeline_run_id     UUID NOT NULL,
  primary_cause_code  TEXT, -- null if IE
  verdict             TEXT NOT NULL CHECK (verdict IN ('identified','Insufficient Evidence')),
  causes              JSONB NOT NULL, -- [{code, confidence, evidence_span_ids, rule_codes}]
  explanation         TEXT NOT NULL,
  generated_at        TIMESTAMPTZ NOT NULL
);
```

### 10.3 `coaching_templates` / `coaching_plans` / `coaching_plan_items`
```sql
CREATE TABLE coaching_templates (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  template_code   TEXT NOT NULL,
  cause_code      TEXT NOT NULL,
  locale          TEXT NOT NULL DEFAULT 'vi',
  title           TEXT NOT NULL,
  body_markdown   TEXT NOT NULL,
  drill_spec      JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (tenant_id, template_code)
);

CREATE TABLE coaching_plans (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  agent_user_id   UUID NOT NULL,
  period_start    DATE NOT NULL,
  period_end      DATE NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('draft','active','completed','cancelled')),
  summary         TEXT NOT NULL,
  generated_at    TIMESTAMPTZ NOT NULL,
  UNIQUE (tenant_id, agent_user_id, period_start, period_end)
);

CREATE TABLE coaching_plan_items (
  id                UUID PRIMARY KEY,
  plan_id           UUID NOT NULL REFERENCES coaching_plans(id),
  tenant_id         UUID NOT NULL,
  cause_code        TEXT,
  template_code     TEXT,
  priority          INT NOT NULL,
  title             TEXT NOT NULL,
  action_markdown   TEXT NOT NULL,
  source_call_ids   UUID[] NOT NULL DEFAULT '{}',
  status            TEXT NOT NULL DEFAULT 'open'
);
```

### 10.4 `revenue_impact_models` / `revenue_leak_estimates`
```sql
CREATE TABLE revenue_impact_models (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  model_code      TEXT NOT NULL,
  rule_code       TEXT,
  cause_code      TEXT,
  formula         JSONB NOT NULL, -- {type: "expected_value_fraction", fraction: 0.15, ...}
  UNIQUE (tenant_id, model_code)
);

CREATE TABLE revenue_leak_estimates (
  id                  UUID PRIMARY KEY,
  tenant_id           UUID NOT NULL,
  call_id             UUID NOT NULL,
  pipeline_run_id     UUID NOT NULL,
  estimated_amount    NUMERIC(14,2),
  currency            CHAR(3),
  verdict             TEXT NOT NULL CHECK (verdict IN ('estimated','Insufficient Evidence','unestimated')),
  components          JSONB NOT NULL,
  explanation         TEXT NOT NULL,
  generated_at        TIMESTAMPTZ NOT NULL
);
```

---

## 11. Dispute, Sprint, Config, Audit

### 11.1 `disputes` / `dispute_events`
```sql
CREATE TABLE disputes (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  call_id         UUID NOT NULL,
  score_item_id   UUID REFERENCES score_items(id),
  opened_by       UUID NOT NULL REFERENCES users(id),
  status          TEXT NOT NULL CHECK (status IN ('open','in_review','upheld','overturned','withdrawn')),
  reason_code     TEXT NOT NULL,
  reason_text     TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at       TIMESTAMPTZ
);

CREATE TABLE dispute_events (
  id              UUID PRIMARY KEY,
  dispute_id      UUID NOT NULL REFERENCES disputes(id),
  actor_id        UUID NOT NULL,
  action          TEXT NOT NULL,
  before          JSONB,
  after           JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 11.2 `sprint_changes`
```sql
CREATE TABLE sprint_changes (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  sprint_label    TEXT NOT NULL, -- 2026-S18
  change_type     TEXT NOT NULL CHECK (change_type IN (
                    'rule','sop','model','prompt_template','config','other'
                  )),
  entity_ref      TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN (
                    'proposed','in_sprint','reviewed','released','monitored','rejected'
                  )),
  title           TEXT NOT NULL,
  description     TEXT NOT NULL,
  requested_by    UUID NOT NULL,
  approved_by     UUID,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 11.3 `tenant_ai_configs`
```sql
CREATE TABLE tenant_ai_configs (
  id              UUID PRIMARY KEY,
  tenant_id       UUID NOT NULL UNIQUE,
  stt_min_avg_confidence NUMERIC(5,4) NOT NULL DEFAULT 0.65,
  diarization_min_confidence NUMERIC(5,4) NOT NULL DEFAULT 0.60,
  redact_before_llm BOOLEAN NOT NULL DEFAULT TRUE,
  llm_judge_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by      UUID
);
```
Thresholds **sống trong DB**, không hardcode trong worker (worker đọc config).

### 11.4 `audit_logs` (append-only)
```sql
CREATE TABLE audit_logs (
  id              UUID PRIMARY KEY,
  tenant_id       UUID,
  actor_id        UUID,
  actor_type      TEXT NOT NULL CHECK (actor_type IN ('user','service','system')),
  action          TEXT NOT NULL,
  entity_type     TEXT NOT NULL,
  entity_id       TEXT NOT NULL,
  before          JSONB,
  after           JSONB,
  reason          TEXT,
  request_id      TEXT,
  trace_id        TEXT,
  ip              INET,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);

CREATE INDEX idx_audit_tenant_time ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id);
```

Revoke UPDATE/DELETE on `audit_logs` from app roles.

---

## 12. Analysis Aggregate

### 12.1 `call_analyses`
```sql
CREATE TABLE call_analyses (
  id                    UUID PRIMARY KEY,
  tenant_id             UUID NOT NULL,
  call_id               UUID NOT NULL UNIQUE,
  pipeline_run_id       UUID NOT NULL,
  rulebook_release_id   UUID NOT NULL,
  status                TEXT NOT NULL,
  result_json           JSONB NOT NULL, -- full canonical JSON Output
  s3_uri                TEXT NOT NULL,
  generated_at          TIMESTAMPTZ NOT NULL,
  schema_version        TEXT NOT NULL
);
CREATE INDEX idx_analyses_tenant_gen ON call_analyses(tenant_id, generated_at DESC);
```

---

## 13. Indexing & Partitioning Strategy

| Table | Strategy |
|-------|----------|
| `calls` | RANGE monthly on `created_at` |
| `audit_logs` | RANGE monthly |
| `transcript_turns` | Consider BRIN on time; PK lookups by transcript |
| Hot filters | Composite `(tenant_id, …)` always leading |

Autovacuum tuned for high-churn score tables.

---

## 14. Consistency & Transactions

- Unit of Work: persist scorecard + score_items + stage success trong **một transaction**.
- Coaching batch có thể async sau — nhưng gắn `pipeline_run_id`.
- Rulebook publish: transaction chèn release items + update campaign pointer + audit.

---

## 15. Insufficient Evidence Persistence

- `score_items.verdict = 'Insufficient Evidence'`
- `score_items.score IS NULL`
- `score_items.evidence_span_ids = '{}'`
- `score_items.explanation` nêu requirement thiếu gì
- Call-level: nếu required auto-fail rules đều IE → `calls.status = 'insufficient_evidence'`

---

## 16. Failure Modes (data)

| Scenario | DB state |
|----------|----------|
| Pipeline fail mid-way | `pipeline_runs.status=failed`; no scorecard row; call `failed` |
| Publish race | Unique `(tenant, campaign, version_label)`; advisory lock |
| Override score | New dispute + new score_item version row OR patch with audit before/after |
| Orphan media | GC job by `call_media` without parent call after TTL |

---

## 17. Retention Jobs

- `job_purge_audio`: delete S3 + mark media after `retention_audio_days`.
- `job_archive_audit`: move old partitions to cold storage; keep metadata.
- Never purge audit earlier than compliance setting.

---

## 18. Seed & Migrations

- Alembic migrations only.
- Seed permissions + system role templates + cause taxonomy baseline.
- Rulebook 1000: bulk import via controlled seed script → `rules`/`rule_versions` → first release — **có audit** `rulebook.seed_import`.

---

## 19. Repository Interfaces (domain ports)

```text
CallRepository
TranscriptRepository
EvidenceRepository
RulebookRepository
ScorecardRepository
RootCauseRepository
CoachingRepository
RevenueLeakRepository
AuditRepository
UserRepository
```

Không SQL từ use-case.

---

## 20. Document Control

| Field | Value |
|-------|-------|
| Owner | Backend Lead |
| Review | Architect, Compliance, DBA |
| Next | `04_API_Spec.md` |

**Hết Database Design v1.0.0**
