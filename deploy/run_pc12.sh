#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
if [[ -f deploy/env.pc12 ]]; then
  set -a; source deploy/env.pc12; set +a
elif [[ -f .env ]]; then
  set -a; source .env; set +a
fi
exec fb-poller run
