#!/usr/bin/env bash
# Cài API, worker và dashboard tĩnh của bộ quét hồ sơ TikTok công khai.
# Chạy bằng root sau khi mã nguồn đã ở /opt/tiktok-osint.
set -euo pipefail

APP_DIR=/opt/tiktok-osint
UV_PY=/root/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu
PY=/opt/python312/bin/python3.12

if [[ ! -x "$UV_PY/bin/python3.12" ]]; then
  echo "Thiếu Python 3.12. Cài bằng: uv python install 3.12" >&2
  exit 1
fi
if [[ ! -x "$PY" || -L /opt/python312 ]]; then
  rm -rf /opt/python312
  cp -aL "$UV_PY" /opt/python312
fi
chmod -R a+rX /opt/python312
if [[ ! -f "$APP_DIR/pyproject.toml" || ! -f "$APP_DIR/web/out/index.html" ]]; then
  echo "Thiếu mã nguồn hoặc bản build web/out trong $APP_DIR" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx redis-server apache2-utils ca-certificates

if ! id tiktok >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin tiktok
fi

if [[ ! -f /swapfile-tiktok ]]; then
  fallocate -l 2G /swapfile-tiktok || dd if=/dev/zero of=/swapfile-tiktok bs=1M count=2048
  chmod 600 /swapfile-tiktok
  mkswap /swapfile-tiktok
  swapon /swapfile-tiktok || true
  grep -q '/swapfile-tiktok' /etc/fstab || echo '/swapfile-tiktok none swap sw 0 0' >> /etc/fstab
fi

chmod -R a+rX /root/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu
"$PY" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR[tiktok]"

install -d -o tiktok -g tiktok "$APP_DIR/data" "$APP_DIR/data/exports" "$APP_DIR/.playwright"
export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.playwright"
if "$APP_DIR/.venv/bin/python" -c "import playwright" \
  && "$APP_DIR/.venv/bin/playwright" install chromium \
  && "$APP_DIR/.venv/bin/playwright" install-deps chromium; then
  echo "Playwright Chromium đã cài"
else
  echo "Playwright chưa chạy được trên hệ điều hành này. API vẫn phục vụ; worker sẽ lỗi khi mở trình duyệt." >&2
fi
chown -R tiktok:tiktok "$APP_DIR/data" "$APP_DIR/.playwright" "$APP_DIR/.venv"

cat > "$APP_DIR/tiktok.env" <<EOF
TIKTOK_DATABASE_URL=sqlite:////opt/tiktok-osint/data/tiktok_osint.db
TIKTOK_REDIS_URL=redis://127.0.0.1:6379/1
TIKTOK_HEADLESS=true
TIKTOK_DATA_DIR=/opt/tiktok-osint/data
TIKTOK_EXPORT_DIR=/opt/tiktok-osint/data/exports
PLAYWRIGHT_BROWSERS_PATH=/opt/tiktok-osint/.playwright
EOF
chown tiktok:tiktok "$APP_DIR/tiktok.env"
chmod 640 "$APP_DIR/tiktok.env"

if [[ ! -f /etc/nginx/tiktok-osint.htpasswd ]]; then
  ACCESS_PASSWORD="$(openssl rand -base64 18)"
  htpasswd -bc /etc/nginx/tiktok-osint.htpasswd admin "$ACCESS_PASSWORD"
  printf '%s\n' "$ACCESS_PASSWORD" > "$APP_DIR/access-password.txt"
  chown root:tiktok "$APP_DIR/access-password.txt"
  chmod 640 "$APP_DIR/access-password.txt"
fi

install -m 644 "$APP_DIR/deploy/vps/tiktok-osint-api.service" /etc/systemd/system/tiktok-osint-api.service
install -m 644 "$APP_DIR/deploy/vps/tiktok-osint-worker.service" /etc/systemd/system/tiktok-osint-worker.service
install -m 644 "$APP_DIR/deploy/vps/nginx-tiktok-osint.conf" /etc/nginx/sites-available/tiktok-osint
ln -sfn /etc/nginx/sites-available/tiktok-osint /etc/nginx/sites-enabled/tiktok-osint
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable --now redis-server
systemctl enable --now tiktok-osint-api.service
systemctl enable --now tiktok-osint-worker.service
nginx -t
systemctl enable --now nginx
systemctl reload nginx

ufw allow 80/tcp || true

echo "INSTALLED"
systemctl is-active tiktok-osint-api.service tiktok-osint-worker.service nginx redis-server
