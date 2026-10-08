#!/usr/bin/env bash
# Cài fb-poller hub trên VPS (Ubuntu). Chạy với root.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/fb-poller}"
PORT="${CONTROL_PORT:-8088}"
TOKEN="${CONTROL_TOKEN:-}"
BIND="${CONTROL_HOST:-0.0.0.0}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR missing: $APP_DIR (rsync/clone code first)"
  exit 1
fi

export PATH="${HOME}/.local/bin:${PATH}"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
if [[ -f "${HOME}/.local/bin/env" ]]; then
  # shellcheck disable=SC1090
  source "${HOME}/.local/bin/env"
fi
export PATH="${HOME}/.local/bin:${PATH}"

uv python install 3.12
cd "$APP_DIR"
uv venv --python 3.12 .venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e ".[control]"

mkdir -p "$APP_DIR/control_data/packages" "$APP_DIR/control_data/sync" "$APP_DIR/logs"

if [[ -z "$TOKEN" ]]; then
  if [[ -f "$APP_DIR/control_data/server.env" ]]; then
    # keep existing token
    # shellcheck disable=SC1090
    source "$APP_DIR/control_data/server.env"
    TOKEN="${CONTROL_TOKEN:-}"
  fi
fi
if [[ -z "${TOKEN:-}" ]]; then
  TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi

# Seed static proxies for live/die monitoring
if [[ -f "$APP_DIR/deploy/vps/proxies_static.txt" ]]; then
  cp -f "$APP_DIR/deploy/vps/proxies_static.txt" "$APP_DIR/control_data/proxies_static.txt"
elif [[ -f "$APP_DIR/data/proxies_static.txt" ]]; then
  grep -v '^#' "$APP_DIR/data/proxies_static.txt" | grep -v '^$' > "$APP_DIR/control_data/proxies_static.txt" || true
fi

cat > "$APP_DIR/control_data/server.env" <<EOF
CONTROL_HOST=$BIND
CONTROL_PORT=$PORT
CONTROL_TOKEN=$TOKEN
CONTROL_DATA_DIR=$APP_DIR/control_data
CONTROL_PACKAGES_DIR=$APP_DIR/control_data/packages
CONTROL_DB=$APP_DIR/control_data/server.db
CONTROL_PROXIES_FILE=$APP_DIR/control_data/proxies_static.txt
CONTROL_MAX_UPLOAD_MB=4096
CONTROL_VIDEO_DISK_GB=80
CONTROL_UVICORN_WORKERS=1
CONTROL_LIMIT_CONCURRENCY=200
CONTROL_BACKLOG=2048
CONTROL_KEEPALIVE=75
CONTROL_PROXY_CHECK_SEC=300
CONTROL_PROXY_CHECK_CONCURRENCY=40
CONTROL_PROXY_CHECK_TIMEOUT=8
EOF
chmod 600 "$APP_DIR/control_data/server.env"

cp -f "$APP_DIR/deploy/vps/fb-poller-hub.service" /etc/systemd/system/fb-poller-hub.service
cp -f "$APP_DIR/deploy/vps/fb-poller-video.service" /etc/systemd/system/fb-poller-video.service
systemctl daemon-reload
systemctl enable fb-poller-hub fb-poller-video
systemctl restart fb-poller-hub fb-poller-video

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PORT}/tcp" || true
  ufw allow OpenSSH || true
  # Do not force-enable ufw if admin left it inactive
fi

sleep 2
systemctl --no-pager --full status fb-poller-hub | head -25 || true
curl -sS "http://127.0.0.1:${PORT}/health" || true
echo ""
echo "=== DONE ==="
echo "Dashboard : http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):${PORT}/"
echo "Health    : http://IP:${PORT}/health"
echo "Token     : ${TOKEN}"
echo "Agent PC  : Install-Agent.ps1 -ControlUrl \"http://IP:${PORT}\" -ControlToken \"${TOKEN}\" -StartNow"
