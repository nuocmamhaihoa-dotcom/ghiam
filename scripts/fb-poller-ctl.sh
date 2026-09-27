#!/usr/bin/env bash
# Điều khiển fb-poller trên PC: start/stop/status/logs/install-service
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PID_FILE="$ROOT/logs/fb-poller.pid"
LOG_FILE="$ROOT/logs/fb-poller.out"
ENV_FILE="$ROOT/.env"
VENV_PY="$ROOT/.venv/bin/python"
VENV_FB="$ROOT/.venv/bin/fb-poller"
SERVICE_NAME="fb-poller"
SERVICE_USER_UNIT="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

mkdir -p "$ROOT/logs"

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

ensure_installed() {
  if [[ ! -x "$VENV_FB" ]]; then
    echo "Chưa cài đặt. Chạy: ./install.sh"
    exit 1
  fi
}

is_running() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

cmd_start() {
  ensure_installed
  load_env
  if is_running; then
    echo "Đang chạy sẵn (pid=$(cat "$PID_FILE"))"
    exit 0
  fi
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  echo "Khởi động PA1 workers=${WORKERS:-?} hot_interval=${HOT_INTERVAL_SEC:-45}s ..."
  nohup "$VENV_FB" run >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 1
  if is_running; then
    echo "Started pid=$(cat "$PID_FILE") — log: $LOG_FILE"
  else
    echo "Start thất bại — xem $LOG_FILE"
    exit 1
  fi
}

cmd_stop() {
  if ! is_running; then
    echo "Không có process đang chạy"
    rm -f "$PID_FILE"
    exit 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  echo "Stopping pid=$pid ..."
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "Stopped"
}

cmd_restart() {
  cmd_stop || true
  cmd_start
}

cmd_status() {
  ensure_installed
  load_env
  echo "=== Process ==="
  if is_running; then
    echo "running pid=$(cat "$PID_FILE")"
  else
    echo "stopped"
  fi
  echo
  echo "=== Config ==="
  echo "WORKER_ID=${WORKER_ID:-}"
  echo "WORKERS=${WORKERS:-}"
  echo "HOT_SIZE=${HOT_SIZE:-}"
  echo "HOT_INTERVAL_SEC=${HOT_INTERVAL_SEC:-}"
  echo "DATABASE_URL=${DATABASE_URL:-}"
  echo
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  "$VENV_FB" status || true
}

cmd_logs() {
  local lines="${1:-80}"
  if [[ ! -f "$LOG_FILE" ]]; then
    echo "Chưa có log: $LOG_FILE"
    exit 0
  fi
  tail -n "$lines" -f "$LOG_FILE"
}

cmd_import() {
  ensure_installed
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  if [[ -s "$ROOT/data/posts.txt" ]]; then
    fb-poller import-urls "$ROOT/data/posts.txt"
  else
    echo "data/posts.txt trống — hãy dán URL vào trước"
  fi
  fb-poller import-proxies
  fb-poller rebalance-hot
  fb-poller status
}

cmd_probe() {
  ensure_installed
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  fb-poller probe --limit "${1:-10}" --workers "${2:-2}"
}

cmd_doctor() {
  ensure_installed
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  fb-poller doctor
}

cmd_install_service() {
  ensure_installed
  mkdir -p "$(dirname "$SERVICE_USER_UNIT")"
  cat >"$SERVICE_USER_UNIT" <<EOF
[Unit]
Description=fb-poller PA1 Facebook comment poller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$VENV_FB run
Restart=on-failure
RestartSec=5
StandardOutput=append:$ROOT/logs/fb-poller.service.log
StandardError=append:$ROOT/logs/fb-poller.service.log

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE_NAME.service"
  echo "Đã cài user systemd service: $SERVICE_USER_UNIT"
  echo "Lệnh:"
  echo "  systemctl --user status $SERVICE_NAME"
  echo "  systemctl --user stop $SERVICE_NAME"
  echo "  journalctl --user -u $SERVICE_NAME -f"
  # allow lingering so service chạy cả khi logout (nếu được phép)
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$USER" 2>/dev/null || true
  fi
}

cmd_uninstall_service() {
  systemctl --user disable --now "$SERVICE_NAME.service" 2>/dev/null || true
  rm -f "$SERVICE_USER_UNIT"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "Đã gỡ service"
}

usage() {
  cat <<EOF
fb-poller-ctl — điều khiển quét comment trên PC

Usage:
  ./scripts/fb-poller-ctl.sh <command>

Commands:
  start             Chạy poller nền (dùng CPU/RAM máy này)
  stop              Dừng poller
  restart           Restart
  status            Trạng thái process + DB
  logs [N]          Xem log (mặc định follow)
  import            Import posts.txt + proxies + rebalance-hot
  probe [n] [w]     Probe n bài với w workers
  doctor            Kiểm tra tài nguyên PC + cấu hình
  install-service   Cài systemd user service (tự chạy)
  uninstall-service Gỡ systemd service
  help              Trợ giúp
EOF
}

cmd="${1:-help}"
shift || true
case "$cmd" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  logs) cmd_logs "${1:-80}" ;;
  import) cmd_import ;;
  probe) cmd_probe "${1:-10}" "${2:-2}" ;;
  doctor) cmd_doctor ;;
  install-service) cmd_install_service ;;
  uninstall-service) cmd_uninstall_service ;;
  help|-h|--help) usage ;;
  *) echo "Unknown: $cmd"; usage; exit 1 ;;
esac
