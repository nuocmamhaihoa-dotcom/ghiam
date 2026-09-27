#!/usr/bin/env bash
# Opens the official TikTok login page in a browser on this VPS.
# The person at the screen types their own login. This script never
# receives a TikTok password and never uploads the contact book.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "tiktok-phone.sh must run as root so it can start the display." >&2
  exit 1
fi

APP=/opt/tiktok-osint
ROOT="$APP/browser-root"
PROFILE="$APP/data/chrome-profile"
LOG_DIR="$APP/data/phone-logs"
CHROME=/opt/google/chrome/chrome
export DISPLAY=:99

install -d -o tiktok -g tiktok -m 700 "$PROFILE" "$LOG_DIR"
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

if [[ ! -x "$ROOT$CHROME" ]]; then
  echo "Chrome is not installed in $ROOT. Run install-tiktok-phone.sh." >&2
  exit 1
fi

if [[ -f /tmp/.X99-lock ]] && ! pgrep -f 'Xvfb :99' >/dev/null 2>&1; then
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
fi

Xvfb :99 -screen 0 1200x900x24 -ac +extension GLX +render -noreset >"$LOG_DIR/xvfb.log" 2>&1 &
xvfb_pid=$!
sleep 0.6
if ! kill -0 "$xvfb_pid" 2>/dev/null; then
  echo "Xvfb did not stay up. See $LOG_DIR/xvfb.log" >&2
  exit 1
fi

openbox >"$LOG_DIR/openbox.log" 2>&1 &
openbox_pid=$!

mkdir -p "$ROOT/tmp/.X11-unix" "$ROOT/chrome-profile" "$ROOT/proc" "$ROOT/dev" "$ROOT/sys" "$ROOT/dev/shm"
chmod 1777 "$ROOT/tmp"
if ! mountpoint -q "$ROOT/proc"; then mount -t proc proc "$ROOT/proc"; fi
if ! mountpoint -q "$ROOT/sys"; then mount --bind /sys "$ROOT/sys"; fi
if ! mountpoint -q "$ROOT/dev"; then mount --bind /dev "$ROOT/dev"; fi
if ! mountpoint -q "$ROOT/dev/shm"; then mount --bind /dev/shm "$ROOT/dev/shm"; fi
if ! mountpoint -q "$ROOT/tmp/.X11-unix"; then mount --bind /tmp/.X11-unix "$ROOT/tmp/.X11-unix"; fi
if ! mountpoint -q "$ROOT/chrome-profile"; then mount --bind "$PROFILE" "$ROOT/chrome-profile"; fi
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"

: >"$LOG_DIR/chrome.log"
start_chrome() {
  # HOME inside the newer system is the profile mount, not the host path.
  setsid env HOME=/chrome-profile DISPLAY=:99 chroot --userspec=999:999 "$ROOT" "$CHROME" \
    --user-data-dir=/chrome-profile \
    --window-size=1200,900 \
    --window-position=0,0 \
    --no-first-run \
    --no-default-browser-check \
    --disable-dev-shm-usage \
    --disable-gpu \
    --password-store=basic \
    --kiosk \
    "$@" \
    "https://www.tiktok.com/login" >>"$LOG_DIR/chrome.log" 2>&1 &
  echo $!
}

chrome_pid="$(start_chrome)"
sleep 3
if ! kill -0 "$chrome_pid" 2>/dev/null; then
  echo "Chrome exited. Retrying without the browser sandbox." >>"$LOG_DIR/chrome.log"
  chrome_pid="$(start_chrome --no-sandbox)"
  sleep 3
fi
if ! kill -0 "$chrome_pid" 2>/dev/null; then
  echo "Chrome did not stay up. See $LOG_DIR/chrome.log" >&2
  tail -n 40 "$LOG_DIR/chrome.log" >&2 || true
  exit 1
fi

x11vnc -display :99 -localhost -rfbport 5900 -nopw -forever -shared -xkb >"$LOG_DIR/x11vnc.log" 2>&1 &
vnc_pid=$!
sleep 0.4
if ! kill -0 "$vnc_pid" 2>/dev/null; then
  echo "x11vnc did not stay up. See $LOG_DIR/x11vnc.log" >&2
  exit 1
fi

setsid "$APP/.venv/bin/websockify" --web="$APP/novnc" 127.0.0.1:6080 127.0.0.1:5900 >"$LOG_DIR/websockify.log" 2>&1 &
ws_pid=$!
sleep 0.4
if ! kill -0 "$ws_pid" 2>/dev/null; then
  echo "websockify did not stay up. See $LOG_DIR/websockify.log" >&2
  exit 1
fi

cleanup() {
  kill -- "-$chrome_pid" "-$ws_pid" "$vnc_pid" "$openbox_pid" "$xvfb_pid" 2>/dev/null || true
  kill "$chrome_pid" "$ws_pid" "$vnc_pid" "$openbox_pid" "$xvfb_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Foreground wait. systemd restarts the screen if Chrome or the socket proxy exits.
wait -n "$chrome_pid" "$ws_pid" || true
exit 1
