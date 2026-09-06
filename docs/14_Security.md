# 14 — Security Architecture

| Field | Value |
|-------|-------|
| Document ID | `DOC-SEC-14` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| AuthN/Z | JWT + RBAC |
| Owners | Security, Backend Lead, DevOps |
| Related | Deployment secrets, Monitoring alerts, UI RBAC |

---

## 1. Objectives

Protect multi-tenant telesales audio, transcripts, PII, Rulebook integrity, and scoring outcomes at enterprise scale. Enforce **least privilege**, **evidence-safe AI**, and **full audit** of security-relevant and business-relevant mutations.

---

## 2. Trust boundaries

```
[Browser Employee/QA/Admin]
        │ TLS 1.2+
        ▼
[Next.js Web] ── BFF cookies / bearer ──► [API FastAPI]
                                              │
                    ┌─────────────────────────┼──────────────────────┐
                    ▼                         ▼                      ▼
               PostgreSQL                   Redis                   S3
              (PII encrypted)            (no raw PII)         (SSE-KMS)
```

Workers are inside the private network; no public STT ports.

---

## 3. Authentication (JWT)

### 3.1 Token profile

| Claim | Required | Notes |
|-------|----------|-------|
| `iss` | yes | matches `JWT_ISSUER` |
| `aud` | yes | `telesale-api` |
| `sub` | yes | user id |
| `tenant_id` | yes | UUID |
| `roles` | yes | string array |
| `permissions` | optional | fine-grained |
| `exp` / `iat` / `nbf` | yes | |
| `jti` | yes | revocation list |

Algorithms: **RS256** or **ES256** only. Validate via JWKS URL; cache keys 10m with kid rotation.

### 3.2 Flows

1. Enterprise IdP (OIDC) → Next.js session → API calls with access token.
2. Service accounts for PBX ingest: client-credentials JWT with role `ingest_service`.
3. Short-lived S3 signed URLs for audio playback (60–300s).

### 3.3 API middleware

```python
# Interface layer only
async def require_auth(request) -> Principal:
    token = extract_bearer(request)
    claims = jwt_validator.validate(token)  # DI
    if claims.jti in revocations: raise Unauthorized
    return Principal.from_claims(claims)
```

---

## 4. RBAC

### 4.1 Roles

| Role | Description |
|------|-------------|
| `executive` | Org dashboards read |
| `manager` | Team dashboards + coaching assign |
| `qa_analyst` | Review calls, annotate, open calibration |
| `qa_manager` | Publish SOP/rules, decide appeals |
| `admin` | Tenant users, integrations, flags |
| `employee` | Own calls + own appeals |
| `ml_ops` | Model/pipeline versions, shadow |
| `ingest_service` | Call ingest only |
| `auditor` | Read-only audit + analyses |

### 4.2 Permission matrix (core)

| Permission | exec | mgr | qa_a | qa_m | admin | emp | ml | ingest | auditor |
|------------|:---:|:---:|:---:|:---:|:-----:|:---:|:--:|:------:|:-------:|
| `dashboard.read` | ✓ | ✓ | ✓ | ✓ | ✓ | | | | ✓ |
| `calls.read.team` | ✓ | ✓ | ✓ | ✓ | ✓ | | | | ✓ |
| `calls.read.self` | | | | | | ✓ | | | |
| `calls.analyze` | | | ✓ | ✓ | | | ✓ | | |
| `appeals.open` | | | ✓ | ✓ | | ✓ | | | |
| `appeals.decide` | | | | ✓ | | | | | |
| `sop.publish` | | | | ✓ | ✓ | | | | |
| `rulebook.write` | | | | ✓ | ✓ | | | | |
| `users.manage` | | | | | ✓ | | | | |
| `pii.export` | | | | ✓ | ✓ | | | | ✓ |
| `audit.read` | | | | ✓ | ✓ | | | | ✓ |
| `ingest.write` | | | | | | | | ✓ | |
| `economics.write` | | | | ✓ | ✓ | | | | |

Enforcement in **application services** via `IAuthorizationService`, not only in UI.

### 4.3 Tables

```sql
CREATE TABLE auth.roles (
  code VARCHAR(32) PRIMARY KEY,
  description TEXT NOT NULL
);

CREATE TABLE auth.permissions (
  code VARCHAR(64) PRIMARY KEY,
  description TEXT NOT NULL
);

CREATE TABLE auth.role_permissions (
  role_code VARCHAR(32) REFERENCES auth.roles(code),
  permission_code VARCHAR(64) REFERENCES auth.permissions(code),
  PRIMARY KEY (role_code, permission_code)
);

CREATE TABLE auth.user_roles (
  user_id UUID NOT NULL,
  tenant_id UUID NOT NULL,
  role_code VARCHAR(32) NOT NULL REFERENCES auth.roles(code),
  PRIMARY KEY (user_id, tenant_id, role_code)
);
```

JWT `roles` must match DB for privileged actions (`sop.publish`, `appeals.decide`) — re-check on mutation (defense in depth).

---

## 5. Multi-tenant isolation

- Every row with tenant scope includes `tenant_id`.
- Repository layer applies `WHERE tenant_id = :tid` from `Principal` — handlers cannot pass arbitrary tenant without `admin` impersonation permission.
- S3 keys prefixed `tenant/{tenant_id}/...`.
- Redis keys prefixed `t:{tenant_id}:`.
- Cross-tenant access attempts: 404 (no existence leak) + security audit event `security.tenant_isolation_violation`.

---

## 6. Data residency

