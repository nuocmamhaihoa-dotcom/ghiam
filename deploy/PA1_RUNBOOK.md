# PA1 runbook (no Graph API)

Engine: **browser-light only**. Hot = 100 posts, start **45s**, goal **30s** after KPI gate. No nested replies in Hot.

## One-time setup (each PC)

```bash
git pull
./scripts/setup_machine.sh
cp deploy/env.pc20.example deploy/env.pc20   # or env.pc12
# edit WORKERS / DATABASE_URL / proxy paths
```

Fill:
- `data/posts.txt` — 300 permalinks
- `data/proxies_static.txt` — sticky proxies (prefer ~20+ for workers)

```bash
fb-poller import-urls data/posts.txt
fb-poller import-proxies
fb-poller rebalance-hot
fb-poller probe --limit 10 --workers 2
fb-poller status
```

## Start

```bash
# PC20
./deploy/run_pc20.sh

# PC12
./deploy/run_pc12.sh
```

## Rollout gates

| Stage | Config | Pass when |
|---|---|---|
| Phase 2 | `WORKERS=6`, Hot 30 @ 60s | success ≥85%, p95 &lt; 8s |
| Phase 3 | `WORKERS=20` total, Hot 100 @ **45s** | success ≥85%, 2h soak |
| Phase 4 | `HOT_INTERVAL_SEC=30` | `fb-poller kpi` → `ready_for_30s=true` |

```bash
fb-poller kpi
```

## Dual-PC note

- Same `DATABASE_URL` (Postgres recommended) so `claim_due` avoids double work.
- Split sticky proxies: PC20 gets 14, PC12 gets 6 (`WORKER_ID` khác nhau).
- Do **not** point both machines at different SQLite files.

## Out of scope for PA1

- Graph API / PPCA
- Nested reply expansion in Hot
- HTTP/GraphQL ultra path
