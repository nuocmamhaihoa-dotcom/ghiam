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

if ! command -v tesseract >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y tesseract-ocr tesseract-ocr-vie ffmpeg
fi
# Bộ chữ lớn đọc số và dấu tiếng Việt đúng hơn bộ chữ đi kèm Ubuntu.
mkdir -p "$APP_DIR/tessdata_best"
for lang in eng vie; do
  if [[ ! -s "$APP_DIR/tessdata_best/$lang.traineddata" ]]; then
    if curl -fsSL -m 300 -o "$APP_DIR/tessdata_best/$lang.traineddata.part" \
      "https://github.com/tesseract-ocr/tessdata_best/raw/main/$lang.traineddata"; then
      mv "$APP_DIR/tessdata_best/$lang.traineddata.part" "$APP_DIR/tessdata_best/$lang.traineddata"
    else
      rm -f "$APP_DIR/tessdata_best/$lang.traineddata.part"
      echo "Không tải được bộ chữ lớn $lang. Bộ đọc sẽ dùng bộ chữ đi kèm Ubuntu."
    fi
  fi
done

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
CONTROL_MAX_UPLOAD_MB=2048
CONTROL_VIDEO_DISK_GB=120
CONTROL_VIDEO_KEEP=8
CONTROL_VIDEO_KEEP_GB=12
CONTROL_VIDEO_FREE_RESERVE_GB=20
CONTROL_VIDEO_OCR_PAUSE_FREE_GB=25
CONTROL_VIDEO_OCR_PAUSE_UPLOADS=6
CONTROL_VIDEO_UPLOAD_SLOTS=64
CONTROL_VIDEO_FEEDERS=1
CONTROL_UVICORN_WORKERS=1
CONTROL_LIMIT_CONCURRENCY=400
CONTROL_BACKLOG=4096
CONTROL_KEEPALIVE=120
CONTROL_TIMEOUT_KEEPALIVE=120
CONTROL_PROXY_CHECK_SEC=0
CONTROL_PROXY_CHECK_CONCURRENCY=40
CONTROL_PROXY_CHECK_TIMEOUT=8
EOF
chmod 600 "$APP_DIR/control_data/server.env"

# Cửa sổ TCP 16MB + 8GB dirty cache (máy 31GB RAM): ACK chunk từ RAM, đĩa bắt kịp sau.
cat > /etc/sysctl.d/99-fb-poller-upload.conf <<'SYS'
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 4096
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_window_scaling = 1
vm.dirty_background_bytes = 268435456
vm.dirty_bytes = 8589934592
vm.dirty_expire_centisecs = 3000
vm.swappiness = 10
SYS
modprobe tcp_bbr 2>/dev/null || true
mkdir -p /etc/modules-load.d
echo tcp_bbr > /etc/modules-load.d/fb-poller-bbr.conf
if sysctl net.ipv4.tcp_available_congestion_control 2>/dev/null | grep -q bbr; then
  printf '\nnet.core.default_qdisc = fq\nnet.ipv4.tcp_congestion_control = bbr\n' >> /etc/sysctl.d/99-fb-poller-upload.conf
fi
sysctl --system >/dev/null 2>&1 || sysctl -p /etc/sysctl.d/99-fb-poller-upload.conf || true
IFACE="$(ip -o route show to default | awk '{print $5; exit}')"
if [ -n "${IFACE}" ]; then
  tc qdisc replace dev "${IFACE}" root fq 2>/dev/null || true
fi
# Cổng 80 vào thẳng hub (không proxy) — iPhone mở http://IP/ hoặc http://IP:8088.
if command -v iptables >/dev/null 2>&1; then
  iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports "${PORT}" 2>/dev/null || \
    iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports "${PORT}"
fi
chmod 755 "$APP_DIR/deploy/vps/tune-upload-net.sh"
cp -f "$APP_DIR/deploy/vps/fb-poller-net.service" /etc/systemd/system/fb-poller-net.service
systemctl daemon-reload
systemctl enable fb-poller-net.service >/dev/null 2>&1 || true

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
