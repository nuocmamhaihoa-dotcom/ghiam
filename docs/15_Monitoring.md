# 15 — Monitoring, Health, SLOs & Tracing

| Field | Value |
|-------|-------|
| Document ID | `DOC-OPS-15-MON` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| Stack | OpenTelemetry, Prometheus, Grafana, structured JSON logs |
| Owners | DevOps Lead, Backend Lead, QA Director |
| Related | Deployment probes, Security incidents, Test chaos |

---

## 1. Goals

Provide continuous visibility into availability, scoring correctness signals, pipeline lag, and security events for a system handling **millions of calls per month**. Monitoring must detect both infrastructure failure and **silent quality failure** (e.g., IE rate spikes, hallucination probes in shadow).

---

## 2. Signals taxonomy

| Signal | System | Examples |
|--------|--------|----------|
| Logs | stdout JSON → collector | request, audit mirror, worker |
| Metrics | Prometheus | RED + business |
| Traces | OTLP | analyze pipeline spans |
| Health | HTTP probes | `/health`, `/ready` |
| Profiles | optional pyroscope | CPU hot spots |

---

## 3. Health & readiness

### 3.1 Endpoints (API)

**`GET /health`** — liveness (process up)

```json
{
  "status": "ok",
  "service": "api",
  "version": "1.4.2",
  "ts": "2026-09-05T13:00:00Z"
}
```

**`GET /ready`** — readiness

```json
{
  "status": "ok",
  "checks": {
    "postgres": {"status": "ok", "latency_ms": 3},
    "redis": {"status": "ok", "latency_ms": 1},
    "s3": {"status": "ok", "latency_ms": 25},
    "rulebook_cache": {"status": "ok"}
  }
}
```

Any critical check fail → HTTP **503**, `status: "not_ready"`.

Workers: expose `:8081/health` and heartbeat key `worker:{kind}:{id}:heartbeat` with TTL 30s; exporter scrapes Redis for missing heartbeats.

Web: `GET /api/health` returns `{status:ok}`.

### 3.2 Probe configuration

| Service | Liveness | Readiness | Period |
|---------|----------|-----------|--------|
| api | `/health` | `/ready` | 10s |
| web | `/api/health` | same | 15s |
| workers | `/health` | heartbeat fresh | 15s |

---

## 4. Structured logging

### 4.1 Required fields

```json
{
  "timestamp": "2026-09-05T13:00:00.123Z",
  "level": "INFO",
  "service": "api",
  "trace_id": "01JABC...",
  "span_id": "xyz",
  "tenant_id": "uuid-or-null",
  "actor_id": "uuid-or-null",
  "action": "call.analyze",
  "call_id": "uuid-or-null",
  "message": "analysis completed",
  "duration_ms": 842,
  "outcome": "scored",
  "rulebook_version": "rb-2026.09.01-a3f2"
}
```

Levels: DEBUG, INFO, WARN, ERROR. No PII in `message` (phones masked). Full audit content lives in `audit.events`, not application logs.

### 4.2 Log sampling

- INFO sample 100% on errors/WARN.
- High-volume successful GET list endpoints sample 10% in production.
- Always keep 100% for `call.analyze`, `appeal.*`, `sop.publish`, `security.*`.

---

## 5. Metrics catalog

### 5.1 RED (per service)

| Metric | Type | Labels |
|--------|------|--------|
| `http_requests_total` | counter | service, method, route, status |
| `http_request_duration_seconds` | histogram | service, route |
| `http_requests_in_flight` | gauge | service |

### 5.2 Pipeline / business

