#!/usr/bin/env bash
# Cài PC Linux làm LAN server băng thông cao (control plane).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BIND_HOST="${CONTROL_HOST:-0.0.0.0}"
PORT="${CONTROL_PORT:-8088}"
TOKEN="${CONTROL_TOKEN:-}"
MAX_UPLOAD_MB="${CONTROL_MAX_UPLOAD_MB:-0}"

if [[ -z "$TOKEN" ]]; then
  TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(12))
PY
)"
fi

echo "=== Cài FbPoller LAN Server (Linux) ==="
echo "Root = $ROOT"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip wheel
pip install -e ".[control]"

DATA_DIR="${CONTROL_DATA_DIR:-$ROOT/control_data}"
PKG_DIR="${CONTROL_PACKAGES_DIR:-$DATA_DIR/packages}"
mkdir -p "$DATA_DIR" "$PKG_DIR" "$ROOT/logs" "$DATA_DIR/sync"

cat > "$DATA_DIR/server.env" <<EOF
CONTROL_HOST=$BIND_HOST
CONTROL_PORT=$PORT
CONTROL_TOKEN=$TOKEN
CONTROL_DATA_DIR=$DATA_DIR
CONTROL_PACKAGES_DIR=$PKG_DIR
CONTROL_MAX_UPLOAD_MB=$MAX_UPLOAD_MB
CONTROL_UVICORN_WORKERS=2
CONTROL_LIMIT_CONCURRENCY=200
CONTROL_BACKLOG=2048
CONTROL_KEEPALIVE=75
EOF

cat > "$ROOT/scripts/fb-server-ctl.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/control_data/server.env"
PID_FILE="$ROOT/logs/server.pid"
OUT="$ROOT/logs/server.out"

load_env() {
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

cmd="${1:-}"
case "$cmd" in
  start)
    load_env
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "already running pid=$(cat "$PID_FILE")"
      exit 0
    fi
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
    nohup python -m control_plane >>"$OUT" 2>&1 &
    echo $! >"$PID_FILE"
    echo "started pid=$(cat "$PID_FILE") http://0.0.0.0:${CONTROL_PORT}/"
    ;;
  stop)
    if [[ -f "$PID_FILE" ]]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
    fi
    echo "stopped"
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "running pid=$(cat "$PID_FILE")"
    else
      echo "not running"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status}"
    exit 1
    ;;
esac
EOF
chmod +x "$ROOT/scripts/fb-server-ctl.sh" "$ROOT/scripts/install-server.sh"

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
LAN_IP="${LAN_IP:-IP_MAY_NAY}"

echo ""
echo "Server URL : http://${LAN_IP}:${PORT}/"
echo "Token      : ${TOKEN}"
echo "Packages   : ${PKG_DIR}"
echo ""
echo "Start:  ./scripts/fb-server-ctl.sh start"
echo "Upload zip:"
echo "  curl -H \"Authorization: Bearer ${TOKEN}\" -F file=@fb-poller.zip http://${LAN_IP}:${PORT}/v1/updates/packages/upload"
echo ""
echo "Agent trên PC scanner:"
echo "  Install-Agent.ps1 -ControlUrl \"http://${LAN_IP}:${PORT}\" -ControlToken \"${TOKEN}\" -StartNow"

./scripts/fb-server-ctl.sh start
