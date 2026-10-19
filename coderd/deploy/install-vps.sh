#!/usr/bin/env bash
# Chạy trên VPS Ubuntu khi Docker đã có và DNS đã trỏ về máy.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  cp .env.example .env
  python3 - <<'PY'
from pathlib import Path
import secrets
text = Path(".env").read_text()
text = text.replace("AUTH_TOKEN=", "AUTH_TOKEN=" + secrets.token_urlsafe(24), 1)
text = text.replace("WORKER_TOKEN=", "WORKER_TOKEN=" + secrets.token_urlsafe(24), 1)
Path(".env").write_text(text)
PY
fi
chmod 600 .env
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
fi
docker compose up -d --build
docker compose ps