| Metric | Type | Labels | Notes |
|--------|------|--------|-------|
| `calls_ingested_total` | counter | tenant | |
| `calls_scored_total` | counter | tenant, industry, status | status=scored\|partial\|failed |
| `analysis_duration_seconds` | histogram | stage=stt\|vcie\|score | |
| `queue_lag_seconds` | gauge | stream | Redis consumer lag |
| `queue_dlq_depth` | gauge | stream | |
| `score_value` | summary/histogram | tenant, industry | distribution |
| `insufficient_evidence_rules_total` | counter | tenant, rule_id | |
| `ie_rate` | gauge | tenant | computed recording rule |
| `violations_total` | counter | tenant, severity, rule_id | |
| `revenue_leak_vnd_sum` | counter | tenant, leak_code | only status=ok |
| `appeals_open` | gauge | tenant | |
| `appeal_age_hours` | histogram | tenant | |
| `rulebook_cache_hit_ratio` | gauge | | |
| `stt_realtime_factor` | gauge | worker | audio_sec / wall_sec |
| `calibration_f1` | gauge | model, task | nightly |
| `anti_hallucination_fail_total` | counter | probe_id | must stay 0 |

### 5.3 Security

| Metric | Type |
|--------|------|
| `auth_failures_total` | counter (reason) |
| `tenant_isolation_violations_total` | counter |
| `pii_export_total` | counter |
| `rate_limit_triggered_total` | counter |

---

## 6. SLOs

### 6.1 Availability

| SLO | Target | Window |
|-----|--------|--------|
| API availability (`/ready` success from probe) | 99.9% | 30d |
| Web availability | 99.9% | 30d |
| Scoring pipeline success (ingested→scored\|partial) | 99.5% | 30d |

Error budget: burn-rate alerts (multi-window).

### 6.2 Latency

| SLO | Target |
|-----|--------|
| `GET /analysis` p95 | ≤ 500 ms |
| `POST /analyze` when features ready p95 | ≤ 2.5 s |
| End-to-end audio≤10 min call p95 | ≤ 90 s |
| Dashboard KPI API p95 | ≤ 1.5 s |

### 6.3 Quality / correctness SLIs

| SLI | Target |
|-----|--------|
| IE rate (rules evaluated as IE / total required rules) | within ±5pp of 30d baseline |
| Anti-hallucination probes | 0 failures / day |
| Appeal overturn rate | monitor only (informational) |
| Calibration F1 dialect/intent | ≥ gates in `11_Test_Cases.md` |

### 6.4 Freshness

| SLI | Target |
|-----|--------|
| STT queue lag p95 | ≤ 5 min |
| Analytics materialized view lag | ≤ 5 min |

---

## 7. Alerting rules

### 7.1 Paging (P1)

| Alert | Condition | For |
|-------|-----------|-----|
| `APIReadyDown` | ready probe fail | 2m |
| `ScorePipelineStalled` | `queue_lag_seconds{stream="stt"}>900` | 5m |
| `ErrorBudgetBurnFast` | burn > 14× | 5m & 1h |
| `AntiHallucinationFail` | `increase(anti_hallucination_fail_total[1h])>0` | immediate |
| `TenantIsolationViolation` | any increase | immediate |
| `PostgresDown` | up==0 | 1m |

### 7.2 High (P2)

| Alert | Condition |
|-------|-----------|
| `AnalyzeP95High` | p95 > 2.5s for 15m |
| `IERateSpike` | ie_rate > baseline+10pp for 30m |
| `DLQDepthHigh` | dlq_depth > 100 for 10m |
| `AppealSLABreach` | appeals older than 48h > 0 |
| `RulebookCacheHitLow` | hit_ratio < 0.8 for 20m |
| `S3WriteFailures` | error rate > 1% 10m |

### 7.3 Notification channels

- P1: on-call phone/SMS + Slack `#telesale-sev`
- P2: Slack `#telesale-alerts`
- Security: `#telesale-security` + email SecOps

Alert annotations include runbook URL and `trace_id` exemplar query.

---

## 8. Distributed tracing

### 8.1 Span model for analyze

```
HTTP POST /api/v1/calls/{id}/analyze
  └─ Application.AnalyzeCall
       ├─ db.load_call
       ├─ redis.enqueue (if async)
       └─ (worker) pipeline
            ├─ stt.transcribe
            ├─ vcie.extract
            │    ├─ dialect
            │    ├─ intents
            │    ├─ objections
            │    ├─ buying_signals
            │    ├─ emotion
            │    └─ silence
            ├─ rulebook.load_version
            ├─ rulebook.evaluate
            ├─ db.insert_analysis
            └─ audit.append
```

