#!/usr/bin/env bash
# Gỡ sạch fb-poller hub khỏi VPS. Chạy với root trên máy cần xoá.
# Sau khi chạy xong máy sẵn sàng cài phần mềm khác.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/fb-poller}"
PORT="${CONTROL_PORT:-8088}"

echo "=== Gỡ fb-poller hub ==="
echo "APP_DIR=$APP_DIR PORT=$PORT"

systemctl disable --now fb-poller-hub.service 2>/dev/null || true
systemctl disable --now fb-poller-hub-health.timer 2>/dev/null || true
systemctl disable --now fb-poller-hub-health.service 2>/dev/null || true

# Tiến trình còn sót
pkill -f 'control_plane' 2>/dev/null || true
pkill -f 'uvicorn .*control_plane' 2>/dev/null || true
sleep 1

rm -f /etc/systemd/system/fb-poller-hub.service
rm -f /etc/systemd/system/fb-poller-hub-health.service
rm -f /etc/systemd/system/fb-poller-hub-health.timer
rm -f /etc/systemd/system/multi-user.target.wants/fb-poller-hub.service
rm -f /etc/systemd/system/timers.target.wants/fb-poller-hub-health.timer
systemctl daemon-reload
systemctl reset-failed fb-poller-hub.service 2>/dev/null || true
systemctl reset-failed fb-poller-hub-health.service 2>/dev/null || true

# Mã nguồn + dữ liệu hub
rm -rf "$APP_DIR"
rm -rf /var/lib/fb-poller
rm -rf /var/backups/fb-poller
rm -f /run/fb-poller-hub.misses

# Nginx/Caddy chuyển tiếp cổng 80 → hub (nếu có)
if [[ -d /etc/nginx ]]; then
  find /etc/nginx -type f \( -name '*fb-poller*' -o -name '*8088*' \) -print -delete 2>/dev/null || true
  if command -v nginx >/dev/null 2>&1; then
    nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
  fi
fi
if [[ -d /etc/caddy ]]; then
  find /etc/caddy -type f \( -name '*fb-poller*' -o -name '*8088*' \) -print -delete 2>/dev/null || true
  systemctl reload caddy 2>/dev/null || true
fi

# Không đóng SSH. Chỉ gỡ rule cổng hub nếu ufw đang bật.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi 'Status: active'; then
  ufw delete allow "${PORT}/tcp" 2>/dev/null || true
fi

echo "=== Kiểm tra ==="
systemctl is-active fb-poller-hub 2>/dev/null || echo "service: đã tắt"
ls "$APP_DIR" 2>/dev/null && echo "WARN: còn $APP_DIR" || echo "APP_DIR: đã xoá"
if curl -fsS -m 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "WARN: cổng ${PORT} vẫn trả lời — kiểm tra tiến trình khác"
  ss -tlnp 2>/dev/null | grep ":${PORT}" || true
else
  echo "cổng ${PORT}: im (hub đã gỡ)"
fi
echo "=== XONG — VPS sạch, sẵn sàng cài phần mềm khác ==="
