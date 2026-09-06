# AQATE Backend

FastAPI Clean Architecture backend for AI QA TELESALE ENTERPRISE.

## Quick start (Docker)

From repo root:

```bash
cp .env.example .env
docker compose up --build
```

API: http://localhost:8000  
OpenAPI: http://localhost:8000/docs  
Health: http://localhost:8000/health

Default admin: `admin@aqate.local` / `ChangeMeAdmin123!`

## Local (without Docker for API only)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# start postgres + redis + minio via docker compose up postgres redis minio -d
cp ../.env.example .env
alembic upgrade head
python scripts/seed.py
uvicorn app.main:app --reload --port 8000
```

## Scoring response contract

Every scoring endpoint returns:

```json
{
  "score": 0,
  "stage_scores": {},
  "violations": [],
  "evidence": [],
  "root_cause": {},
  "coaching": {},
  "revenue_leak": {}
}
```

Empty evidence → `Insufficient Evidence` (never invented scores).
