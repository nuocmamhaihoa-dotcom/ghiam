# Audit — Repository (Phase 1)

Generated: `2026-09-06T01:17:23.409312+00:00`  
Evidence: `reports/audit/phase1_repo_inventory.json`  
Principle: **no speculation — every finding cites evidence**.

## Inventory

- Python files: **253**
- Syntax errors: **0**
- Dependency manifests: `['backend/requirements.txt', 'backend/pyproject.toml', 'package.json', 'enterprise-web/package.json']`
- Missing expected doc aliases: `[]`  
  Note: project uses numbered docs; aliases differ by name, content exists.
- Duplicate basename sample size: **11**
- Top folders: `{"backend": 216, "docs": 118, "datasets": 109, "enterprise-web": 71, "src": 32, "ai-brain": 19, "integrations": 17, "tests": 15, "data": 12, "knowledge": 7, "models": 7, "public": 5, "scripts": 5, "routing": 3, "vector_store": 2, "automation": 2, "forecast": 2, "frontend": 2, ".gitignore": 1, "next-env.d.ts": 1, "tsconfig.tsbuildinfo": 1, ".env.example": 1, "docker-compose.yml": 1, "package-lock.json": 1, "package.json": 1, "eslint.config.mjs": 1, "tsconfig.json": 1, "ecosystem.config.cjs": 1, "`
- Extensions: `{".py": 253, ".md": 135, ".json": 74, ".tsx": 56, ".jsonl": 53, ".ts": 31, "(none)": 13, ".csv": 10, ".mjs": 5, ".svg": 5, ".txt": 3, ".tsbuildinfo": 2, ".example": 2, ".local": 2, ".ico": 2, ".css": 2, ".woff": 2, ".wav": 2, ".yml": 1, ".cjs": 1, ".toml": 1, ".ini": 1, ".sql": 1, ".log": 1, ".mako": 1, ".mdc": 1}`

## Module presence

| Module | Present |
|--------|---------|
| `audio_engine` | YES |
| `audio_pipeline` | YES |
| `audio_repair` | YES |
| `speaker` | YES |
| `transcript` | YES |
| `pragmatics` | YES |
| `memory_graph` | YES |
| `self_learning` | YES |
| `digital_twin` | YES |
| `negotiation` | YES |
| `cltv` | YES |
| `war_room` | YES |
| `autonomous` | YES |
| `sales_os` | YES |
| `ai-brain/rulebook` | YES |
| `ai-brain/vcie` | YES |
| `enterprise-web` | YES |
| `integrations` | YES |

## Findings

| ID | Severity | Status | File | Cause |
|----|----------|--------|------|-------|
| ISS-001 | High | Fixed | ai-brain/rulebook | Dual rulebook schemas |
| ISS-011 | Low | Planned | duplicate basenames | Naming drift |

## Dead code / unused libs

- Tiny/empty Python files sample: `['backend/app/core/__init__.py', 'backend/app/domain/__init__.py', 'backend/app/infrastructure/__init__.py', 'backend/app/interfaces/__init__.py', 'backend/app/application/__init__.py', 'backend/app/infrastructure/redis/__init__.py', 'backend/app/infrastructure/s3/__init__.py', 'backend/app/infrastructure/repositories/__init__.py', 'backend/app/interfaces/api/__init__.py', 'backend/app/application/services/__init__.py']`
- Full unused-library proof requires `vulture`/`deptry` in CI → **Planned**, not claimed clean.

## Broken imports

- Import smoke (`reports/audit/phase_pipeline_imports.json`): core AI/audio modules import OK where reported ok.
- Mutual import pairs: `[['audio_engine', 'audio_pipeline'], ['audio_engine', 'audio_repair'], ['approval', 'self_learning'], ['research', 'self_learning']]`

## Repository Health Score: **78/100**
