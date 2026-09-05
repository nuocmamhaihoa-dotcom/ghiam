# 13 — Deployment, Environments & Scaling

| Field | Value |
|-------|-------|
| Document ID | `DOC-OPS-13-DEPLOY` |
| Version | `1.0.0` |
| Status | Approved for Enterprise Implementation |
| Packaging | Docker + Docker Compose (base); K8s-compatible images |
| Owners | DevOps Lead, Backend Lead |
| Related | Security secrets (`14`), Monitoring (`15`) |

---

## 1. Principles

1. Immutable images; config via environment / secret manager.
2. Migrations are versioned, forward-only in prod; expand-contract pattern.
3. Blue/green for API & workers; sticky rulebook versions per in-flight analysis.
4. No secrets in git; Compose uses `secrets:` or `.env` excluded from VCS.
5. Scale to millions of calls via horizontal workers + Redis streams + Postgres read replicas + S3.

---

## 2. Service topology

| Service | Image | Role |
|---------|-------|------|
| `web` | `telesale/web` | Next.js frontend |
| `api` | `telesale/api` | FastAPI application |
| `worker-ingest` | `telesale/worker` | cmd=`ingest` |
| `worker-stt` | `telesale/worker` | cmd=`stt` (GPU optional) |
| `worker-vcie` | `telesale/worker` | cmd=`vcie` |
| `worker-score` | `telesale/worker` | cmd=`score` |
| `scheduler` | `telesale/worker` | cmd=`scheduler` |
| `postgres` | `postgres:16-alpine` | Primary DB |
| `redis` | `redis:7-alpine` | Cache + streams |
| `minio` | `minio/minio` | S3-compatible (dev/stage) |
| `migrate` | `telesale/api` | one-shot alembic |
| `otel-collector` | `otel/opentelemetry-collector` | Telemetry |
| `prometheus` | `prom/prometheus` | Metrics |
| `grafana` | `grafana/grafana` | Dashboards |

Production may replace MinIO with AWS S3 / GCS; replace Compose orchestration with ECS/K8s while keeping same images & env contract.

---

## 3. Docker Compose (enterprise baseline)

File: `deploy/docker-compose.yml`

```yaml
name: telesale-enterprise

x-api-env: &api-env
  APP_ENV: ${APP_ENV:-staging}
  DATABASE_URL: postgresql+psycopg://telesale:${POSTGRES_PASSWORD}@postgres:5432/telesale
  REDIS_URL: redis://redis:6379/0
  S3_ENDPOINT: ${S3_ENDPOINT:-http://minio:9000}
  S3_BUCKET: ${S3_BUCKET:-telesale-calls}
  S3_ACCESS_KEY: ${S3_ACCESS_KEY}
  S3_SECRET_KEY: ${S3_SECRET_KEY}
  JWT_ISSUER: ${JWT_ISSUER}
  JWT_AUDIENCE: ${JWT_AUDIENCE}
  JWT_PUBLIC_JWKS_URL: ${JWT_PUBLIC_JWKS_URL}
  OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
  LOG_LEVEL: ${LOG_LEVEL:-INFO}
  RULEBOOK_CACHE_TTL_SEC: ${RULEBOOK_CACHE_TTL_SEC:-60}

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: telesale
      POSTGRES_USER: telesale
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    secrets: [postgres_password]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U telesale -d telesale"]
      interval: 5s
      timeout: 5s
      retries: 20
    networks: [internal]

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes: ["redisdata:/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 20
    networks: [internal]

  minio:
    image: minio/minio:RELEASE.2025-01-20T14-49-07Z
    command: ["server", "/data", "--console-address", ":9001"]
    environment:
      MINIO_ROOT_USER_FILE: /run/secrets/minio_root_user
      MINIO_ROOT_PASSWORD_FILE: /run/secrets/minio_root_password
    secrets: [minio_root_user, minio_root_password]
    volumes: ["miniodata:/data"]
    networks: [internal]

  migrate:
    image: telesale/api:${IMAGE_TAG:-latest}
    command: ["alembic", "upgrade", "head"]
    environment: *api-env
    secrets: [postgres_password]
    depends_on:
      postgres:
        condition: service_healthy
    networks: [internal]
    restart: "on-failure"

  api:
    image: telesale/api:${IMAGE_TAG:-latest}
    environment:
      <<: *api-env
      UVICORN_WORKERS: ${UVICORN_WORKERS:-4}
    secrets: [postgres_password, jwt_private_optional]
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
    ports: ["8080:8080"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      retries: 12
    networks: [internal, public]
    deploy:
      replicas: ${API_REPLICAS:-2}
      update_config:
        order: start-first
        failure_action: rollback

  worker-ingest:
    image: telesale/worker:${IMAGE_TAG:-latest}
    command: ["python", "-m", "workers", "ingest"]
    environment: *api-env
    depends_on: [api, redis, minio]
    networks: [internal]
    deploy:
      replicas: ${WORKER_INGEST_REPLICAS:-2}

  worker-stt:
    image: telesale/worker:${IMAGE_TAG:-latest}
    command: ["python", "-m", "workers", "stt"]
    environment: *api-env
    networks: [internal]
    deploy:
      replicas: ${WORKER_STT_REPLICAS:-2}
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  worker-vcie:
    image: telesale/worker:${IMAGE_TAG:-latest}
    command: ["python", "-m", "workers", "vcie"]
    environment: *api-env
    networks: [internal]
    deploy:
      replicas: ${WORKER_VCIE_REPLICAS:-4}

  worker-score:
    image: telesale/worker:${IMAGE_TAG:-latest}
    command: ["python", "-m", "workers", "score"]
    environment: *api-env
    networks: [internal]
    deploy:
      replicas: ${WORKER_SCORE_REPLICAS:-4}

  web:
    image: telesale/web:${IMAGE_TAG:-latest}
    environment:
      NEXT_PUBLIC_API_BASE: ${NEXT_PUBLIC_API_BASE:-https://api.example.com}
      NEXTAUTH_URL: ${NEXTAUTH_URL}
    ports: ["3000:3000"]
    depends_on:
      api:
        condition: service_healthy
    networks: [public, internal]

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.111.0
    volumes: ["./otel-collector.yaml:/etc/otelcol/config.yaml"]
    networks: [internal]

  prometheus:
    image: prom/prometheus:v2.54.1
    volumes: ["./prometheus.yml:/etc/prometheus/prometheus.yml"]
    networks: [internal]

  grafana:
    image: grafana/grafana:11.2.0
    networks: [internal, public]
    ports: ["3001:3000"]

secrets:
  postgres_password:
    file: ./secrets/postgres_password.txt
  minio_root_user:
    file: ./secrets/minio_root_user.txt
  minio_root_password:
    file: ./secrets/minio_root_password.txt
  jwt_private_optional:
    file: ./secrets/jwt_private.pem

volumes:
  pgdata:
  redisdata:
  miniodata:

networks:
  internal:
    internal: true
  public:
```

