#!/usr/bin/env bash
# Installs a current Chrome inside an Ubuntu 24.04 root, then serves it
# through noVNC. The host is Ubuntu 18.04 and cannot run current Chrome.
# No TikTok password is read or stored by this installer.
set -euo pipefail

APP_DIR=/opt/tiktok-osint
ROOT="$APP_DIR/browser-root"
BASE_URL="https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.3-base-amd64.tar.gz"
CHROME_URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq xvfb x11vnc openbox fonts-liberation ca-certificates curl scrot

"$APP_DIR/.venv/bin/pip" install 'websockify==0.12.0'

if [[ ! -x "$ROOT/opt/google/chrome/chrome" ]]; then
  tmp="$(mktemp -d)"
  curl -fL "$BASE_URL" -o "$tmp/ubuntu-base.tar.gz"
  rm -rf "$ROOT"
  mkdir -p "$ROOT"
  tar -xzf "$tmp/ubuntu-base.tar.gz" -C "$ROOT"
  rm -rf "$tmp"

  cat >"$ROOT/etc/apt/sources.list" <<'EOF'
deb http://archive.ubuntu.com/ubuntu noble main universe
deb http://archive.ubuntu.com/ubuntu noble-updates main universe
deb http://security.ubuntu.com/ubuntu noble-security main universe
EOF
  printf '#!/bin/sh\nexit 101\n' >"$ROOT/usr/sbin/policy-rc.d"
  chmod 755 "$ROOT/usr/sbin/policy-rc.d"
  mkdir -p "$ROOT/tmp" "$ROOT/proc" "$ROOT/dev" "$ROOT/sys" "$ROOT/dev/shm"
  chmod 1777 "$ROOT/tmp"
  if ! grep -q '^tiktok:' "$ROOT/etc/passwd"; then
    echo 'tiktok:x:999:999::/chrome-profile:/usr/sbin/nologin' >>"$ROOT/etc/passwd"
  fi
  if ! grep -q '^tiktok:' "$ROOT/etc/group"; then
    echo 'tiktok:x:999:' >>"$ROOT/etc/group"
  fi
  if [[ ! -s "$ROOT/etc/machine-id" ]]; then
    cat /proc/sys/kernel/random/uuid | tr -d '-' >"$ROOT/etc/machine-id"
  fi
  cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
  mountpoint -q "$ROOT/proc" || mount -t proc proc "$ROOT/proc"
  mountpoint -q "$ROOT/sys" || mount --bind /sys "$ROOT/sys"
  mountpoint -q "$ROOT/dev" || mount --bind /dev "$ROOT/dev"
  mountpoint -q "$ROOT/dev/shm" || mount --bind /dev/shm "$ROOT/dev/shm"

  chroot "$ROOT" apt-get update
  curl -fL "$CHROME_URL" -o "$ROOT/tmp/chrome.deb"
  chroot "$ROOT" apt-get install -y /tmp/chrome.deb fonts-liberation ca-certificates
  rm -f "$ROOT/tmp/chrome.deb"
fi

if [[ ! -f "$APP_DIR/novnc/vnc.html" ]]; then
  tmp="$(mktemp -d)"
  curl -fL "https://github.com/novnc/noVNC/archive/refs/tags/v1.4.0.tar.gz" -o "$tmp/novnc.tar.gz"
  tar -xzf "$tmp/novnc.tar.gz" -C "$tmp"
  rm -rf "$APP_DIR/novnc"
  mv "$tmp"/noVNC-1.4.0 "$APP_DIR/novnc"
  rm -rf "$tmp"
fi

install -d -o tiktok -g tiktok -m 700 "$APP_DIR/data/chrome-profile" "$APP_DIR/data/phone-logs"
chmod 755 "$APP_DIR/deploy/vps/tiktok-phone.sh" "$APP_DIR/deploy/vps/install-tiktok-phone.sh"

install -m 644 "$APP_DIR/deploy/vps/tiktok-phone.service" /etc/systemd/system/tiktok-phone.service
install -m 644 "$APP_DIR/deploy/vps/nginx-tiktok-osint.conf" /etc/nginx/sites-available/tiktok-osint
nginx -t
systemctl daemon-reload
systemctl enable tiktok-phone.service
systemctl restart tiktok-phone.service
systemctl reload nginx
systemctl is-active tiktok-phone.service