| Policy key | Default | Enforcement |
|------------|---------|-------------|
| `DATA_RESIDENCY_REGION` | `VN` | Stored on tenant; blocks bucket region mismatch |
| Allowed regions | `VN`, `SG`, `ID` (configurable) | Deploy-time + runtime check on S3 client |
| Subprocessor list | IdP, cloud, STT vendor | Contract register in Admin |

Audio and transcripts must not replicate outside allowed regions unless tenant signs addendum (flag `residency_override` audited).

---

## 7. PII handling

### 7.1 PII inventory

| Field | Store | Control |
|-------|-------|---------|
| Customer phone | Postgres | Encrypt + mask in UI |
| Customer name | Postgres | Encrypt optional; mask employee |
| Address | Postgres / transcript | Encrypt at rest; minimize in indexes |
| National ID | rare | Encrypt; `pii.export` only |
| Audio voice | S3 | Bucket encryption; short signed URL |
| Agent employee id | Postgres | Internal |

### 7.2 Encryption

- **At rest DB:** TDE (cloud) + application-level AES-GCM for phone/address using `PII_ENCRYPTION_KEY` (envelope: DEK in row, KEK in secret manager).
- **At rest S3:** SSE-S3 or SSE-KMS.
- **In transit:** TLS everywhere; internal mTLS optional for workers.
- **Redis:** store hashes/ids, not raw phone; transcripts cached only as call_id pointers with TTL ≤ 15m.

### 7.3 Masking API

```json
{
  "phone_masked": "+84******789",
  "phone_full": null
}
```

`phone_full` only if permission `pii.export` and purpose header `X-PII-Purpose` logged.

### 7.4 Retention

| Data | Default retention | Action |
|------|-------------------|--------|
| Audio | 365 days | lifecycle delete |
| Transcript | 365 days | anonymize or delete |
| Analysis | 365 days | keep scores aggregate |
| Audit | 730 days | immutable |
| Appeals | 730 days | |

Tenant overrides in `tenant_policies`; scheduled worker enforces.

---

## 8. Rulebook & scoring integrity

1. Rules cannot be changed without `rulebook.write` + audit before/after.
2. Production scores reference immutable `rulebook_version` checksum.
3. Prompt templates **forbidden** from containing industry pass criteria — linters in CI (`scripts/check_no_hardcoded_rules.py`).
4. Model outputs pass `EvidenceValidator` — claims without spans → drop / IE.
5. Appeal overturns create new analysis revision; never silent UPDATE of score history.

---

## 9. Audit logging

### 9.1 Schema

```sql
CREATE TABLE audit.events (
  id              UUID PRIMARY KEY,
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  tenant_id       UUID,
  actor_id        UUID,
  actor_type      VARCHAR(16) NOT NULL, -- user|service|system
  action          VARCHAR(64) NOT NULL,
  entity_type     VARCHAR(64) NOT NULL,
  entity_id       VARCHAR(128) NOT NULL,
  before          JSONB,
  after           JSONB,
  trace_id        VARCHAR(64),
  ip              INET,
  user_agent      TEXT,
  prev_hash       CHAR(64),
  event_hash      CHAR(64) NOT NULL
);
CREATE INDEX idx_audit_tenant_ts ON audit.events(tenant_id, ts DESC);
CREATE INDEX idx_audit_action ON audit.events(action, ts DESC);
```

Hash chain: `event_hash = HMAC(AUDIT_HASH_KEY, prev_hash + payload)`. No UPDATE/DELETE grants to app role.

### 9.2 Mandatory audited actions

`user.login`, `user.role_change`, `sop.publish`, `rulebook.write`, `call.analyze`, `call.reanalyze`, `appeal.open`, `appeal.decide`, `pii.export`, `integration.update`, `secret.rotate`, `policy.retention_change`, `tenant.impersonate`.

Structured app logs (JSON) are separate from audit; both include `trace_id`.

---

## 10. API security controls

| Control | Implementation |
|---------|----------------|
| Input validation | Pydantic models; max transcript size 1MB |
| Rate limit | Redis token bucket per principal + IP |
| CORS | allowlist only |
| CSRF | SameSite cookies for web session |
| SSRF | Deny internal IPs on webhook URLs |
| Upload | Content-type allowlist audio; virus scan async |
| OpenAPI | Publish without internal admin paths publicly if needed via filtered build |

Standard error body: no stack traces in production; include `trace_id`.

---

## 11. AnalysisResult & security

Even error/partial responses that include scoring sections must not leak other tenants’ evidence. Field-level redaction service runs before serialization for `employee` role (mask PII in quotes when policy requires).

Envelope fields always present structurally; sensitive monetary org rollups omitted for employees.

---

## 12. Vulnerability & compliance ops

- Dependency scanning in CI (pip/npm).
- Container image scan gate on critical CVEs.
- Annual pen-test; quarterly authz regression (`SEC-*` tests in `11_Test_Cases.md`).
- GDPR/PDPA-like DSARs: export/delete pipeline with audit.

---

## 13. Incident response (security)

| Severity | Example | Response |
|----------|---------|----------|
| SEV1 | Cross-tenant audio leak | Revoke keys, take slice offline, notify |
| SEV2 | Privilege escalation | Force re-login, patch, audit review |
| SEV3 | Failed brute force spike | Auto-ban / WAF |

Runbooks linked from on-call; alerts in `15_Monitoring.md`.

---

## 14. Acceptance criteria

1. JWT validation + RBAC re-check on mutations.
2. Tenant isolation enforced in repositories.
3. PII encrypted + masked; export permissioned + audited.
4. Data residency config blocks wrong-region buckets.
5. Audit hash chain append-only for mandatory actions.
6. No hardcoded business rules in code (CI check).
7. EvidenceValidator prevents evidence-less conclusions from persisting as pass/fail.