Dev override: `deploy/docker-compose.dev.yml` exposes Postgres `5432`, MinIO console, reduces replicas.

---

## 4. Environment variable catalog

### 4.1 Required (all envs)

| Variable | Example | Description |
|----------|---------|-------------|
| `APP_ENV` | `production` | `local\|staging\|production` |
| `DATABASE_URL` | `postgresql+psycopg://...` | Primary |
| `DATABASE_READ_URL` | optional replica | Read APIs |
| `REDIS_URL` | `redis://redis:6379/0` | |
| `S3_ENDPOINT` | `https://s3.amazonaws.com` | |
| `S3_BUCKET` | `telesale-prod-calls` | |
| `S3_REGION` | `ap-southeast-1` | Data residency default VN/SG |
| `S3_ACCESS_KEY` | ***** | Prefer IAM role in prod |
| `S3_SECRET_KEY` | ***** | |
| `JWT_ISSUER` | `https://auth.example.com/` | |
| `JWT_AUDIENCE` | `telesale-api` | |
| `JWT_PUBLIC_JWKS_URL` | `https://auth.../jwks` | |
| `CORS_ORIGINS` | `https://app.example.com` | |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel:4317` | |
| `PII_ENCRYPTION_KEY` | base64 32B | Envelope encryption |
| `AUDIT_HASH_KEY` | base64 | Audit chain HMAC |

### 4.2 Optional tuning

| Variable | Default | Purpose |
|----------|---------|---------|
| `UVICORN_WORKERS` | 4 | API concurrency |
| `RULEBOOK_CACHE_TTL_SEC` | 60 | Redis cache |
| `STT_MAX_CONCURRENCY` | 4 | Per worker |
| `ANALYZE_TIMEOUT_SEC` | 120 | |
| `APPEAL_SLA_HOURS` | 48 | |
| `FEATURE_SHADOW_MODEL` | false | |
| `DATA_RESIDENCY_REGION` | `VN` | Policy tag |

### 4.3 Frontend

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE` | Browser API |
| `NEXTAUTH_URL` / auth client IDs | OIDC |
| `NEXT_PUBLIC_APP_ENV` | Badge |

---

## 5. Migrations

Tool: **Alembic** in `backend/migrations/`.

### 5.1 Procedure (production)

1. Take DB snapshot / verify PITR window.
2. `migrate` job runs `alembic upgrade head` against primary.
3. Expand-contract: add columns nullable → deploy code → backfill worker → set NOT NULL → drop old.
4. SOP/Rulebook seed migrations are separate revision files; never edit applied revisions.
5. Record migration in audit `schema.migrate` with revision id.
6. On failure: stop deploy; `alembic downgrade -1` only if revision marked `downgrade_safe=true` in comments; else restore snapshot.

### 5.2 Commands

```bash
# local
docker compose -f deploy/docker-compose.yml run --rm migrate
docker compose -f deploy/docker-compose.yml exec api alembic current
docker compose -f deploy/docker-compose.yml exec api alembic history
```

