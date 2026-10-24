#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
playwright install chromium
playwright install-deps chromium || true

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — edit WORKERS / WORKER_ID"
fi

mkdir -p data
touch data/posts.txt
fb-poller init-db
echo "Setup done. Next: fill data/posts.txt (quét đi IP máy), then:"
echo "  fb-poller import-urls data/posts.txt"
echo "  fb-poller rebalance-hot"
echo "  fb-poller run"
