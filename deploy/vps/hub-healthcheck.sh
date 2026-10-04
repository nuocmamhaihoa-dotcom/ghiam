#!/usr/bin/env bash
# Kiểm tra hub trên máy. Ba lần im liên tiếp thì systemd kéo lại dịch vụ.
set -euo pipefail

ENV_FILE="${HUB_ENV:-/opt/fb-poller/control_data/server.env}"
PORT=8088
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  PORT="${CONTROL_PORT:-8088}"
fi

STAMP="${HUB_HEALTH_STAMP:-/run/fb-poller-hub.misses}"
if curl -fsS -m 5 "http://127.0.0.1:${PORT}/health" >/dev/null; then
  : > "$STAMP"
  exit 0
fi

count=0
if [[ -f "$STAMP" ]]; then
  count="$(tr -cd '0-9' < "$STAMP" || true)"
  count="${count:-0}"
fi
count=$((count + 1))
printf '%s\n' "$count" > "$STAMP"
if [[ "$count" -ge 3 ]]; then
  systemctl restart fb-poller-hub
  : > "$STAMP"
fi