### 5.3 Data migrations for Rulebook

Large rule inserts use batched `COPY` and checksum verification job `validate_sop_bindings` before flipping `status=active`.

---

## 6. Image build & promotion

```bash
# CI
docker build -t telesale/api:$GIT_SHA -f backend/Dockerfile .
docker build -t telesale/worker:$GIT_SHA -f backend/Dockerfile.worker .
docker build -t telesale/web:$GIT_SHA -f frontend/Dockerfile .
docker push ...
```

Promotion path: `CI build → staging deploy → smoke + calibration → prod blue/green`.

Image tags: immutable SHA; `latest` only on staging.

---

## 7. Scaling guide (millions of calls)

| Bottleneck | Scale action |
|------------|--------------|
| API RPS | Increase `API_REPLICAS`; add LB |
| STT lag | Add GPU `worker-stt` replicas; shard streams by `tenant_id` |
| VCIE lag | Scale `worker-vcie`; enable ONNX batching |
| Score lag | Scale `worker-score`; ensure rule cache warm |
| Postgres writes | Separate analyses partitioned by month; read replicas for dashboards |
| Redis memory | Split cache DB vs stream DB; set maxmemory-policy `allkeys-lru` on cache only |
| S3 | Prefix by `tenant_id/yyyy/mm/dd/`; multipart upload |
| Dashboard heavy queries | Materialized views refreshed every 5m |

Capacity planning formula (approx):

```
calls_per_day = 5_000_000 / 30 ≈ 166_700
avg_audio_min = 4 → ~11,100 audio-hours/day
stt_gpu_needed ≈ audio_hours / (120 hours_per_gpu_hour * 16h) ≈ 6 GPUs (+buffer 30%)
```

---

## 8. Blue / green deployment

### 8.1 API

1. Deploy **green** stack with new `IMAGE_TAG` on alternate Compose project / K8s color.
2. Run migrate (expand-only) before green receives traffic.
3. Smoke: `/health`, `/ready`, analyze fixture call → assert AnalysisResult envelope.
4. Shift LB weight 10% → 50% → 100%.
5. Keep blue 1 hour for rollback.
6. Drain blue workers after stream consumer group migrates.

### 8.2 Workers

- Use Redis consumer groups `cg_stt`, `cg_vcie`, `cg_score`.
- Green joins same group; scale blue to 0 after lag=0.
- In-flight messages carry `pipeline_version`; mixed versions allowed.

### 8.3 Frontend

- Blue/green via CDN / LB on `web`.
- `NEXT_PUBLIC_API_BASE` points to API LB (color-agnostic).

### 8.4 Rollback

```bash
# re-point LB to blue tag
IMAGE_TAG=$PREV_SHA docker compose up -d api web worker-score worker-vcie
# Do NOT downgrade DB unless revision safe
```

---

## 9. Secrets management

| Env | Mechanism |
|-----|-----------|
| local | `deploy/secrets/*.txt` gitignored |
| staging | Docker secrets / Vault Agent |
| production | AWS Secrets Manager / GCP Secret Manager / Vault; injected as files or env at runtime |

Rotation:

1. Issue new secret version.
2. Dual-read window for JWT keys (JWKS contains both).
3. Rotate DB password with reconnect pool recycle.
4. Audit `secret.rotate`.

Forbidden: secrets in Next.js `NEXT_PUBLIC_*`.

---

## 10. Backup & DR

| Asset | RPO | RTO | Method |
|-------|-----|-----|--------|
| Postgres | ≤ 5 min | ≤ 1 h | PITR + daily full |
| Redis streams | ≤ 15 min | ≤ 1 h | AOF + replay from S3 raw |
| S3 audio | ≤ 0 (11 9s) | ≤ 15 min | Cross-region replication optional per contract |
| Rulebook | ≤ 5 min | ≤ 30 min | DB PITR + export JSON nightly |

DR drill quarterly documented in runbooks.

---

## 11. Local developer bootstrap

```bash
cp deploy/env.example deploy/.env
mkdir -p deploy/secrets && openssl rand -base64 32 > deploy/secrets/postgres_password.txt
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
docker compose run --rm migrate
# seed
docker compose exec api python -m scripts.seed_platform_sop
npm run dev --prefix frontend
```

---

## 12. Health endpoints used by orchestrator

| Path | Service | Use |
|------|---------|-----|
| `GET /health` | api | liveness |
| `GET /ready` | api | readiness (DB, Redis, S3 head) |
| `GET /health` | web | liveness |
| worker heartbeat | Redis key `worker:{id}:heartbeat` | custom exporter |

Compose / K8s probes must use these (see `15_Monitoring.md`).

---

## 13. Acceptance criteria

1. Single Compose file brings up full stack with healthchecks.
2. Env catalog complete; no undocumented required vars.
3. Migration procedure expand-contract documented and used.
4. Blue/green steps executable without downtime for API reads.
5. Secrets not in repository; rotation procedure defined.
6. Scaling knobs (replicas, partitions) documented for ≥5M calls/month.
