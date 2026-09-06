# API Health Report (Phase 8)

Generated: `2026-09-06T01:17:23.409312+00:00`  
Evidence: `phase_multi_evidence.json#api` + remediations in `backend/app`

- Router count: **32**
- OpenAPI candidates: `['datasets/validation/schema.json', 'models/cltv/schema.json', 'models/negotiation/schema.json', 'models/war_room/schema.json', 'models/digital_twin/schema.json', 'models/audio_intelligence/schema.json', 'models/autonomous/schema.json']`
- Auth-related files (sample): `['scripts/quality_gate.py', 'backend/app/main.py', 'backend/scripts/seed.py', 'backend/app/core/deps.py', 'backend/app/core/security.py', 'backend/app/interfaces/api/routers/cltv.py', 'backend/app/interfaces/api/routers/coaching.py', 'backend/app/interfaces/api/routers/revenue_leak.py', 'backend/app/interfaces/api/routers/datasets.py', 'backend/app/interfaces/api/routers/analytics.py', 'backend/app/interfaces/api/routers/health.py', 'backend/app/interfaces/api/routers/autonomous.py', 'backend/app/interfaces/api/routers/simulator.py', 'backend/app/interfaces/api/routers/forecast.py', 'backend/app/interfaces/api/routers/auth.py']`

| Control | Status | Evidence |
|---------|--------|----------|
| Rate limit | Fixed | `backend/app/interfaces/api/rate_limit.py` |
| Prod secret assert | Fixed | `config.assert_production_secrets` |
| CORS harden | Planned | ISS-009 |

## API Health Score: **80/100**