Attributes: `tenant.id`, `call.id`, `sop.id`, `rulebook.version`, `pipeline.version`, `analysis.status`.

### 8.2 Sampling

- Head sample 5% baseline.
- Always sample: errors, analyze, appeals, latency > SLO.
- Tail-based preferred when collector supports.

---

## 9. Grafana dashboards

| Dashboard | Panels |
|-----------|--------|
| `TEL-Overview` | Availability, error budget, ingest/score rates, lag |
| `TEL-API` | RED, top routes, saturation |
| `TEL-Pipeline` | Per-stage duration, lag, DLQ, GPU util |
| `TEL-Quality` | Score distributions, IE rate, violations, calibration F1 |
| `TEL-RevenueLeak` | leak VND by code/team (ok only), IE excluded count |
| `TEL-Appeals` | open/age/SLA |
| `TEL-Security` | auth fails, isolation, pii exports |
| `TEL-SLO` | burn rates per SLO |

Dashboards as code: `deploy/grafana/dashboards/*.json` provisioned.

---

## 10. Correlation with AnalysisResult

Exporters must not scrape raw AnalysisResult bodies. Instead metrics emit from service counters when persisting analyses:

- Increment `calls_scored_total{status=...}`
- Observe `score_value`
- Increment IE / violation counters from envelope fields
- Add `revenue_leak_vnd_sum` only when `revenue_leak.status == "ok"`

Exemplars link histogram buckets to `trace_id` of a sample analyze.

---

## 11. Runbooks (index)

| Alert | Runbook path |
|-------|--------------|
| APIReadyDown | `docs/runbooks/api_ready.md` |
| ScorePipelineStalled | `docs/runbooks/pipeline_lag.md` |
| IERateSpike | `docs/runbooks/ie_spike.md` |
| AntiHallucinationFail | `docs/runbooks/anti_hallucination.md` |
| AppealSLABreach | `docs/runbooks/appeals_sla.md` |
| TenantIsolationViolation | `docs/runbooks/security_isolation.md` |

Each runbook: symptoms → verify → mitigate → escalate → postmortem checklist.

---

## 12. Synthetic monitoring

| Check | Frequency | Assert |
|-------|-----------|--------|
| Login + dashboard KPI | 1 min | 200 + JSON keys |
| Analyze fixture call | 5 min | AnalysisResult 7 fields |
| Insufficient Evidence fixture | 15 min | IE status strings |
| Audio signed URL fetch | 5 min | 200 within TTL |

Synthetics run from same residency region.

---

## 13. Capacity & autoscaling signals

Scale on:

- `queue_lag_seconds` (workers)
- `http_requests_in_flight` / CPU (API)
- GPU util / `stt_realtime_factor` (STT)

HPA-style thresholds documented in `13_Deployment.md` replica env vars.

---

## 14. Data retention for telemetry

| Telemetry | Retention |
|-----------|-----------|
| Raw metrics | 15d local; 13m aggregated |
| Traces | 7d (14d errors) |
| App logs | 30d |
| Security logs | 365d |
| Audit DB | 730d |

---

## 15. Implementation wiring (Clean Architecture)

- Metrics/tracing adapters in Infrastructure implementing `IMetrics`, `ITracer`.
- Domain services remain free of Prometheus client imports.
- Composition root configures OpenTelemetry SDK once.
- Health checks use repository `ping()` methods.

```python
class IMetrics(Protocol):
    def incr(self, name: str, labels: dict[str, str] | None = None) -> None: ...
    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None: ...
```

---

## 16. Acceptance criteria

1. `/health` and `/ready` implemented with dependency checks.
2. JSON structured logs include timestamp, level, service, trace_id, actor_id, action.
3. Prometheus metrics cover RED + pipeline + IE + leak + appeals + security.
4. SLOs defined with burn-rate alerts.
5. Traces cover analyze pipeline end-to-end.
6. Grafana dashboards provisioned as code.
7. Synthetics assert AnalysisResult envelope fields continuously.
8. Anti-hallucination failures page immediately.
