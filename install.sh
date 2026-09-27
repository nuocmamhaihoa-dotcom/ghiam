#!/usr/bin/env bash
# Cài fb-poller PA1 trên PC Linux — dùng CPU/RAM/proxy máy local để quét comment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[install]${NC} $*"; }
warn() { echo -e "${YELLOW}[install]${NC} $*"; }
err()  { echo -e "${RED}[install]${NC} $*"; }

PROFILE="${1:-auto}"   # auto | pc20 | pc12 | custom
NONINTERACTIVE="${NONINTERACTIVE:-0}"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "Thiếu lệnh: $1"
    return 1
  fi
}

detect_profile() {
  local cores ram_gb
  cores="$(nproc 2>/dev/null || echo 4)"
  ram_gb="$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 8)"
  if [[ "$cores" -ge 16 && "$ram_gb" -ge 48 ]]; then
    echo "pc20"
  elif [[ "$cores" -ge 8 && "$ram_gb" -ge 24 ]]; then
    echo "pc12"
  else
    echo "custom"
  fi
}

suggest_workers() {
  local cores="$1"
  # ~0.7 browser / core, cap 14 for safety on Facebook pages
  local w=$(( cores * 7 / 10 ))
  if [[ "$w" -lt 1 ]]; then w=1; fi
  if [[ "$w" -gt 14 ]]; then w=14; fi
  echo "$w"
}

install_system_deps() {
  info "Cài dependency hệ thống (cần sudo nếu thiếu gói)..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      python3 python3-venv python3-pip \
      ca-certificates curl git \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
      fonts-liberation xvfb >/dev/null || \
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      python3 python3-venv python3-pip ca-certificates curl git \
      libnss3 libatk-bridge2.0-0 libgbm1 libasound2 fonts-liberation xvfb || true
  else
    warn "Không phải apt — bỏ qua cài gói hệ thống. Đảm bảo Python 3.11+ đã có."
  fi
}

install_python_app() {
  need_cmd python3
  info "Tạo virtualenv + cài fb-poller..."
  python3 -m venv "$ROOT/.venv"
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  pip install -U pip wheel >/dev/null
  pip install -e "$ROOT"
  info "Cài Playwright Chromium..."
  playwright install chromium
  playwright install-deps chromium 2>/dev/null || true
}

write_env() {
  local profile="$1"
  local cores ram_gb workers worker_id
  cores="$(nproc 2>/dev/null || echo 4)"
  ram_gb="$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 8)"
  workers="$(suggest_workers "$cores")"
  worker_id="$(hostname)-1"

  mkdir -p "$ROOT/data" "$ROOT/data/metrics" "$ROOT/deploy" "$ROOT/logs"
  touch "$ROOT/data/posts.txt" "$ROOT/data/proxies_static.txt" "$ROOT/data/proxies_4g.txt"

  case "$profile" in
    pc20)
      workers=14
      worker_id="pc20-1"
      cp "$ROOT/deploy/env.pc20.example" "$ROOT/deploy/env.pc20"
      ENV_FILE="$ROOT/deploy/env.pc20"
      ;;
    pc12)
      workers=6
      worker_id="pc12-1"
      cp "$ROOT/deploy/env.pc12.example" "$ROOT/deploy/env.pc12"
      ENV_FILE="$ROOT/deploy/env.pc12"
      ;;
    *)
      ENV_FILE="$ROOT/.env"
      cp "$ROOT/.env.example" "$ENV_FILE"
      ;;
  esac

  # Always sync root .env used by pydantic Settings
  cat > "$ROOT/.env" <<EOF
DATABASE_URL=sqlite+aiosqlite:///${ROOT}/data/fb_poller.db
WORKER_ID=${worker_id}
ROLE=all
WORKERS=${workers}
WARM_COLD_MAX_INFLIGHT=3
HOT_SIZE=100
HOT_INTERVAL_SEC=45
WARM_INTERVAL_SEC=150
COLD_INTERVAL_SEC=600
BOOST_ON_NEW_SEC=1200
HOT_JOB_TIMEOUT_MS=7000
HOT_MAX_COMMENTS_PAGE=20
BROWSER_RESTART_EVERY_JOBS=80
HEADLESS=true
NAVIGATION_TIMEOUT_MS=15000
PROXIES_STATIC_FILE=${ROOT}/data/proxies_static.txt
PROXIES_4G_FILE=${ROOT}/data/proxies_4g.txt
DATA_DIR=${ROOT}/data
EOF

  if [[ "$profile" == "pc20" || "$profile" == "pc12" ]]; then
    cp "$ROOT/.env" "$ENV_FILE"
  fi

  info "Phát hiện: ${cores} CPU, ~${ram_gb}GB RAM → WORKERS=${workers}, WORKER_ID=${worker_id}"
  info "File cấu hình: $ROOT/.env"
}

init_app_db() {
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  fb-poller init-db
}

install_ctl_link() {
  mkdir -p "$ROOT/bin"
  ln -sfn "$ROOT/scripts/fb-poller-ctl.sh" "$ROOT/bin/fb-poller-ctl"
  chmod +x "$ROOT/scripts/fb-poller-ctl.sh" "$ROOT/install.sh" || true
}

main() {
  info "=== Cài fb-poller PA1 trên PC ==="
  if [[ "$PROFILE" == "auto" ]]; then
    PROFILE="$(detect_profile)"
    info "Tự chọn profile: $PROFILE"
  fi

  install_system_deps
  install_python_app
  write_env "$PROFILE"
  init_app_db
  install_ctl_link

  echo
  info "Cài xong."
  echo
  echo "Bước tiếp theo:"
  echo "  1) Dán URL bài vào:     $ROOT/data/posts.txt"
  echo "  2) Dán proxy tĩnh vào:  $ROOT/data/proxies_static.txt"
  echo "  3) Import dữ liệu:"
  echo "       source .venv/bin/activate"
  echo "       fb-poller import-urls data/posts.txt"
  echo "       fb-poller import-proxies"
  echo "       fb-poller rebalance-hot"
  echo "  4) Chạy bằng tài nguyên PC:"
  echo "       ./scripts/fb-poller-ctl.sh start"
  echo "       ./scripts/fb-poller-ctl.sh status"
  echo "  5) (Tuỳ chọn) cài service tự chạy khi bật máy:"
  echo "       ./scripts/fb-poller-ctl.sh install-service"
  echo
  echo "Xem hướng dẫn: docs/CAI_DAT_PC.md"
}

main "$@"
